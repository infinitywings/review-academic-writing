import contextlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
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

FAKE_CODEX = """#!{python}
import json, pathlib, sys
home = pathlib.Path(__file__).resolve().parent
prompt = sys.stdin.read()
counter = home / "codex-counter.txt"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
(home / "codex-prompts").mkdir(exist_ok=True)
(home / "codex-prompts" / f"{{n + 1}}.txt").write_text(prompt)
(home / "codex-prompts" / f"{{n + 1}}.argv.json").write_text(json.dumps(sys.argv))
responses = json.loads((home / "codex-responses.json").read_text())
entry = responses[n] if n < len(responses) else {{"text": "{{}}"}}
sys.stderr.write("ERROR codex_core: failed to load skill something\\n")
print(json.dumps({{"type": "thread.started", "thread_id": f"thread{{n + 1:04d}}-aaaa"}}))
print(json.dumps({{"type": "turn.started"}}))
if entry.get("command"):
    print(json.dumps({{"type": "item.completed", "item": {{"id": "item_0", "type": "command_execution", "command": entry["command"], "exit_code": 0, "status": "completed"}}}}))
print("not json at all")
print(json.dumps({{"type": "item.completed", "item": {{"id": "item_1", "type": "agent_message", "text": entry["text"]}}}}))
print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 18390, "cached_input_tokens": 12288, "output_tokens": 9, "reasoning_output_tokens": 0}}}}))
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
CASE3 = {
    "id": "three-windows",
    "asset_policy": "strict-prefix",
    "blocks": [
        {"id": "B1", "text": "First window text about counts.", "page": 1, "kind": "text"},
        {"id": "B2", "text": "Second window text about limits.", "page": 2, "kind": "text"},
        {"id": "B3", "text": "Third window text about methods.", "page": 3, "kind": "text"},
    ],
    "windows": [["B1"], ["B2"], ["B3"]],
    "assets": [],
}
RECALL_QUESTIONS = [
    {"id": "Q1", "after_block": "B2", "question": "What does the second window report?", "options": ["limits", "weights", mr.RECALL_IDK], "answer": "limits"},
    {"id": "Q2", "after_block": "B3", "question": "What does the third window report?", "options": ["methods", "prices", mr.RECALL_IDK], "answer": "methods"},
]
RECOGNITION = {"recognized": False, "description": ""}


def recall_response(*answers):
    """answers: (question_id, choice, note_ids) triples."""
    return json.dumps({"answers": [{"question_id": q, "choice": c, "note_ids": list(n)} for q, c, n in answers], "source_recognition": RECOGNITION})
HEDGED_REQUEST = {"candidate_id": "C06", "original": [{"block_id": "B0002", "quote": "Counts may rise under these conditions."}],
                  "proposed_wording": "Counts might rise under these conditions.", "basis": []}
KEPT_MAY = {"id": "H1", "term": "may", "status": "kept", "kept_as": "might"}
KEPT_CONDITIONS = {"id": "H2", "term": "under these conditions", "status": "kept", "kept_as": "under these conditions"}
HEDGED_OCCURRENCES = [
    {"id": "H1", "term": "may", "original_index": 0, "start": 7, "end": 10, "in_context": "Counts [[may]] rise under these conditions."},
    {"id": "H2", "term": "under these conditions", "original_index": 0, "start": 16, "end": 38, "in_context": "Counts may rise [[under these conditions]]."},
]


def hedge(n, term, status, kept_as=None):
    return {"id": f"H{n}", "term": term, "status": status, "kept_as": kept_as}


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

    def main(self, argv, choose_model=True):
        if choose_model and "--model" not in argv:
            argv = [*argv, "--model", "default"]
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

    def test_inexact_anchor_is_corrected_on_the_second_attempt(self):
        bad = json.loads(masked_response([N1]))
        bad["understood"][0]["anchor"]["quote"] = "First window text about count totals."
        self.responses(json.dumps(bad), masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        reason = (self.events / "E01.invalid-reason.txt").read_text()
        self.assertIn("(anchor)", reason)
        self.assertIn("E01b", reason)
        correction = self.prompt(2)
        self.assertIn("Anchor note from the controller", correction)
        self.assertIn("copied character for character", correction)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "invalid_anchor"), ("E01b", "ok"), ("E02", "ok")])

    def test_resumed_run_after_a_rejection_uses_a_resume_label(self):
        unseen = dict(N2, sources=["B2"])  # B2 is not yet seen at event 1: the helper rejects it outright
        self.responses(masked_response([N1, unseen]), masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        code, out, err = self.masked()
        self.assertNotEqual(code, 0)
        self.assertTrue((self.events / "E01.packet.json").exists())
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        self.assertTrue((self.events / "E01r1.packet.json").exists())
        self.assertEqual((self.events / "E01r1.packet.json").read_bytes(), (self.events / "E01.packet.json").read_bytes())
        summaries = sorted(p.name for p in self.events.glob("summary*.json"))
        self.assertEqual(len(summaries), 2)
        later = [name for name in summaries if name != "summary.json"][0]
        latest = json.loads((self.events / later).read_text())
        self.assertEqual([(r["label"], r["status"]) for r in latest["calls"]], [("E01r1", "ok"), ("E02", "ok")])

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

    # ------------------------------------------------------------ masked recall events

    def three_windows(self):
        """The fake responses for the three windows of CASE3, as a list to splice recalls into."""
        self.case_path.write_text(json.dumps(CASE3))
        return [masked_response([N1]), masked_response([N1, N2], claim_source="B2"), masked_response([N1, N2], claim_source="B3")]

    def questions_file(self, questions=RECALL_QUESTIONS, name="q.json"):
        path = self.root / name
        path.write_text(json.dumps(questions))
        return path

    def summary(self, events=None):
        return json.loads(((events or self.events) / "summary.json").read_text())

    def test_recall_events_between_windows_and_at_the_end(self):
        w1, w2, w3 = self.three_windows()
        self.responses(w1, w2, recall_response(("Q1", mr.RECALL_IDK, [])), w3, recall_response(("Q2", "prices", ["n2"])))
        code, out, err = self.masked("--recall-every", "2", "--recall-questions", str(self.questions_file()))
        self.assertEqual(code, 0, err)
        summary = self.summary()
        self.assertTrue(summary["outcome"].startswith("Run complete after 3 recorded event(s)."), summary["outcome"])
        self.assertEqual([e["mode"] for e in summary["loop"]], ["window", "window", "recall", "window", "recall"])
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "ok"), ("E02", "ok"), ("R01", "ok"), ("E03", "ok"), ("R02", "ok")])
        self.assertEqual(summary["recalls"], [
            {"recall_sequence": 1, "sequence": 2, "label": "R01", "status": "ok", "question_ids": ["Q1"], "wrong": [], "idk": ["Q1"]},
            {"recall_sequence": 2, "sequence": 3, "label": "R02", "status": "ok", "question_ids": ["Q2"], "wrong": ["Q2"], "idk": []},
        ])
        self.assertEqual(summary["restarts"], 0)
        self.assertEqual(summary["totals"]["calls"], 5)
        prompt = self.prompt(3)
        self.assertIn("Counts are reported.", prompt)
        self.assertIn("Limits are reported.", prompt)
        self.assertIn("What does the second window report?", prompt)
        self.assertNotIn("What does the third window report?", prompt, "Q2 is not due until B3 has been seen")
        for block in CASE3["blocks"]:
            self.assertNotIn(block["text"], prompt)
        self.assertNotIn('"answer"', prompt)
        self.assertIn('"mode": "recall"', prompt)
        packet = json.loads((self.run / "recalls" / "000001" / "packet.json").read_text())
        self.assertEqual((packet["mode"], packet["materials"], packet["after_sequence"], packet["recall_sequence"]), ("recall", [], 2, 1))
        self.assertEqual(packet["questions"], [{"id": "Q1", "question": "What does the second window report?", "options": ["limits", "weights", mr.RECALL_IDK]}])
        self.assertTrue(all("answer" not in q for q in packet["questions"]))
        self.assertEqual(packet["carry_memory"], {"gist": "Counts.", "notes": [N1, N2]})
        self.assertEqual(json.loads((self.run / "recalls" / "000001" / "questions.json").read_text()), [RECALL_QUESTIONS[0]])
        receipt = json.loads((self.run / "recalls" / "000001" / "receipt.json").read_text())
        self.assertEqual((receipt["results"], receipt["wrong"], receipt["idk"]), ([{"question_id": "Q1", "choice": mr.RECALL_IDK, "outcome": "idk"}], [], ["Q1"]))
        self.assertEqual(receipt["event_receipt_sha256"], mr.digest((self.run / "records" / "000002" / "receipt.json").read_bytes()))
        self.assertEqual(receipt["agent_id"], "sess0003-0000-0000")
        second = json.loads((self.run / "recalls" / "000002" / "receipt.json").read_text())
        self.assertEqual((second["after_sequence"], second["wrong"], second["event_receipt_sha256"]), (3, ["Q2"], mr.digest((self.run / "records" / "000003" / "receipt.json").read_bytes())))
        run, manifest, _case, profile, state, _seq, _ = mr.load_run(self.run)
        recalls = mr.load_recalls(run, manifest, profile, state)
        self.assertEqual([(r["sequence"], r["after_sequence"], [q["id"] for q in r["questions"]]) for r in recalls], [(1, 2, ["Q1"]), (2, 3, ["Q2"])])
        self.assertEqual(json.loads((self.run / "records" / "000003" / "packet.json").read_text())["carry_memory"], {"gist": "Counts.", "notes": [N1, N2]})
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001", "000002", "000003"])
        for suffix in ("packet.json", "prompt.txt", "cli.json", "response.raw", "response.json", "provenance.json"):
            self.assertTrue((self.events / f"R01.{suffix}").exists(), suffix)
        self.assertEqual((self.events / "R01.packet.json").read_bytes(), (self.run / "recall-packets" / "000001.json").read_bytes())
        self.assertIn("R01", out)

    def test_recall_flags_must_come_together_and_questions_must_fit_the_case(self):
        w1, w2, w3 = self.three_windows()
        self.responses(w1, w2, w3)
        code, out, err = self.masked("--recall-every", "2")
        self.assertEqual(code, 2)
        error = json.loads(err)
        self.assertEqual(error["error"]["code"], "dispatch_error")
        self.assertIn("--recall-every needs --recall-questions", error["error"]["message"])
        code, out, err = self.masked("--recall-questions", str(self.questions_file()))
        self.assertEqual(code, 2)
        self.assertIn("--recall-questions needs --recall-every N", json.loads(err)["error"]["message"])
        outside = [dict(RECALL_QUESTIONS[0], after_block="B9")]
        code, out, err = self.masked("--recall-every", "2", "--recall-questions", str(self.questions_file(outside, "outside.json")))
        self.assertEqual(code, 2)
        self.assertIn("--recall-questions: recall_questions[0].after_block names a block that is not in the case: B9", json.loads(err)["error"]["message"])
        code, out, err = self.masked("--recall-every", "-1", "--recall-questions", str(self.questions_file()))
        self.assertEqual(code, 2)
        self.assertIn("--recall-every must be 0 (off) or a positive number of windows.", json.loads(err)["error"]["message"])
        self.assertFalse((self.fake / "counter.txt").exists(), "no model call was made")
        self.assertFalse((self.events / "summary.json").exists())

    def test_recall_questions_from_a_checks_notes_file(self):
        w1, w2, w3 = self.three_windows()
        plan = {"snapshot_id": "sha256:x", "checkpoint_plan": [
            {"id": "M1", "mode": "masked-sequential", "anchor": {"block_id": "B0002", "quote": "limits"}, "reason": "carry the limits", "recall_questions": RECALL_QUESTIONS},
            {"id": "P1", "mode": "fresh-prefix", "anchor": {"block_id": "B0002", "quote": "limits"}, "reason": "first use"},
        ]}
        checks = self.root / "checks.json"
        checks.write_text(json.dumps(plan))
        self.responses(w1, w2, recall_response(("Q1", "limits", ["n2"])), w3, recall_response(("Q2", "methods", [])))
        code, out, err = self.masked("--recall-every", "2", "--recall-questions", str(checks), "--recall-plan-id", "M1")
        self.assertEqual(code, 0, err)
        summary = self.summary()
        self.assertEqual([e["mode"] for e in summary["loop"]], ["window", "window", "recall", "window", "recall"])
        self.assertEqual([(r["question_ids"], r["wrong"], r["idk"]) for r in summary["recalls"]], [(["Q1"], [], []), (["Q2"], [], [])])
        self.assertEqual(json.loads((self.run / "recalls" / "000001" / "receipt.json").read_text())["results"], [{"question_id": "Q1", "choice": "limits", "outcome": "correct"}])
        self.assertEqual(dr.load_recall_questions(str(checks), None, {"B1", "B2", "B3"}), RECALL_QUESTIONS)
        for plan_id, message in (("P1", "--recall-plan-id P1: no checkpoint_plan entry with that id carries recall_questions."),
                                 ("M9", "--recall-plan-id M9: no checkpoint_plan entry with that id carries recall_questions.")):
            with self.subTest(plan_id=plan_id):
                code, out, err = self.main(["masked", "--events", str(self.root / f"events-{plan_id}"), "--run", str(self.root / f"run-{plan_id}"), "--case", str(self.case_path), "--profile", str(self.profile_path),
                                            "--recall-every", "2", "--recall-questions", str(checks), "--recall-plan-id", plan_id])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(err)["error"]["message"], message)
        with self.assertRaisesRegex(dr.DispatchError, "--recall-plan-id applies only when --recall-questions is a checks notes file."):
            dr.load_recall_questions(str(self.questions_file()), "M1", {"B1", "B2", "B3"})
        no_questions = self.root / "no-questions.json"
        no_questions.write_text(json.dumps({"checkpoint_plan": [plan["checkpoint_plan"][1]]}))
        with self.assertRaisesRegex(dr.DispatchError, "checkpoint_plan has no entry with recall_questions"):
            dr.load_recall_questions(str(no_questions), None, {"B1", "B2", "B3"})
        neither = self.root / "neither.json"
        neither.write_text(json.dumps({"summary": "x"}))
        with self.assertRaisesRegex(dr.DispatchError, "--recall-questions must be a JSON array of questions or a checks notes file with a checkpoint_plan."):
            dr.load_recall_questions(str(neither), None, {"B1", "B2", "B3"})
        self.assertEqual(dr.load_recall_questions(None, None, {"B1"}), [])
        self.assertEqual((self.fake / "counter.txt").read_text(), "5")

    def test_recall_choice_outside_the_options_is_corrected(self):
        w1, w2, w3 = self.three_windows()
        self.responses(w1, w2, recall_response(("Q1", "bounds", [])), recall_response(("Q1", "limits", ["n2"])), w3, recall_response(("Q2", mr.RECALL_IDK, [])))
        code, out, err = self.masked("--recall-every", "2", "--recall-questions", str(self.questions_file()))
        self.assertEqual(code, 0, err)
        summary = self.summary()
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "ok"), ("E02", "ok"), ("R01", "invalid_format"), ("R01b", "ok"), ("E03", "ok"), ("R02", "ok")])
        reason = (self.events / "R01.invalid-reason.txt").read_text()
        self.assertIn("R01 not accepted (format): answers[0].choice must be one of the question's options, copied verbatim.", reason)
        self.assertIn("R01b", reason)
        correction = self.prompt(4)
        self.assertIn("Format note from the controller", correction)
        self.assertIn("answers[0].choice must be one of the question's options", correction)
        self.assertFalse((self.events / "R01b.packet.json").exists(), "the correction reuses the R01 packet")
        self.assertEqual((self.events / "R01b.prompt-source.txt").read_text().count("Format note"), 1)
        provenance = json.loads((self.events / "R01b.provenance.json").read_text())
        self.assertEqual((provenance["corrects"], provenance["packet_file"]), ("R01", str(self.events / "R01.packet.json")))
        self.assertEqual([e["mode"] for e in summary["loop"]], ["window", "window", "recall", "window", "recall"])
        self.assertEqual(summary["loop"][2]["label"], "R01b")
        self.assertEqual(sorted(p.name for p in (self.run / "recalls").iterdir()), ["000001", "000002"])

    # ------------------------------------------------------------ masked --until-complete and the pid file

    def masked_at(self, run, events, *extra):
        (self.fake / "counter.txt").unlink(missing_ok=True)
        return self.main(["masked", "--events", str(events), "--run", str(run), "--case", str(self.case_path), "--profile", str(self.profile_path), *extra])

    def test_until_complete_restarts_after_exhausted_corrections(self):
        w1, w2, w3 = self.three_windows()
        self.responses(w1, "not json", "still not json", "{broken", w2, w3)
        code, out, err = self.masked("--until-complete", "--max-restarts", "2")
        self.assertEqual(code, 0, err)
        summary = self.summary()
        self.assertTrue(summary["outcome"].startswith("Run complete after 3 recorded event(s)."), summary["outcome"])
        self.assertEqual(summary["restarts"], 1)
        labels = [r["label"] for r in summary["calls"]]
        self.assertEqual(labels, ["E01", "E02", "E02b", "E02c", "E02r1", "E03"])
        self.assertEqual([r["status"] for r in summary["calls"]], ["ok", "invalid_format", "invalid_format", "invalid_format", "ok", "ok"])
        restart = [e for e in summary["loop"] if e["mode"] == "restart"]
        self.assertEqual(len(restart), 1)
        self.assertEqual(restart[0]["restart"], 1)
        self.assertIn("E02: two corrections were requested", restart[0]["after"])
        self.assertEqual([e["mode"] for e in summary["loop"]], ["window", "window", "restart", "window", "window"])
        self.assertEqual((self.events / "E02r1.packet.json").read_bytes(), (self.events / "E02.packet.json").read_bytes())
        self.assertFalse((self.run / "dispatch.pid").exists())
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001", "000002", "000003"])
        self.assertEqual(len(list(self.events.glob("summary*.json"))), 1, "one invocation, one summary")
        # Without --until-complete the same responses stop after E02c, and the pid file is gone as well.
        run2, events2 = self.root / "run2", self.root / "events2"
        code, out, err = self.masked_at(run2, events2)
        self.assertEqual(code, 1, err)
        summary = self.summary(events2)
        self.assertIn("Stopped after attempts E02, E02b, E02c", summary["outcome"])
        self.assertNotIn("--max-restarts", summary["outcome"])
        self.assertEqual((summary["restarts"], [r["label"] for r in summary["calls"]]), (0, ["E01", "E02", "E02b", "E02c"]))
        self.assertFalse((run2 / "dispatch.pid").exists())
        self.assertEqual(sorted(p.name for p in (run2 / "records").iterdir()), ["000001"])

    def test_until_complete_stops_at_max_restarts(self):
        w1, w2, w3 = self.three_windows()
        self.responses(w1, *(["not json"] * 6), w2, w3)
        code, out, err = self.masked("--until-complete", "--max-restarts", "1")
        self.assertEqual(code, 1, err)
        summary = self.summary()
        self.assertEqual(summary["restarts"], 1)
        self.assertIn("Stopped after attempts E02r1, E02r1b, E02r1c.", summary["outcome"])
        self.assertIn("Stopped after 1 automatic restart(s) (--max-restarts 1).", summary["outcome"])
        self.assertEqual([r["label"] for r in summary["calls"]], ["E01", "E02", "E02b", "E02c", "E02r1", "E02r1b", "E02r1c"])
        self.assertEqual(len([e for e in summary["loop"] if e["mode"] == "restart"]), 1)
        self.assertFalse((self.run / "dispatch.pid").exists())
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001"])
        self.assertEqual(dr.MASKED_DEFAULT_RESTARTS, 5)
        code, out, err = self.masked_at(self.root / "run-neg", self.root / "events-neg", "--until-complete", "--max-restarts", "-1")
        self.assertEqual(code, 2)
        self.assertIn("--max-restarts must be a non-negative integer.", json.loads(err)["error"]["message"])

    def test_helper_rejection_is_not_restarted(self):
        unseen = dict(N2, sources=["B2"])
        self.responses(masked_response([N1, unseen]), masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        code, out, err = self.masked("--until-complete")
        self.assertEqual(code, 1, err)
        summary = self.summary()
        self.assertEqual(summary["restarts"], 0)
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "rejected")])
        self.assertIn("the helper rejected the response and no correction applies", summary["outcome"])
        self.assertNotIn("--max-restarts", summary["outcome"])
        self.assertEqual([e["mode"] for e in summary["loop"]], ["window"])
        self.assertFalse((self.events / "E01r1.packet.json").exists())
        self.assertFalse((self.run / "dispatch.pid").exists())

    @staticmethod
    def stale_pid():
        pid = 2 ** 22 + 7
        while dr.process_alive(pid):
            pid += 1
        return pid

    def test_pid_file_refuses_a_live_holder_and_replaces_a_stale_one(self):
        self.responses(masked_response([N1]), masked_response([N1, N2], claim_source="B2"))
        mr.start(self.case_path, self.profile_path, self.run)
        pid_file = self.run / dr.PID_FILE
        self.assertEqual(pid_file.name, "dispatch.pid")
        live = os.getppid()
        self.assertTrue(dr.process_alive(live))
        pid_file.write_text(json.dumps({"pid": live, "started_at": "2026-01-01T00:00:00+00:00", "events_dir": "elsewhere"}) + "\n")
        code, out, err = self.masked()
        self.assertEqual(code, 2)
        error = json.loads(err)
        self.assertEqual(error["error"]["code"], "dispatch_error")
        self.assertIn(f"Another dispatch is driving this run (pid {live}, see {pid_file})", error["error"]["message"])
        self.assertEqual(json.loads(pid_file.read_text())["pid"], live, "the live holder's file is left in place")
        self.assertFalse((self.fake / "counter.txt").exists(), "no model call was made")
        stale = self.stale_pid()
        self.assertFalse(dr.process_alive(stale))
        pid_file.write_text(json.dumps({"pid": stale, "started_at": "2026-01-01T00:00:00+00:00", "events_dir": "elsewhere"}) + "\n")
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        self.assertTrue(self.summary()["outcome"].startswith("Run complete"))
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001", "000002"])
        self.assertFalse(pid_file.exists(), "the stale file was replaced for the run and removed at its end")
        # While a run is in progress the file names this process; claim_run itself shows the written shape.
        held = dr.claim_run(self.run, self.events)
        self.assertEqual(held, pid_file)
        holder = json.loads(pid_file.read_text())
        self.assertEqual((holder["pid"], holder["events_dir"]), (os.getpid(), str(self.events)))
        self.assertTrue(holder["started_at"].endswith("+00:00"))
        self.assertEqual(dr.claim_run(self.run, self.events), pid_file, "the current process may reclaim its own run")
        pid_file.write_text("not json\n")
        self.assertEqual(dr.claim_run(self.run, self.events), pid_file, "an unreadable pid file is replaced")
        self.assertEqual(json.loads(pid_file.read_text())["pid"], os.getpid())
        pid_file.unlink()

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
        verdict = {"candidate_id": "C04", "proposed_wording": "Counts increased.", "original": request["original"], "verdict": "preserved", "checks": {**{k: "same" for k in dr.AUDIT_CHECKS}, "hedges": []}, "notes": "No property changed.", "auditor": {"mode": "bounded-request", "agent_id": "filled in by the controller"}}
        self.responses(json.dumps(verdict))
        out_path = self.root / "C04.audit.json"
        code, out, err = self.main(["audit", "--events", str(self.events), "--input", str(request_path), "--out", str(out_path)])
        self.assertEqual(code, 0, err)
        audit = json.loads(out_path.read_text())
        self.assertEqual(audit["auditor"], {"mode": "bounded-request", "agent_id": "claude-p-sess0001"})
        self.assertEqual(set(audit), {"candidate_id", "proposed_wording", "original", "verdict", "checks", "notes", "audit_round", "hedges_in_original", "hedge_patterns", "auditor"})
        self.assertEqual(audit["audit_round"], 1)
        self.assertEqual(audit["verdict"], "preserved")
        self.assertTrue(self.prompt(1).startswith("You are an independent auditor of scientific meaning, checking a rewrite that someone else submitted."))
        argv = json.loads((self.fake / "prompts" / "1.argv.json").read_text())
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "You are the auditor described in the user's message. Return only the JSON the message asks for, with no code fence and no commentary.")
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual(summary["calls"][0]["label"], "C04")
        self.assertEqual(summary["verdict"], "preserved")

    def test_audit_with_wrong_shape_is_corrected_once(self):
        request = {"candidate_id": "C05", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "proposed_wording": "Counts increased.", "basis": []}
        request_path = self.root / "C05.input.json"
        request_path.write_text(json.dumps(request))
        good = {"candidate_id": "C05", "proposed_wording": "Counts increased.", "original": request["original"], "verdict": "changed", "checks": {**{k: "same" for k in dr.AUDIT_CHECKS}, "hedges": []}, "notes": "n", "auditor": {"mode": "", "agent_id": ""}}
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

    # ------------------------------------------------------------ audit hedges and rounds

    def hedged_request(self, name="C06.input.json", **change):
        request = {**HEDGED_REQUEST, **change}
        path = self.root / name
        path.write_text(json.dumps(request))
        return path

    def hedged_verdict(self, hedges=(), verdict="preserved", checks=None, **change):
        base = {**{k: "same" for k in dr.AUDIT_CHECKS}, **(checks or {})}
        if hedges is not None:
            base["hedges"] = list(hedges)
        return json.dumps({"candidate_id": "C06", "proposed_wording": HEDGED_REQUEST["proposed_wording"], "original": HEDGED_REQUEST["original"], "verdict": verdict,
                           "checks": base, "notes": "n", "auditor": {"mode": "", "agent_id": ""}, **change})

    def sent_request(self, number):
        return json.loads(self.prompt(number).rsplit("\n\n", 1)[1])

    def audit(self, request_path, out_name, *extra, events=None):
        return self.main(["audit", "--events", str(events or self.events), "--input", str(request_path), "--out", str(self.root / out_name), *extra])

    def test_audit_prompt_lists_the_hedges_found_in_the_original(self):
        self.responses(self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]))
        code, out, err = self.audit(self.hedged_request(), "C06.audit.json")
        self.assertEqual(code, 0, err)
        sent = self.sent_request(1)
        self.assertEqual(sent["hedges_in_original"], HEDGED_OCCURRENCES)
        self.assertEqual({k: v for k, v in sent.items() if k != "hedges_in_original"}, HEDGED_REQUEST)
        prompt = self.prompt(1)
        self.assertTrue(prompt.startswith("You are an independent auditor of scientific meaning, checking a rewrite that someone else submitted."))
        self.assertIn('"hedges_in_original"', prompt)
        self.assertIn("hedges_in_original", prompt.split("\n\n")[0])
        audit = json.loads((self.root / "C06.audit.json").read_text())
        self.assertEqual(audit["audit_round"], 1)
        self.assertEqual(audit["checks"]["hedges"], [KEPT_MAY, KEPT_CONDITIONS])
        self.assertEqual((audit["hedges_in_original"], audit["hedge_patterns"]), (HEDGED_OCCURRENCES, []))
        self.assertEqual(audit["checks"]["added_qualification"], "same")
        self.assertEqual(json.loads((self.events / "summary.json").read_text())["verdict"], "preserved")

    def test_audit_checks_without_hedges_or_with_nine_properties_are_corrected(self):
        nine = json.loads(self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]))
        del nine["checks"]["added_qualification"]
        self.responses(self.hedged_verdict(None), self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]), json.dumps(nine), self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]))
        for n, (name, events) in enumerate((("no-hedges", self.events), ("nine", self.root / "events-nine")), 1):
            with self.subTest(name=name):
                code, out, err = self.audit(self.hedged_request(f"{name}.input.json"), f"{name}.audit.json", events=events)
                self.assertEqual(code, 0, err)
                correction = self.prompt(2 * n)
                self.assertIn("checks must map each of the ten properties to same, changed, or not_applicable and carry a hedges array", correction)
                self.assertIn("Format note from the controller", correction)
                summary = json.loads((events / "summary.json").read_text())
                self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("C06", "invalid_format"), ("C06b", "ok")])
                self.assertEqual(json.loads((self.root / f"{name}.audit.json").read_text())["checks"]["hedges"], [KEPT_MAY, KEPT_CONDITIONS])

    def test_audit_hedges_missing_an_occurrence_is_corrected(self):
        self.responses(self.hedged_verdict([KEPT_CONDITIONS]), self.hedged_verdict([KEPT_CONDITIONS, {**KEPT_MAY, "term": "May"}]))
        code, out, err = self.audit(self.hedged_request(), "C06.audit.json")
        self.assertEqual(code, 0, err)
        self.assertIn("audit.checks.hedges does not report every entry in hedges_in_original; missing: H1.", self.prompt(2))
        self.assertIn("does not report every entry", (self.events / "C06.invalid-reason.txt").read_text())
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("C06", "invalid_format"), ("C06b", "ok")])
        self.assertEqual([h["id"] for h in json.loads((self.root / "C06.audit.json").read_text())["checks"]["hedges"]], ["H2", "H1"])

    def test_audit_hedge_removed_requires_a_changed_property(self):
        wording = "Counts rise under these conditions."
        removed = [hedge(1, "may", "removed"), KEPT_CONDITIONS]
        self.responses(self.hedged_verdict(removed, proposed_wording=wording),
                       self.hedged_verdict(removed, verdict="changed", checks={"claim_strength": "changed"}, proposed_wording=wording))
        code, out, err = self.audit(self.hedged_request(proposed_wording=wording), "C06.audit.json")
        self.assertEqual(code, 0, err)
        self.assertIn("audit.checks.hedges reports H1 strengthened or removed, so claim_strength or uncertainty must be changed.", self.prompt(2))
        audit = json.loads((self.root / "C06.audit.json").read_text())
        self.assertEqual((audit["verdict"], audit["checks"]["claim_strength"], audit["checks"]["hedges"][0]["status"]), ("changed", "changed", "removed"))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("C06", "invalid_format"), ("C06b", "ok")])
        self.assertIn("changed: claim_strength", summary["outcome"])

    def test_audit_hedge_strengthened_with_uncertainty_changed_is_accepted(self):
        hedges = [hedge(1, "may", "strengthened"), KEPT_CONDITIONS]
        self.responses(self.hedged_verdict(hedges, verdict="changed", checks={"uncertainty": "changed"}))
        code, out, err = self.audit(self.hedged_request(), "C06.audit.json")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.events / "C06b.prompt.txt").exists())
        self.assertEqual(json.loads((self.root / "C06.audit.json").read_text())["checks"]["hedges"], hedges)

    def test_audit_duplicate_hedge_id_is_corrected(self):
        twice = [KEPT_MAY, KEPT_CONDITIONS, {**KEPT_MAY, "status": "removed", "kept_as": None}]
        self.responses(self.hedged_verdict(twice), self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]))
        code, out, err = self.audit(self.hedged_request(), "C06.audit.json")
        self.assertEqual(code, 0, err)
        self.assertIn("audit.checks.hedges reports H1 twice; one entry per occurrence.", self.prompt(2))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("C06", "invalid_format"), ("C06b", "ok")])
        self.assertEqual(json.loads((self.root / "C06.audit.json").read_text())["checks"]["hedges"], [KEPT_MAY, KEPT_CONDITIONS])

    def test_audit_kept_hedge_needs_a_hedge_in_the_wording(self):
        wording = "Counts rise."
        kept = [{**KEPT_MAY, "kept_as": "rise"}, hedge(2, "under these conditions", "removed")]
        honest = [hedge(1, "may", "removed"), hedge(2, "under these conditions", "removed")]
        self.responses(self.hedged_verdict(kept, checks={"uncertainty": "changed"}, verdict="changed", proposed_wording=wording),
                       self.hedged_verdict(honest, checks={"claim_strength": "changed", "uncertainty": "changed"}, verdict="changed", proposed_wording=wording))
        code, out, err = self.audit(self.hedged_request(proposed_wording=wording), "C06.audit.json")
        self.assertEqual(code, 0, err)
        self.assertIn("audit.checks.hedges[0].kept_as 'rise' contains no hedge the helper recognizes", self.prompt(2))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("C06", "invalid_format"), ("C06b", "ok")])
        self.assertEqual([h["status"] for h in json.loads((self.root / "C06.audit.json").read_text())["checks"]["hedges"]], ["removed", "removed"])
        # A kept hedge survives through a synonym the helper lists, with no correction.
        (self.fake / "counter.txt").write_text("0")
        could = [{**KEPT_MAY, "kept_as": "could"}, KEPT_CONDITIONS]
        self.responses(self.hedged_verdict(could, proposed_wording="Counts could rise under these conditions."))
        code, out, err = self.audit(self.hedged_request("synonym.input.json", proposed_wording="Counts could rise under these conditions."), "synonym.audit.json", events=self.root / "events-synonym")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "events-synonym" / "C06b.prompt.txt").exists())
        self.assertEqual(json.loads((self.root / "synonym.audit.json").read_text())["checks"]["hedges"], could)

    def test_audit_kept_hedges_cannot_share_words(self):
        # Two occurrences of "may", one left in the wording: both cannot be kept (the diagnosis's first case).
        original = [{"block_id": "B0002", "quote": "Counts may rise. Costs may fall."}]
        wording = "Counts rise. Costs may fall."
        lying = [hedge(1, "may", "kept", "may"), hedge(2, "may", "kept", "may")]
        honest = [hedge(1, "may", "removed"), hedge(2, "may", "kept", "may")]
        self.responses(json.dumps({**json.loads(self.hedged_verdict(lying, proposed_wording=wording)), "original": original}),
                       json.dumps({**json.loads(self.hedged_verdict(honest, verdict="changed", checks={"claim_strength": "changed"}, proposed_wording=wording)), "original": original}))
        code, out, err = self.audit(self.hedged_request("two.input.json", original=original, proposed_wording=wording), "two.audit.json")
        self.assertEqual(code, 0, err)
        self.assertIn("reports H1, H2 kept, but the proposed wording does not hold a separate qualifying phrase for each", self.prompt(2))
        self.assertEqual([h["status"] for h in json.loads((self.root / "two.audit.json").read_text())["checks"]["hedges"]], ["removed", "kept"])
        # A surviving condition cannot stand in for a deleted "may" (the diagnosis's third case).
        (self.fake / "counter.txt").write_text("0")
        wording = "Counts rise under these conditions."
        self.responses(self.hedged_verdict([{**KEPT_MAY, "kept_as": "under these conditions"}, KEPT_CONDITIONS], proposed_wording=wording),
                       self.hedged_verdict([hedge(1, "may", "removed"), KEPT_CONDITIONS], verdict="changed", checks={"claim_strength": "changed"}, proposed_wording=wording))
        code, out, err = self.audit(self.hedged_request("cond.input.json", proposed_wording=wording), "cond.audit.json", events=self.root / "events-cond")
        self.assertEqual(code, 0, err)
        self.assertIn("does not hold a separate qualifying phrase for each", (self.root / "events-cond" / "C06b.prompt.txt").read_text())

    def test_audit_hedge_item_shape_and_status_are_checked(self):
        self.responses(self.hedged_verdict([{"term": "may", "status": "kept"}, KEPT_CONDITIONS]), self.hedged_verdict([{**KEPT_MAY, "status": "weakened"}, KEPT_CONDITIONS]), self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]))
        code, out, err = self.audit(self.hedged_request(), "C06.audit.json")
        self.assertEqual(code, 0, err)
        self.assertIn("audit.checks.hedges[0] must be an object with exactly id, term, status, and kept_as.", self.prompt(2))
        self.assertIn("audit.checks.hedges[0].status must be one of: kept, removed, strengthened.", self.prompt(3))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([r["status"] for r in summary["calls"]], ["invalid_format", "invalid_format", "ok"])

    def test_audit_round_is_recorded_from_the_option(self):
        self.responses(self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]))
        code, out, err = self.audit(self.hedged_request(), "round2.audit.json", "--round", "2")
        self.assertEqual(code, 0, err)
        audit = json.loads((self.root / "round2.audit.json").read_text())
        self.assertEqual(audit["audit_round"], 2)
        self.assertEqual(list(audit), ["candidate_id", "proposed_wording", "original", "verdict", "checks", "notes", "audit_round", "hedges_in_original", "hedge_patterns", "auditor"])
        self.assertNotIn("audit_round", self.sent_request(1))
        code, out, err = self.audit(self.hedged_request(), "round0.audit.json", "--round", "0", events=self.root / "events-zero")
        self.assertEqual(code, 2)
        error = json.loads(err)
        self.assertEqual(error["error"]["code"], "dispatch_error")
        self.assertIn("--round must be a positive integer", error["error"]["message"])
        self.assertFalse((self.root / "round0.audit.json").exists())
        self.assertFalse((self.root / "events-zero" / "C06.prompt.txt").exists())
        self.assertEqual((self.fake / "counter.txt").read_text(), "1", "no model call was made for --round 0")

    def test_hedge_list_extends_hedges_in_original(self):
        hedge_list = self.root / "hedges.json"
        hedge_list.write_text(json.dumps(["\\bwe believe\\b"]))
        original = [{"block_id": "B0002", "quote": "We believe counts rose."}]
        believe = hedge(1, "We believe", "kept", "We believe")
        self.responses(json.dumps({**json.loads(self.hedged_verdict([believe])), "original": original, "proposed_wording": "We believe counts increased."}))
        code, out, err = self.audit(self.hedged_request("believe.input.json", original=original, proposed_wording="We believe counts increased."), "believe.audit.json", "--hedge-list", str(hedge_list))
        self.assertEqual(code, 0, err)
        self.assertEqual([(o["id"], o["term"]) for o in self.sent_request(1)["hedges_in_original"]], [("H1", "We believe")])
        audit = json.loads((self.root / "believe.audit.json").read_text())
        self.assertEqual((audit["checks"]["hedges"], audit["hedge_patterns"]), ([believe], ["\\bwe believe\\b"]))
        # A phrase the input lists joins the patterns; one already found adds nothing; one absent from the original is refused before any call.
        self.responses(self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]),
                       json.dumps({**json.loads(self.hedged_verdict([hedge(1, "According to the authors", "kept", "According to the authors"), {**KEPT_MAY, "id": "H2"}, {**KEPT_CONDITIONS, "id": "H3"}],
                                                                    proposed_wording="According to the authors, counts might rise under these conditions.")),
                                   "original": [{"block_id": "B0002", "quote": "According to the authors, counts may rise under these conditions."}]}))
        (self.fake / "counter.txt").write_text("0")
        code, out, err = self.audit(self.hedged_request("given.input.json", hedges_in_original=["May"]), "given.audit.json", events=self.root / "events-given")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.sent_request(1)["hedges_in_original"], HEDGED_OCCURRENCES)
        extra = self.hedged_request("extra.input.json", hedges_in_original=["according  to the authors"], original=[{"block_id": "B0002", "quote": "According to the authors, counts may rise under these conditions."}],
                                    proposed_wording="According to the authors, counts might rise under these conditions.")
        code, out, err = self.audit(extra, "extra.audit.json", events=self.root / "events-extra")
        self.assertEqual(code, 0, err)
        self.assertEqual([o["term"] for o in self.sent_request(2)["hedges_in_original"]], ["According to the authors", "may", "under these conditions"])
        self.assertEqual(json.loads((self.root / "extra.audit.json").read_text())["hedge_patterns"], [rp.term_pattern("according  to the authors")])
        code, out, err = self.audit(self.hedged_request("absent.input.json", hedges_in_original=["according to the authors"]), "absent.audit.json", events=self.root / "events-absent")
        self.assertEqual(code, 2)
        self.assertIn("hedges_in_original in --input lists 'according to the authors', which does not occur in the original quotes.", err)
        code, out, err = self.audit(self.hedged_request("bad.input.json", hedges_in_original="may"), "bad.audit.json", events=self.root / "events-bad")
        self.assertEqual(code, 2)
        self.assertIn("hedges_in_original in --input must be an array of nonempty strings when present.", err)
        self.assertEqual((self.fake / "counter.txt").read_text(), "2", "no model call was made for the refused inputs")

    def test_malformed_hedge_list_exits_2_before_any_call(self):
        self.responses(self.hedged_verdict([KEPT_MAY, KEPT_CONDITIONS]))
        for name, value, message in (("object.json", {"a": 1}, "--hedge-list must be a JSON array of regular-expression strings."),
                                     ("blank.json", [""], "--hedge-list must be a JSON array of regular-expression strings."),
                                     ("regex.json", ["("], "--hedge-list pattern '(' does not compile"),
                                     ("empty-match.json", ["x*"], "--hedge-list pattern 'x*' matches the empty string; every pattern must require at least one character."),
                                     ("empty-alternative.json", ["\\bwe believe\\b|"], "matches the empty string"),
                                     ("inline-flag.json", ["(?i)foo"], "--hedge-list patterns do not combine with the built-in list (no inline flags such as (?i) are allowed)"),
                                     ("later-inline-flag.json", ["\\bfoo\\b", "(?m)bar"], "do not combine with the built-in list")):
            with self.subTest(name=name):
                path = self.root / name
                path.write_text(json.dumps(value))
                code, out, err = self.audit(self.hedged_request(), name + ".audit.json", "--hedge-list", str(path), events=self.root / ("events-" + name))
                self.assertEqual(code, 2, err)
                error = json.loads(err)
                self.assertEqual(error["error"]["code"], "dispatch_error")
                self.assertIn(message, error["error"]["message"])
                self.assertFalse((self.root / (name + ".audit.json")).exists())
        self.assertFalse((self.fake / "counter.txt").exists(), "no model call was made")
        # A pattern with its own alternation and a group, but no inline flag, combines with the built-in list.
        good = self.root / "good.json"
        good.write_text(json.dumps(["\\bwe (?:believe|think)\\b"]))
        self.assertEqual(dr.load_hedge_list(str(good)), ["\\bwe (?:believe|think)\\b"])
        original = [{"block_id": "B0002", "quote": "We think counts may rise."}]
        self.responses(json.dumps({**json.loads(self.hedged_verdict([hedge(1, "We think", "kept", "We think"), {**KEPT_MAY, "id": "H2"}])), "original": original, "proposed_wording": "We think counts might rise."}))
        code, out, err = self.audit(self.hedged_request("good.input.json", original=original, proposed_wording="We think counts might rise."), "good.audit.json", "--hedge-list", str(good), events=self.root / "events-good")
        self.assertEqual(code, 0, err)
        self.assertEqual([o["term"] for o in self.sent_request(1)["hedges_in_original"]], ["We think", "may"])

    # ------------------------------------------------------------ CLI

    def test_claude_runtime_has_no_built_in_model(self):
        self.responses(self.audit_verdict_json())
        code, out, err = self.main(["audit", "--events", str(self.events), "--input", str(self.audit_request()), "--out", str(self.root / "a.json")], choose_model=False)
        self.assertEqual(code, 2)
        self.assertIn("ask the user which model to use", err)
        self.assertFalse((self.events / "C04.prompt.txt").exists(), "nothing is dispatched before the model is chosen")
        self.assertFalse(hasattr(dr, "CLAUDE_DEFAULT_MODEL"))

    def test_model_default_leaves_the_choice_to_the_claude_command(self):
        self.responses(self.audit_verdict_json())
        code, out, err = self.main(["audit", "--events", str(self.events), "--input", str(self.audit_request()), "--out", str(self.root / "a.json"), "--model", "default"])
        self.assertEqual(code, 0, err)
        argv = json.loads((self.fake / "prompts" / "1.argv.json").read_text())
        self.assertNotIn("--model", argv)

    def test_a_named_model_is_passed_through_and_recorded(self):
        self.responses(self.audit_verdict_json())
        code, out, err = self.main(["audit", "--events", str(self.events), "--input", str(self.audit_request()), "--out", str(self.root / "a.json"), "--model", "claude-sonnet-5-5"])
        self.assertEqual(code, 0, err)
        argv = json.loads((self.fake / "prompts" / "1.argv.json").read_text())
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")
        provenance = json.loads((self.events / "C04.provenance.json").read_text())
        self.assertIn("claude-sonnet-5-5", provenance["command"])

    def audit_request(self):
        request = {"candidate_id": "C04", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "proposed_wording": "Counts increased.", "basis": [{"fact": "Counts rose.", "location": "B0002"}]}
        path = self.root / "C04.input.json"
        path.write_text(json.dumps(request))
        return path

    def audit_verdict_json(self):
        return json.dumps({"candidate_id": "C04", "proposed_wording": "Counts increased.", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "verdict": "preserved", "checks": {**{k: "same" for k in dr.AUDIT_CHECKS}, "hedges": []}, "notes": "No property changed.", "auditor": {"mode": "bounded-request", "agent_id": "x"}})

    def test_help_runs_isolated_from_another_directory(self):
        result = subprocess.run([sys.executable, "-B", "-I", str(SCRIPTS / "dispatch_reader.py"), "--help"], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("prefix", "masked", "branch", "audit"):
            self.assertIn(name, result.stdout)

    def test_config_is_rejected_for_the_claude_runtime(self):
        config = self.root / "llm.json"
        config.write_text("{}")
        code, out, err = self.main(["prefix", "--events", str(self.events), "--packet", "x.json", "--config", str(config)])
        self.assertEqual(code, 2)
        self.assertIn("--config applies to --runtime http only.", err)


# ---------------------------------------------------------------- codex runtime


class CodexRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.fake = self.root / "fake-bin"
        self.fake.mkdir()
        script = self.fake / "codex"
        script.write_text(FAKE_CODEX.format(python=sys.executable))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.fake) + os.pathsep + self.old_path
        self.events = self.root / "events"

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()

    def responses(self, *entries):
        (self.fake / "codex-responses.json").write_text(json.dumps(list(entries)))

    def main(self, argv, choose_model=True):
        if choose_model and "--model" not in argv:
            argv = [*argv, "--model", "default"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dr.main(argv)
        return code, out.getvalue(), err.getvalue()

    def audit_input(self):
        path = self.root / "audit-input.json"
        path.write_text(json.dumps({"candidate_id": "C01", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "proposed_wording": "Counts increased.", "basis": [{"fact": "Counts rose.", "location": "B0002"}]}))
        return path

    def audit_response(self):
        return json.dumps({
            "candidate_id": "C01", "proposed_wording": "Counts increased.", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "verdict": "preserved",
            "checks": {**{k: "same" for k in dr.AUDIT_CHECKS}, "hedges": []}, "notes": "No property changed.", "auditor": {"mode": "bounded-request", "agent_id": "filled in by the controller"},
        })

    def test_audit_over_codex_prepends_the_role_and_records_the_thread(self):
        self.responses({"text": self.audit_response()})
        out_path = self.root / "audit.json"
        code, out, err = self.main(["audit", "--runtime", "codex", "--effort", "low", "--model", "gpt-fake", "--events", str(self.events), "--input", str(self.audit_input()), "--out", str(out_path)])
        self.assertEqual(code, 0, err)
        argv = json.loads((self.fake / "codex-prompts" / "1.argv.json").read_text())
        self.assertEqual(argv[1:8], dr.CODEX_FLAGS)
        self.assertIn("--model", argv)
        self.assertIn("model_reasoning_effort=\"low\"", argv)
        self.assertEqual(argv[-1], "-")
        sent = (self.fake / "codex-prompts" / "1.txt").read_text()
        self.assertTrue(sent.startswith(dr.SYSTEM_PROMPTS["auditor"] + "\n\n"))
        provenance = json.loads((self.events / "C01.provenance.json").read_text())
        self.assertEqual(provenance["runtime"], "codex")
        self.assertEqual(provenance["session_id"], "thread0001-aaaa")
        self.assertEqual(provenance["input_tokens"], 18390)
        self.assertEqual(provenance["cached_input_tokens"], 12288)
        self.assertIsNone(provenance["cost_usd"])
        self.assertEqual(provenance["command_executions"], [])
        self.assertEqual(provenance["isolation"], dr.CODEX_ISOLATION_NOTE)
        self.assertEqual(provenance["system_prompt_delivery"], "prepended to the stdin prompt, separated by a blank line")
        record = json.loads((self.events / "C01.cli.json").read_text())
        self.assertEqual(record["unparsed"], ["not json at all"])
        self.assertIn("failed to load skill", record["stderr_tail"])
        audit = json.loads(out_path.read_text())
        self.assertEqual(audit["auditor"]["agent_id"], "codex-exec-thread00")
        self.assertEqual((self.events / "C01.response.raw").read_text(), self.audit_response())

    def test_a_command_execution_makes_the_call_unusable(self):
        self.responses({"text": self.audit_response(), "command": "cat manuscript.md"})
        code, out, err = self.main(["audit", "--runtime", "codex", "--events", str(self.events), "--input", str(self.audit_input()), "--out", str(self.root / "audit.json")])
        self.assertNotEqual(code, 0)
        self.assertIn("codex ran 1 command(s)", out + err)
        self.assertFalse((self.events / "C01b.prompt.txt").exists(), "a runtime rejection is not re-dispatched")
        provenance = json.loads((self.events / "C01.provenance.json").read_text())
        self.assertEqual(provenance["command_executions"][0]["command"], "cat manuscript.md")
        self.assertFalse((self.root / "audit.json").exists())

    def test_codex_runtime_needs_a_chosen_model(self):
        code, out, err = self.main(["audit", "--runtime", "codex", "--events", str(self.events), "--input", str(self.audit_input()), "--out", str(self.root / "a.json")], choose_model=False)
        self.assertEqual(code, 2)
        self.assertIn("--model default to let the codex command use its own configured default", err)

    def test_codex_runtime_rejects_config_and_needs_the_executable(self):
        config = self.root / "llm.json"
        config.write_text("{}")
        code, out, err = self.main(["audit", "--runtime", "codex", "--config", str(config), "--events", str(self.events), "--input", str(self.audit_input()), "--out", str(self.root / "a.json")])
        self.assertEqual(code, 2)
        self.assertIn("--config applies to --runtime http only.", err)
        code, out, err = self.main(["audit", "--runtime", "codex", "--codex", "/nonexistent/codex", "--events", str(self.events), "--input", str(self.audit_input()), "--out", str(self.root / "a.json")])
        self.assertNotEqual(code, 0)

    def test_events_parser_keeps_unparsed_lines(self):
        events, unparsed = dr.CodexRuntime.events(b'{"type":"turn.started"}\n\nhalf a line\n[1,2]\n')
        self.assertEqual(events, [{"type": "turn.started"}])
        self.assertEqual(unparsed, ["half a line", "[1,2]"])


# ---------------------------------------------------------------- http runtime


API_KEY = "sk-test-secret-0000"
SYSTEM_READER = "You are the reader described in the user's message. Return only the JSON the message asks for, with no code fence and no commentary."


def completion(text, number=1, prompt_tokens=200, completion_tokens=50):
    body = {"id": f"chatcmpl-{number:04d}", "object": "chat.completion", "created": 1700000000, "model": "fake-llm",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}}
    return 200, {}, json.dumps(body).encode("utf-8")


class FakeChatServer:
    """A chat-completions endpoint in a thread; answers come from a script, one entry per request."""

    def __init__(self):
        self.script = []
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                number = len(server.requests)
                server.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(body)})
                status, headers, payload = server.script[number] if number < len(server.script) else completion("{}", number + 1)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def prefix_fixture(root):
    index = rp.build_index(TEXT)
    packet = rp.build_packet(index, PROFILE, through_block="B0002")
    packet_path = root / "P1.packet.json"
    rp.write_json_atomic(packet_path, packet)
    observation = {k: packet[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256")}
    observation.update({"understanding": "Counts of agreement rose.", "issues": [{"id": "R1", "category": "unavailable_prerequisite", "anchor": {"block_id": "B0002", "quote": "counts of agreement"}, "reader_question": "What is agreement?", "consequence": "The claim is unclear.", "severity": "major", "confidence": "high"}]})
    return packet_path, observation


class HttpRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.server = FakeChatServer()
        self.old_key = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = API_KEY
        self.config = self.root / "llm.json"
        self.write_config()
        self.events = self.root / "events"
        self.run = self.root / "run"
        self.case_path = self.root / "case.json"
        self.case_path.write_text(json.dumps(CASE))
        self.profile_path = self.root / "profile.json"
        self.profile_path.write_text(json.dumps(MASKED_PROFILE))
        self.packet_path, self.observation = prefix_fixture(self.root)

    def tearDown(self):
        self.server.close()
        if self.old_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = self.old_key
        self.tmp.cleanup()

    def write_config(self, **changes):
        config = {"base_url": self.server.base_url, "model": "fake-llm", "api_key_env": "LLM_API_KEY", "temperature": 0, "max_tokens": 4096, "json_mode": True, "timeout_seconds": 300, "extra_headers": {}, "retry_backoff_seconds": 0.01}
        config.update(changes)
        self.config.write_text(json.dumps({k: v for k, v in config.items() if v is not ...}))

    def main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dr.main(argv)
        return code, out.getvalue(), err.getvalue()

    def prefix(self, *extra):
        return self.main(["prefix", "--runtime", "http", "--config", str(self.config), "--events", str(self.events), "--packet", str(self.packet_path), "--label", "P1", *extra])

    def masked(self, *extra):
        return self.main(["masked", "--runtime", "http", "--config", str(self.config), "--events", str(self.events), "--run", str(self.run), "--case", str(self.case_path), "--profile", str(self.profile_path), *extra])

    def user_prompt(self, number):
        return self.server.requests[number - 1]["body"]["messages"][1]["content"]

    def provenance(self, label):
        return json.loads((self.events / f"{label}.provenance.json").read_text())

    def test_prefix_dispatch_over_http_passes_check_observation(self):
        self.server.script = [completion(json.dumps(self.observation))]
        code, out, err = self.prefix()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads((self.events / "P1.check.json").read_text())["issue_count"], 1)
        self.assertEqual(json.loads((self.events / "P1.observation.json").read_text()), self.observation)
        request = self.server.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["headers"]["authorization"], "Bearer sk-test-secret-0000")
        self.assertEqual(request["headers"]["content-type"], "application/json")
        body = request["body"]
        self.assertEqual(sorted(body), ["max_tokens", "messages", "model", "response_format", "temperature"])
        self.assertEqual((body["model"], body["temperature"], body["max_tokens"], body["response_format"]), ("fake-llm", 0, 4096, {"type": "json_object"}))
        self.assertEqual(body["messages"][0], {"role": "system", "content": SYSTEM_READER})
        self.assertEqual(body["messages"][1]["role"], "user")
        self.assertTrue(body["messages"][1]["content"].startswith("Act as the target academic reader"))
        self.assertTrue(body["messages"][1]["content"].rstrip().endswith("}"))
        self.assertEqual(body["messages"][1]["content"], (self.events / "P1.prompt.txt").read_text())
        for suffix in ("prompt.txt", "cli.json", "response.raw", "response.json", "provenance.json"):
            self.assertTrue((self.events / f"P1.{suffix}").exists(), suffix)
        cli = json.loads((self.events / "P1.cli.json").read_text())
        self.assertEqual((cli["id"], cli["model"], cli["choices"][0]["finish_reason"]), ("chatcmpl-0001", "fake-llm", "stop"))
        self.assertEqual((self.events / "P1.response.raw").read_text(), json.dumps(self.observation))
        provenance = self.provenance("P1")
        self.assertEqual(provenance["runtime"], "http")
        self.assertEqual(provenance["base_url"], self.server.base_url)
        self.assertEqual(provenance["url"], self.server.base_url + "/chat/completions")
        self.assertEqual(provenance["model"], "fake-llm")
        self.assertEqual(provenance["api_key_env"], "LLM_API_KEY")
        self.assertEqual(provenance["request"], {"temperature": 0, "max_tokens": 4096, "response_format": {"type": "json_object"}, "timeout_seconds": 300.0})
        self.assertEqual((provenance["http_status"], provenance["attempt_count"]), (200, 1))
        self.assertEqual((provenance["attempts"][0]["attempt"], provenance["attempts"][0]["status"], provenance["attempts"][0]["error"]), (1, 200, None))
        self.assertEqual((provenance["response_id"], provenance["session_id"], provenance["response_model"], provenance["finish_reason"]), ("chatcmpl-0001", "chatcmpl-0001", "fake-llm", "stop"))
        self.assertEqual(provenance["usage"], {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250})
        self.assertEqual((provenance["input_tokens"], provenance["output_tokens"], provenance["cost_usd"]), (200, 50, None))
        self.assertEqual(provenance["isolation"], "bare chat-completions request with no tools; isolation by request content, not by sandbox")
        self.assertIsNone(provenance["parse_error"])
        self.assertIsNone(provenance["error"])
        self.assertFalse(provenance["fence_stripped"])
        self.assertEqual(provenance["packet_sha256"], mr.digest(self.packet_path.read_bytes()))
        self.assertNotIn("command", provenance)
        self.assertNotIn("exit_code", provenance)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"], r["session_id"], r["exit_code"]) for r in summary["calls"]], [("P1", "ok", "chatcmpl-0001", None)])
        self.assertEqual(summary["totals"]["cost_usd"], 0)
        self.assertIn("P1 passed check-observation with 1 issue(s).", out)

    def test_masked_loop_over_http_runs_two_windows(self):
        self.server.script = [completion(masked_response([N1]), 1), completion(masked_response([N1, N2], claim_source="B2"), 2)]
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001", "000002"])
        self.assertEqual(json.loads((self.run / "records" / "000001" / "receipt.json").read_text())["agent_id"], "chatcmpl-0001")
        self.assertEqual(json.loads((self.run / "records" / "000002" / "receipt.json").read_text())["agent_id"], "chatcmpl-0002")
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "ok"), ("E02", "ok")])
        self.assertEqual(summary["totals"]["calls"], 2)
        self.assertTrue(summary["outcome"].startswith("Run complete after 2 recorded event(s)."))
        self.assertTrue(self.user_prompt(2).startswith("You are a fresh reader"))
        for suffix in ("packet.json", "prompt.txt", "cli.json", "response.raw", "response.json", "provenance.json"):
            self.assertTrue((self.events / f"E02.{suffix}").exists(), suffix)

    def test_budget_violation_is_corrected_over_http(self):
        long_gist = " ".join(["word"] * 30)
        self.server.script = [completion(masked_response([N1], gist=long_gist), 1), completion(masked_response([N1]), 2), completion(masked_response([N1, N2], claim_source="B2"), 3)]
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        self.assertIn("carry_memory has 33 words; limit is 20", (self.events / "E01.invalid-reason.txt").read_text())
        self.assertIn("Budget note from the controller: a previous attempt at this same packet returned a carry_memory of 33 words, above the 20-word limit", self.user_prompt(2))
        self.assertEqual(self.server.requests[1]["body"]["messages"][0]["content"], SYSTEM_READER)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("E01", "invalid_budget"), ("E01b", "ok"), ("E02", "ok")])
        self.assertEqual(self.provenance("E01b")["corrects"], "E01")

    def test_server_error_is_retried_inside_one_call(self):
        self.server.script = [(500, {}, b"upstream exploded"), completion(json.dumps(self.observation))]
        code, out, err = self.prefix()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.server.requests[0]["body"], self.server.requests[1]["body"])
        provenance = self.provenance("P1")
        self.assertEqual((provenance["http_status"], provenance["attempt_count"]), (200, 2))
        self.assertEqual([(a["attempt"], a["status"], a["error"], a["retry_after"], a["waited_seconds"]) for a in provenance["attempts"]], [(1, 500, None, None, 0.01), (2, 200, None, None, None)])
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("P1", "ok")])
        self.assertFalse((self.events / "P1b.prompt.txt").exists())

    def test_rate_limit_honors_retry_after(self):
        self.server.script = [(429, {"Retry-After": "0"}, b'{"error": {"message": "slow down"}}'), completion(json.dumps(self.observation))]
        code, out, err = self.prefix()
        self.assertEqual(code, 0, err)
        provenance = self.provenance("P1")
        self.assertEqual([(a["status"], a["retry_after"], a["waited_seconds"]) for a in provenance["attempts"]], [(429, 0.0, 0.0), (200, None, None)])
        self.assertEqual(provenance["http_status"], 200)

    def test_three_failures_exhaust_retries_and_enter_the_correction_protocol(self):
        self.server.script = [(503, {}, b"busy"), (503, {}, b"busy"), (503, {}, b"busy"), completion(json.dumps(self.observation))]
        code, out, err = self.prefix()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.server.requests), 4)
        first = self.provenance("P1")
        self.assertEqual((first["http_status"], first["attempt_count"], first["parse_error"], first["error"]), (503, 3, "HTTP 503 after 3 attempts", "HTTP 503 after 3 attempts"))
        self.assertEqual([a["status"] for a in first["attempts"]], [503, 503, 503])
        self.assertEqual((self.events / "P1.response.raw").read_bytes(), b"busy")
        self.assertFalse((self.events / "P1.response.json").exists())
        cli = json.loads((self.events / "P1.cli.json").read_text())
        self.assertEqual((cli["error"], cli["status"], cli["body"]), ("HTTP 503 after 3 attempts", 503, "busy"))
        self.assertIn("P1 not accepted (format): HTTP 503 after 3 attempts.", (self.events / "P1.invalid-reason.txt").read_text())
        self.assertIn("Format note from the controller", self.user_prompt(4))
        self.assertIn("HTTP 503 after 3 attempts", self.user_prompt(4))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("P1", "invalid_format"), ("P1b", "ok")])

    def test_client_error_is_not_retried(self):
        self.server.script = [(400, {}, b'{"error": {"message": "bad request"}}'), completion(json.dumps(self.observation))]
        code, out, err = self.prefix()
        self.assertEqual(code, 0, err)
        first = self.provenance("P1")
        self.assertEqual((first["http_status"], first["attempt_count"], first["parse_error"]), (400, 1, "HTTP 400"))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("P1", "invalid_format"), ("P1b", "ok")])

    def test_non_json_content_is_corrected_on_the_second_attempt(self):
        self.server.script = [completion("Sure! Here is the JSON: {", 1), completion("```json\n" + json.dumps(self.observation) + "\n```", 2)]
        code, out, err = self.prefix()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.events / "P1.response.raw").read_text(), "Sure! Here is the JSON: {")
        self.assertFalse((self.events / "P1.response.json").exists())
        self.assertTrue(self.provenance("P1")["parse_error"].startswith("JSONDecodeError"))
        self.assertEqual(self.provenance("P1")["http_status"], 200)
        self.assertIn("Format note from the controller", self.user_prompt(2))
        self.assertIn("no code fence", self.user_prompt(2))
        self.assertTrue(self.provenance("P1b")["fence_stripped"])
        self.assertEqual(json.loads((self.events / "P1b.response.json").read_text()), self.observation)
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("P1", "invalid_format"), ("P1b", "ok")])

    def test_missing_api_key_env_exits_2_with_a_clear_message(self):
        del os.environ["LLM_API_KEY"]
        code, out, err = self.prefix()
        self.assertEqual(code, 2)
        error = json.loads(err)
        self.assertEqual(error["error"]["code"], "dispatch_error")
        self.assertEqual(error["error"]["message"], f"Environment variable LLM_API_KEY (named by api_key_env in {self.config}) is not set; export the API key there. The key is never read from the config file or the command line.")
        self.assertEqual(self.server.requests, [])
        self.assertEqual(sorted(p.name for p in self.events.iterdir()), [])

    def test_api_key_in_the_config_file_is_refused(self):
        self.config.write_text(json.dumps({"base_url": self.server.base_url, "model": "fake-llm", "api_key": API_KEY}))
        code, out, err = self.prefix()
        self.assertEqual(code, 2)
        self.assertIn("The config file must not hold an API key; name an environment variable in api_key_env and export the key there.", err)
        self.assertEqual(self.server.requests, [])

    def test_no_output_file_contains_the_api_key(self):
        self.server.script = [completion(masked_response([N1]), 1), completion(masked_response([N1, N2], claim_source="B2"), 2)]
        code, out, err = self.masked()
        self.assertEqual(code, 0, err)
        files = [p for p in list(self.events.rglob("*")) + list(self.run.rglob("*")) if p.is_file()]
        self.assertEqual(len([p for p in files if p.name.endswith(".provenance.json")]), 2)
        for path in files:
            data = path.read_bytes()
            self.assertNotIn(API_KEY.encode(), data, path.name)
            self.assertNotIn(b"Authorization", data, path.name)
        self.assertNotIn(API_KEY, out + err)
        self.assertEqual(self.provenance("E01")["api_key_env"], "LLM_API_KEY")

    def test_without_api_key_env_no_authorization_header_is_sent(self):
        self.write_config(api_key_env=..., max_tokens=..., json_mode=False)
        self.server.script = [completion(json.dumps(self.observation))]
        code, out, err = self.prefix("--timeout", "5")
        self.assertEqual(code, 0, err)
        request = self.server.requests[0]
        self.assertNotIn("authorization", request["headers"])
        self.assertEqual(sorted(request["body"]), ["messages", "model", "temperature"])
        provenance = self.provenance("P1")
        self.assertIsNone(provenance["api_key_env"])
        self.assertEqual(provenance["request"], {"temperature": 0, "max_tokens": None, "response_format": None, "timeout_seconds": 5.0})

    def test_price_table_fills_cost_usd(self):
        self.write_config(price_per_token={"input": 0.00001, "output": 0.00002})
        self.server.script = [completion(json.dumps(self.observation))]
        code, out, err = self.prefix("--model", "other-llm")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.server.requests[0]["body"]["model"], "other-llm")
        provenance = self.provenance("P1")
        self.assertEqual((provenance["model"], provenance["cost_usd"]), ("other-llm", 0.003))
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual(summary["totals"]["cost_usd"], 0.003)
        self.assertIn("0.00300 USD", out)

    def test_audit_over_http_fills_the_auditor_from_the_response_id(self):
        request = {"candidate_id": "C04", "original": [{"block_id": "B0002", "quote": "Counts rose."}], "proposed_wording": "Counts increased.", "basis": [{"fact": "Counts rose.", "location": "B0002"}]}
        request_path = self.root / "C04.input.json"
        request_path.write_text(json.dumps(request))
        verdict = {"candidate_id": "C04", "proposed_wording": "Counts increased.", "original": request["original"], "verdict": "preserved", "checks": {**{k: "same" for k in dr.AUDIT_CHECKS}, "hedges": []}, "notes": "No property changed.", "auditor": {"mode": "", "agent_id": ""}}
        self.server.script = [completion(json.dumps(verdict), 7)]
        out_path = self.root / "C04.audit.json"
        code, out, err = self.main(["audit", "--runtime", "http", "--config", str(self.config), "--events", str(self.events), "--input", str(request_path), "--out", str(out_path)])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out_path.read_text())["auditor"], {"mode": "bounded-request", "agent_id": "http-0007"})
        self.assertEqual(self.server.requests[0]["body"]["messages"][0]["content"], "You are the auditor described in the user's message. Return only the JSON the message asks for, with no code fence and no commentary.")
        self.assertTrue(self.user_prompt(1).startswith("You are an independent auditor of scientific meaning, checking a rewrite that someone else submitted."))

    def test_http_runtime_needs_config_and_rejects_effort(self):
        code, out, err = self.main(["prefix", "--runtime", "http", "--events", str(self.events), "--packet", str(self.packet_path)])
        self.assertEqual(code, 2)
        self.assertIn("--runtime http needs --config PATH", err)
        code, out, err = self.prefix("--effort", "high")
        self.assertEqual(code, 2)
        self.assertIn("--effort applies to the claude and codex runtimes only.", err)
        self.assertEqual(self.server.requests, [])

    def test_unknown_config_key_is_refused(self):
        self.write_config(api_base=self.server.base_url)
        code, out, err = self.prefix()
        self.assertEqual(code, 2)
        self.assertIn("Unknown key(s) in --config: api_base.", err)

    def test_redirect_is_refused_and_not_followed(self):
        self.server.script = [(302, {"Location": "http://127.0.0.1:9/elsewhere/v1/chat/completions"}, b""), completion(json.dumps(self.observation))]
        code, out, err = self.prefix()
        self.assertEqual(code, 0, err)
        self.assertEqual([r["path"] for r in self.server.requests], ["/v1/chat/completions", "/v1/chat/completions"])
        first = self.provenance("P1")
        self.assertEqual((first["http_status"], first["attempt_count"], first["parse_error"]), (302, 1, "HTTP 302 (redirect refused: the request is never resent to another location)"))
        self.assertIsNone(first["transport_warning"])
        summary = json.loads((self.events / "summary.json").read_text())
        self.assertEqual([(r["label"], r["status"]) for r in summary["calls"]], [("P1", "invalid_format"), ("P1b", "ok")])

    def test_cleartext_key_warning(self):
        self.assertIsNone(dr.cleartext_key_warning("http://127.0.0.1:8000/v1/chat/completions", API_KEY))
        self.assertIsNone(dr.cleartext_key_warning("http://localhost:8000/v1/chat/completions", API_KEY))
        self.assertIsNone(dr.cleartext_key_warning("https://llm.example.edu/v1/chat/completions", API_KEY))
        self.assertIsNone(dr.cleartext_key_warning("http://llm.example.edu:8000/v1/chat/completions", None))
        self.assertEqual(dr.cleartext_key_warning("http://llm.example.edu:8000/v1/chat/completions", API_KEY), "API key from the environment is sent in cleartext to llm.example.edu: base_url uses http://; use https:// or keep the endpoint on this machine.")

    def test_retry_after_parses_seconds_and_dates(self):
        self.assertIsNone(dr.parse_retry_after(None))
        self.assertEqual(dr.parse_retry_after("2"), 2.0)
        self.assertEqual(dr.parse_retry_after(" 1.5 "), 1.5)
        self.assertEqual(dr.parse_retry_after("-3"), 0.0)
        self.assertEqual(dr.parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), 0.0)
        self.assertIsNone(dr.parse_retry_after("soon"))


if __name__ == "__main__":
    unittest.main()
