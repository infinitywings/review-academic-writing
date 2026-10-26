import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dr = load("dispatch_reader")
rp, mr = dr.rp, dr.mr

FAKE_CLAUDE = """#!{python}
import json, pathlib, sys
home = pathlib.Path(__file__).resolve().parent
prompt = sys.stdin.read()
counter = home / "counter.txt"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
(home / "prompts").mkdir(exist_ok=True)
(home / "prompts" / f"{{n + 1}}.txt").write_text(prompt)
(home / "prompts" / f"{{n + 1}}.argv.json").write_text(json.dumps(sys.argv))
responses = json.loads((home / "responses.json").read_text())
result = responses[n] if n < len(responses) else "{{}}"
envelope = {{"type": "result", "subtype": "success", "is_error": False, "result": result,
            "session_id": f"sess{{n + 1:04d}}-0000-0000", "total_cost_usd": 0.1,
            "usage": {{"input_tokens": 2, "cache_creation_input_tokens": 100, "output_tokens": 50}},
            "modelUsage": {{"claude-fake-1": {{"costUSD": 0.1}}}}, "permission_denials": []}}
print(json.dumps(envelope))
"""

TEXT = "# Counting study\n\nWe report counts of agreement. Counts rose.\n\nWe define agreement later.\n"
PROFILE = {"audience": "A trained researcher new to this project"}
CASE = {
    "id": "two-windows",
    "asset_policy": "strict-prefix",
    "blocks": [
        {"id": "B1", "text": "First window text about counts.", "page": 1, "kind": "text"},
        {"id": "B2", "text": "Second window text about limits.", "page": 2, "kind": "text"},
    ],
    "windows": [["B1"], ["B2"]],
    "assets": [],
}
MASKED_PROFILE = {"task": "Read the text in order and explain it.", "memory_limit": 20, "memory_unit": "words"}
N1 = {"id": "n1", "kind": "established", "text": "Counts are reported.", "sources": ["B1"], "since": 1}
N2 = {"id": "n2", "kind": "established", "text": "Limits are reported.", "sources": ["B2"], "since": 2}


def masked_response(notes, gist="Counts.", action=("advance", None), claim_source="B1"):
    return json.dumps({
        "understanding": "A short reading.",
        "understood": [{"claim": "Something is reported.", "basis": "explicit", "anchor": {"source_id": claim_source, "quote": ""}}],
        "uncertain": [],
        "resolved": [],
        "next_action": {"kind": action[0], "target": action[1], "reason": "because"},
        "carry_memory": {"gist": gist, "notes": list(notes), "dropped": []},
        "source_recognition": {"recognized": False, "description": ""},
    })


class DispatchReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.fake = self.root / "fake-bin"
        self.fake.mkdir()
        script = self.fake / "claude"
        script.write_text(FAKE_CLAUDE.format(python=sys.executable))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.fake) + os.pathsep + self.old_path
        self.events = self.root / "events"
        self.run = self.root / "run"
        self.case_path = self.root / "case.json"
        self.case_path.write_text(json.dumps(CASE))
        self.profile_path = self.root / "profile.json"
        self.profile_path.write_text(json.dumps(MASKED_PROFILE))

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()

    def responses(self, *results):
        (self.fake / "responses.json").write_text(json.dumps(list(results)))

    def prompt(self, number):
        return (self.fake / "prompts" / f"{number}.txt").read_text()

    def main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dr.main(argv)
        return code, out.getvalue(), err.getvalue()

    def masked(self, *extra):
        return self.main(["masked", "--events", str(self.events), "--run", str(self.run), "--case", str(self.case_path), "--profile", str(self.profile_path), *extra])

    # ------------------------------------------------------------ prefix

    def test_prefix_dispatch_passes_check_observation(self):
        index = rp.build_index(TEXT)
        packet = rp.build_packet(index, PROFILE, through_block="B0002")
        packet_path = self.root / "P1.packet.json"
        rp.write_json_atomic(packet_path, packet)
        observation = {k: packet[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256")}
        observation.update({"understanding": "Counts of agreement rose.", "issues": [{"id": "R1", "category": "unavailable_prerequisite", "anchor": {"block_id": "B0002", "quote": "counts of agreement"}, "reader_question": "What is agreement?", "consequence": "The claim is unclear.", "severity": "major", "confidence": "high"}]})
        self.responses(json.dumps(observation))
        code, out, err = self.main(["prefix", "--events", str(self.events), "--packet", str(packet_path), "--label", "P1", "--model", "claude-fake-1", "--effort", "high"])
        self.assertEqual(code, 0, err)
        check = json.loads((self.events / "P1.check.json").read_text())
        self.assertEqual(check["issue_count"], 1)
        self.assertEqual(json.loads((self.events / "P1.observation.json").read_text()), observation)
        self.assertTrue(self.prompt(1).startswith("Act as the target academic reader"))
        self.assertTrue(self.prompt(1).rstrip().endswith("}"))
        argv = json.loads((self.fake / "prompts" / "1.argv.json").read_text())
        self.assertEqual(argv[1:11], ["-p", "--restricted", "--strict-mcp-config", "--tools", "", "--setting-sources", "", "--no-session-persistence", "--permission-prompts", "none"])
        self.assertEqual(argv[-4:], ["--model", "claude-fake-1", "--effort", "high"])
        self.assertIn("--output-format", argv)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("P1", "ok")])

    def test_prefix_rejection_without_budget_is_not_corrected(self):
        index = rp.build_index(TEXT)
        packet = rp.build_packet(index, PROFILE, through_block="B0002")
        packet_path = self.root / "P1.packet.json"
        rp.write_json_atomic(packet_path, packet)
        stale = {k: packet[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256")}
        stale.update({"packet_sha256": "0" * 64, "understanding": "x", "issues": []})
        self.responses(json.dumps(stale), json.dumps(stale))
        code, out, err = self.main(["prefix", "--events", str(self.events), "--packet", str(packet_path), "--label", "P1"])
        self.assertEqual(code, 1)
        self.assertTrue((self.events / "P1.invalid-reason.txt").exists())
        self.assertFalse((self.events / "P1b.prompt.txt").exists())
        self.assertIn("rejected", (self.events / "P1.invalid-reason.txt").read_text())

    # ------------------------------------------------------------ masked

    def test_masked_loop_runs_two_windows_to_completion(self):
        self.responses(masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001", "000002"])
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "ok"), ("E02", "ok")])
        self.assertEqual(summary["totals"], {"calls": 2, "seconds": summary["totals"]["seconds"], "cost_usd": 0.2})
        self.assertTrue(summary["outcome"].startswith("Run complete"))
        self.assertEqual([e["record"]["memory_size"] for e in summary["loop"]], [4, 7])
        receipt = json.loads((self.run / "records" / "000001" / "receipt.json").read_text())
        self.assertEqual(receipt["agent_id"], "sess0001-0000-0000")
        self.assertIn("E02", out)
        for suffix in ("packet.json", "prompt.txt", "cli.json", "response.raw", "response.json", "provenance.json"):
            self.assertTrue((self.events / f"E01.{suffix}").exists(), suffix)

    def test_budget_violation_is_corrected_on_the_second_attempt(self):
        long_gist = " ".join(["word"] * 30)
        self.responses(masked_response([N1], gist=long_gist), masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        reason = (self.events / "E01.invalid-reason.txt").read_text()
        self.assertIn("carry_memory has 33 words; limit is 20", reason)
        self.assertIn("E01b", reason)
        self.assertTrue((self.events / "E01.response.raw").exists())
        self.assertEqual((self.events / "E01.packet.json").read_bytes(), (self.run / "packets" / "000001.json").read_bytes())
        correction = self.prompt(2)
        self.assertIn("Budget note from the controller: a previous attempt at this same packet returned a carry_memory of 33 words, above the 20-word limit", correction)
        self.assertIn("counts the whitespace-separated words of gist plus every note's text", correction)
        self.assertIn("at or below 17 words (85 percent of the limit)", correction)
        self.assertTrue(correction.rstrip().endswith("}"))
        self.assertEqual((self.events / "E01b.prompt-source.txt").read_text().count("Budget note"), 1)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "invalid_budget"), ("E01b", "ok"), ("E02", "ok")])
        self.assertEqual(json.loads((self.events / "E01b.provenance.json").read_text())["corrects"], "E01")

    def test_parse_failure_is_corrected(self):
        self.responses("```json\n{not json", masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        self.assertFalse((self.events / "E01.response.json").exists())
        self.assertEqual((self.events / "E01.response.raw").read_text(), "```json\n{not json")
        self.assertIn("Format note from the controller", self.prompt(2))
        self.assertIn("no code fence", self.prompt(2))
        provenance = json.loads((self.events / "E01.provenance.json").read_text())
        self.assertTrue(provenance["parse_error"].startswith("JSONDecodeError"))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([r["status"] for r in summary["calls"]], ["invalid_format", "ok", "ok"])

    def test_two_corrections_cap_stops_the_run(self):
        long_gist = " ".join(["word"] * 25)
        self.responses(masked_response([N1], gist=long_gist), masked_response([N1], gist=long_gist + " more"), masked_response([N1], gist=long_gist), masked_response([N1]))
        code, out, err = self.masked()
        self.assertEqual(code, 1, err)
        self.assertEqual(sorted(p.name for p in self.events.glob("E01*.prompt.txt")), ["E01.prompt.txt", "E01b.prompt.txt", "E01c.prompt.txt"])
        self.assertIn("2 previous attempts at this same packet returned carry_memory of 28 and 29 words", self.prompt(3))
        self.assertIn("No further correction was requested", (self.events / "E01c.invalid-reason.txt").read_text())
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertIn("two corrections were requested", summary["outcome"])
        self.assertIn("Stopped after attempts E01, E01b, E01c", summary["outcome"])
        self.assertEqual(len(summary["calls"]), 3)
        self.assertFalse((self.run / "records" / "000001").exists())
        self.assertIn("Stopped", out)

    def test_three_lookups_without_new_sources_stop_the_loop(self):
        lookup = masked_response([N1], action=("lookup", "B1"))
        back = masked_response([N1])
        self.responses(lookup, back, lookup, back, lookup, back, masked_response([N1]))
        code, out, err = self.masked("--max-events", "20")
        self.assertEqual(code, 1, err)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([e["mode"] for e in summary["loop"]], ["window", "lookup"] * 3)
        self.assertEqual([e.get("new_source_ids") for e in summary["loop"] if e["mode"] == "lookup"], [[], [], []])
        self.assertIn("3 consecutive lookups brought no new source ids", summary["outcome"])
        self.assertEqual(summary["totals"]["calls"], 6)

    def test_max_events_caps_the_loop(self):
        self.responses(masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        code, out, err = self.masked("--max-events", "1")
        self.assertEqual(code, 1, err)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual(summary["totals"]["calls"], 1)
        self.assertIn("--max-events 1 reached", summary["outcome"])
        self.assertTrue((self.run / "records" / "000001").exists())

    def test_provenance_fields(self):
        self.responses(masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        self.masked("--model", "claude-fake-1")
        provenance = json.loads((self.events / "E01.provenance.json").read_text())
        self.assertEqual(provenance["label"], "E01")
        self.assertIsNone(provenance["corrects"])
        self.assertEqual(provenance["exit_code"], 0)
        self.assertEqual(provenance["session_id"], "sess0001-0000-0000")
        self.assertEqual(provenance["cost_usd"], 0.1)
        self.assertEqual(provenance["model_usage"], ["claude-fake-1"])
        self.assertEqual(provenance["permission_denials"], [])
        self.assertIsNone(provenance["parse_error"])
        self.assertEqual(provenance["packet_file"], str(self.events / "E01.packet.json"))
        self.assertEqual(provenance["packet_sha256"], mr.digest((self.events / "E01.packet.json").read_bytes()))
        self.assertEqual((provenance["input_tokens"], provenance["output_tokens"], provenance["cache_creation_input_tokens"]), (2, 50, 100))
        self.assertEqual(provenance["command"][0], str(self.fake / "claude"))
        self.assertIn("--system-prompt", provenance["command"])
        self.assertTrue(provenance["isolation"].startswith("claude -p --restricted"))
        self.assertTrue(provenance["started_at"].endswith("+00:00"))
        cli = json.loads((self.events / "E01.cli.json").read_text())
        self.assertEqual(cli["session_id"], "sess0001-0000-0000")

    # ------------------------------------------------------------ branch

    def test_branch_question_from_a_recorded_event(self):
        answer = json.dumps({"answer": "Counts are reported.", "support": "stated", "anchors": [{"source_id": "B1", "quote": "counts"}], "inference": "", "background_used": "", "undetermined": "", "source_recognition": {"recognized": False, "description": ""}})
        self.responses(masked_response([N1]), masked_response([N1, N2], claim_source="B2"), answer)
        self.masked()
        question = self.root / "q1.json"
        question.write_text(json.dumps({"id": "Q1", "question": "What is reported?", "selected_with_full_text": True, "reason": "check"}))
        branch_events = self.root / "branch-events"
        code, out, err = self.main(["branch", "--events", str(branch_events), "--run", str(self.run), "--from-sequence", "1", "--question", str(question)])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.run / "branches" / "000001" / "receipt.json").exists())
        self.assertTrue(self.prompt(3).startswith("You are a fresh reader with no earlier conversation. The JSON below is a branch packet"))
        summary = json.loads((branch_events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("BR01", "ok")])
        self.assertEqual(summary["branch_sequence"], 1)

    # ------------------------------------------------------------ audit

    def test_audit_writes_the_audit_file_with_auditor_filled(self):
        request = {"candidate_id": "C04", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "proposed_wording": "Counts increased.", "basis": [{"fact": "Counts rose.", "location": "B0002"}]}
        request_path = self.root / "C04.input.json"
        request_path.write_text(json.dumps(request))
        verdict = {"candidate_id": "C04", "proposed_wording": "Counts increased.", "original": request["original"], "verdict": "preserved", "checks": {k: "same" for k in dr.AUDIT_CHECKS}, "notes": "No property changed.", "auditor": {"mode": "bounded-request", "agent_id": "filled in by the controller"}}
        self.responses(json.dumps(verdict))
        out_path = self.root / "C04.audit.json"
        code, out, err = self.main(["audit", "--events", str(self.events), "--input", str(request_path), "--out", str(out_path)])
        self.assertEqual(code, 0, err)
        audit = json.loads(out_path.read_text())
        self.assertEqual(audit["auditor"], {"mode": "bounded-request", "agent_id": "claude-p-sess0001"})
        self.assertEqual(set(audit), {"candidate_id", "proposed_wording", "original", "verdict", "checks", "notes", "auditor"})
        self.assertEqual(audit["verdict"], "preserved")
        self.assertTrue(self.prompt(1).startswith("You are an independent auditor of scientific meaning."))
        argv = json.loads((self.fake / "prompts" / "1.argv.json").read_text())
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "You are the auditor described in the user's message. Return only the JSON the message asks for, with no code fence and no commentary.")
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual(summary["calls"][0]["label"], "C04")
        self.assertEqual(summary["verdict"], "preserved")

    def test_audit_with_wrong_shape_is_corrected_once(self):
        request = {"candidate_id": "C05", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "proposed_wording": "Counts increased.", "basis": []}
        request_path = self.root / "C05.input.json"
        request_path.write_text(json.dumps(request))
        good = {"candidate_id": "C05", "proposed_wording": "Counts increased.", "original": request["original"], "verdict": "changed", "checks": {k: "same" for k in dr.AUDIT_CHECKS}, "notes": "n", "auditor": {"mode": "", "agent_id": ""}}
        good["checks"]["claim_strength"] = "changed"
        bad = dict(good, verdict="maybe")
        self.responses(json.dumps(bad), json.dumps(good))
        out_path = self.root / "C05.audit.json"
        code, out, err = self.main(["audit", "--events", str(self.events), "--input", str(request_path), "--out", str(out_path)])
        self.assertEqual(code, 0, err)
        self.assertIn("verdict must be preserved, changed, or undetermined", self.prompt(2))
        self.assertEqual(json.loads(out_path.read_text())["verdict"], "changed")
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("C05", "invalid_format"), ("C05b", "ok")])
        self.assertIn("changed: claim_strength", summary["outcome"])

    # ------------------------------------------------------------ CLI

    def test_help_runs_isolated_from_another_directory(self):
        result = subprocess.run([sys.executable, "-B", "-I", str(SCRIPTS / "dispatch_reader.py"), "--help"], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("prefix", "masked", "branch", "audit"):
            self.assertIn(name, result.stdout)


if __name__ == "__main__":
    unittest.main()
