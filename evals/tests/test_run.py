import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
EVALS = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, EVALS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load("run")
rp = run.rp

# A stand-in for `claude -p` that answers from the prompt alone, so calls may run in parallel.
# Audit: verdict changed when the proposed wording starts with "X", else preserved (the test
# cases carry no hedges). Prefix: one issue that quotes the whole of the last visible block.
FAKE_CLAUDE = """#!{python}
import json, sys
data = json.loads(sys.stdin.read().rsplit("\\n\\n", 1)[1])
if "candidate_id" in data:
    changed = data["proposed_wording"].startswith("X")
    checks = {{k: "same" for k in ("claim_strength", "quantifiers", "assumptions", "population", "measured_outcome", "time_horizon", "numerical_values", "uncertainty", "causal_vs_associative", "added_qualification")}}
    if changed:
        checks["claim_strength"] = "changed"
    answer = {{"candidate_id": data["candidate_id"], "proposed_wording": data["proposed_wording"], "original": data["original"],
              "verdict": "changed" if changed else "preserved", "checks": {{**checks, "hedges": []}}, "notes": "n", "auditor": {{"mode": "", "agent_id": ""}}}}
else:
    block = data["blocks"][-1]
    anchor = {{"block_id": block["block_id"], "quote": block["text"]}}
    answer = {{k: data[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256")}}
    answer.update(understanding="u", issues=[{{"id": "R1", "category": "other", "anchor": anchor, "reader_question": "q?", "consequence": "c", "severity": "minor", "confidence": "low"}}])
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(answer), "session_id": "sess0001-0000-0000",
                  "total_cost_usd": 0.01, "usage": {{"input_tokens": 1, "output_tokens": 1}}, "modelUsage": {{"claude-fake-1": {{"costUSD": 0.01}}}}, "permission_denials": []}}))
"""


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.claude = self.root / "claude"
        self.claude.write_text(FAKE_CLAUDE.format(python=sys.executable))
        os.chmod(self.claude, 0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_audit_compares_each_verdict_with_the_expected_one(self):
        cases = self.root / "cases.json"
        cases.write_text(json.dumps({"cases": [
            {"id": "T1", "type": "caught", "original": "Counts rose.", "proposed_wording": "X counts rose.", "expected": "changed"},
            {"id": "T2", "type": "slipped", "original": "Counts rose.", "proposed_wording": "Counts fell.", "expected": "changed"},
            {"id": "T3", "type": "passed", "original": "Counts rose.", "proposed_wording": "Counts increased.", "expected": "preserved"},
        ]}))
        out = self.root / "audit"
        code, text, err = self.main(["audit", "--model", "default", "--claude", str(self.claude), "--cases", str(cases), "--out", str(out), "--repeats", "2", "--jobs", "3"])
        self.assertEqual(code, 0, err)
        result = json.loads(text)
        self.assertEqual({c["id"]: (c["verdicts"], c["matched"]) for c in result["cases"]},
                         {"T1": (["changed", "changed"], 2), "T2": (["preserved", "preserved"], 0), "T3": (["preserved", "preserved"], 2)})
        self.assertEqual(result["expected_changed"], {"cases": 2, "runs": 4, "matched": 2})
        self.assertEqual(result["expected_preserved"], {"cases": 1, "runs": 2, "matched": 2})
        self.assertEqual((result["totals"]["calls"], result["model"], result["repeats"]), (6, "default", 2))
        self.assertEqual(json.loads((out / "results.json").read_text()), result)
        self.assertTrue((out / "T2-r2" / "events" / "T2.prompt.txt").is_file(), "every call keeps its prompt")
        code, text, err = self.main(["audit", "--model", "default", "--claude", str(self.claude), "--cases", str(cases), "--out", str(out)])
        self.assertEqual(code, 2, "an existing --out is refused")

    def test_audit_without_a_model_or_with_bad_cases_stops_before_any_call(self):
        code, text, err = self.main(["audit", "--out", str(self.root / "none")])
        self.assertEqual(code, 2)
        self.assertIn("--model", json.loads(err)["error"]["message"])
        self.assertFalse((self.root / "none").exists())
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"cases": [{"id": "T1", "type": "t", "original": "a", "proposed_wording": "b", "expected": "better"}]}))
        code, text, err = self.main(["audit", "--model", "default", "--claude", str(self.claude), "--cases", str(bad), "--out", str(self.root / "bad-out")])
        self.assertEqual(code, 2)
        self.assertIn("Case T1: expected must be changed or preserved.", json.loads(err)["error"]["message"])
        self.assertFalse((self.root / "bad-out").exists())

    def test_the_shipped_audit_cases_are_well_formed(self):
        cases = run.load_cases(EVALS / "audit-cases.json")
        self.assertEqual(len(cases), 15)
        self.assertEqual(sorted({c["expected"] for c in cases}), ["changed", "preserved"])
        self.assertTrue(all(c["original"] != c["proposed_wording"] for c in cases))

    def test_prefix_sends_a_reader_to_the_end_of_every_section_and_scores_the_issues(self):
        out = self.root / "prefix"
        code, text, err = self.main(["prefix", "--model", "default", "--claude", str(self.claude), "--out", str(out), "--repeats", "2"])
        self.assertEqual(code, 0, err)
        result = json.loads(text)
        index = rp.load_json(out / "index.json")
        self.assertEqual(result["checkpoints"], run.checkpoints(index))
        # Planting D05 deletes one Methods paragraph, so the planted copy has one block fewer than the clean manuscript.
        self.assertEqual(result["checkpoints"], ["B0006", "B0010", "B0012", "B0014", "B0020", "B0023"])
        self.assertEqual(result["planted"], [f"D0{n}" for n in range(1, 9)])
        # The stand-in reader quotes the last block it sees: D04 (B0006), D03 (B0010), D08 (B0020), two control blocks, and one block that is neither.
        for one in result["runs"]:
            self.assertEqual((one["readers"], one["readers_failed"], one["findings"], one["found"]), (6, [], 6, 3))
            self.assertEqual(sorted(one["found_by"]), ["D03", "D04", "D08"])
            self.assertEqual((one["false_alarms"], one["elsewhere"]), (["P04:R1", "P06:R1"], 1))
        self.assertEqual(result["found_in_runs"], {"D01": 0, "D02": 0, "D03": 2, "D04": 2, "D05": 0, "D06": 0, "D07": 0, "D08": 2})
        self.assertEqual((result["totals"]["calls"], result["complete_runs"]), (12, 2))

    def test_a_run_in_which_every_call_fails_reports_the_failure_not_a_score(self):
        broken = self.root / "claude-expired"
        broken.write_text(f"#!{sys.executable}\nimport sys\nprint('Failed to authenticate: session expired')\nsys.exit(1)\n")
        os.chmod(broken, 0o755)
        for command in ("audit", "prefix"):
            with self.subTest(command=command):
                out = self.root / f"failed-{command}"
                code, text, err = self.main([command, "--model", "default", "--claude", str(broken), "--out", str(out)])
                self.assertEqual(code, 2)
                error = json.loads(err)["error"]
                self.assertEqual(error["code"], "no_call_succeeded")
                self.assertIn("Every call failed, so there is nothing to score.", error["message"])
                self.assertIn("Failed to authenticate: session expired", error["message"])
                self.assertFalse((out / "results.json").exists())


if __name__ == "__main__":
    unittest.main()
