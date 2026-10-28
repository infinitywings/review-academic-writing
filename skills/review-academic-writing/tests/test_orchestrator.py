import contextlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


orch = load("orchestrator", PACKAGE / "server" / "orchestrator.py")
rp, rs = orch.rp, orch.rs

TEXT = """# Measuring agreement between two records

We measured how often two independent records agreed. Each record was produced by a separate observer.

Agreement fell sharply when the MSW exceeded two seconds, so we restricted the analysis to pairs within that window.

We call the time between consecutive measurements the Measurement Separation Window (MSW). We set the MSW to two seconds.

The restricted analysis included 412 pairs. Agreement in this set was 91 percent.
"""
INCLUDED = ["B0002", "B0003", "B0004", "B0005"]
DEFINITION = {"block_id": "B0004", "quote": "We call the time between consecutive measurements the Measurement Separation Window (MSW)."}
CRITICAL = {"block_id": "B0003", "quote": "the MSW exceeded two seconds"}
ORIGINAL = {"block_id": "B0003", "quote": "Agreement fell sharply when the MSW exceeded two seconds"}
WORDING = "Agreement fell sharply when consecutive measurements were more than two seconds apart."
RATE_ORIGINAL = {"block_id": "B0005", "quote": "Agreement in this set was 91 percent."}
RATE_WORDING = "Agreement in this set was 91 percent, which exceeds the unrestricted rate."
RATE_REVISED = "Agreement in this set reached 91 percent."
API_KEY = "sk-test-secret-0000"
STEP = re.compile(r"^Editor step: (\w+)(?: \(batch (\d+) of (\d+))?")
JOURNAL_FILES = ["00-start.json", "01-reading.notes.json", "01-reading.receipt.json", "02-checks.notes.json", "02-checks.receipt.json",
                 "03-reconcile.notes.json", "03-reconcile.receipt.json", "04-decisions.notes.json", "04-decisions.receipt.json",
                 "05-repairs.notes.json", "05-repairs.receipt.json", "06-ready.json"]


def A(block_id, quote):
    return {"block_id": block_id, "quote": quote}


def completion(text, number):
    body = {"id": f"chatcmpl-{number:04d}", "object": "chat.completion", "created": 1700000000, "model": "fake-llm",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
    return json.dumps(body).encode("utf-8")


class FakeLLM:
    """One chat-completions endpoint that plays editor, reader, and auditor from the prompt it receives."""

    def __init__(self):
        self.requests = []
        self.editor_calls = []  # (step, batch, attempt)
        self.break_step = None
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                server.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                text = server.answer(body["messages"][0]["content"], body["messages"][1]["content"])
                payload = completion(text, len(server.requests))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
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

    # ------------------------------------------------------------ routing

    def answer(self, system, user):
        if system.startswith("You are the editor"):
            return self.editor(user)
        if system.startswith("You are the auditor"):
            return self.auditor(json.loads(user.rsplit("\n\n", 1)[1]))
        packet = json.loads(user.rsplit("\n\n", 1)[1])
        if user.startswith("Act as the target academic reader"):
            return self.prefix_reader(packet)
        if "branch packet" in user[:200]:
            return self.branch_reader(packet)
        return self.masked_reader(packet)

    def editor(self, user):
        match = STEP.match(user)
        step, batch = match.group(1), int(match.group(2) or 1)
        attempt = 1 + sum(1 for s, b, _ in self.editor_calls if (s, b) == (step, batch))
        self.editor_calls.append((step, batch, attempt))
        data = json.loads(user.rsplit("\n=== INPUT (one JSON object) ===\n", 1)[1])
        if step == self.break_step:
            return "not json at all"
        result = getattr(self, "step_" + step)(data, batch, attempt)
        return result if isinstance(result, str) else json.dumps(result)

    # ------------------------------------------------------------ editor steps

    def step_scope(self, data, batch, attempt):
        return {"task": "review", "included_block_ids": INCLUDED, "omissions": [{"block_id": "B0001", "reason": "title"}],
                "reading_path": "linear", "extraction_notes": data["extraction_notes"]}

    def step_reading(self, data, batch, attempt):
        if batch == 1 and attempt == 1:
            return "Sure, here are the notes:"  # not a JSON object
        rows = [{"anchor": A(b["block_id"], b["text"][:20]), "paraphrase": "It says " + b["text"][:30], "role": "reports",
                 "required_context": [], "new_information": [b["text"][:15]], "open_questions": []} for b in data["blocks"]]
        if batch == 1 and attempt == 2:
            rows = rows[:1]  # drops B0003: the helper refuses the coverage
        return {"summary": f"batch {batch}", "paragraphs": rows}

    def step_checks(self, data, batch, attempt):
        claim_check = {"claim_strength": "none", "counterexample": "no rule to test", "citation_check": "no citation"}
        passages = [{"block_id": b["block_id"], "structure_check": "s", "wording_check": "w", "claim_check": claim_check, "evidence": [A(b["block_id"], b["text"][:15])]} for b in data["blocks"]]
        if batch == 1:
            return {"summary": "first half", "no_concepts_reason": "", "no_candidate": [],
                    "concepts": [{"concept_id": "x", "term": "MSW", "first_mention": CRITICAL, "earliest_usable_explanation": DEFINITION, "first_critical_use": CRITICAL, "assessment": "defined after its first critical use"}],
                    "passages": passages,
                    "checkpoint_plan": [{"id": "x", "anchor": CRITICAL, "mode": "bounded-request", "reason": "first critical use of MSW; selected with the full text"}],
                    "candidates": [{"id": "a", "anchor": CRITICAL, "question": "Is MSW defined first?", "evidence": [DEFINITION]},
                                   {"id": "b", "anchor": A("B0002", "a separate observer"), "question": "Who observed?", "evidence": []},
                                   {"id": "c", "anchor": A("B0003", "MSW"), "question": "Same symptom as the first?", "evidence": []}],
                    "branch_question": {"question": "What does MSW stand for?", "reason": "tests whether the abbreviation was usable", "after_block_id": "B0003"}}
        return {"summary": "second half", "concepts": [], "no_concepts_reason": "none questioned here", "no_candidate": [], "passages": passages, "checkpoint_plan": [],
                "candidates": [{"id": "d", "anchor": A("B0005", "412 pairs"), "question": "How were pairs counted?", "evidence": []}], "branch_question": None}

    def step_reconcile(self, data, batch, attempt):
        limitation = "bounded stateless request to the fake endpoint; isolation by request content, not by sandbox"
        return {"summary": "one prefix checkpoint, one masked event, one branch",
                "checkpoints": [{"id": "K01", "result": "asked what MSW is", "limitation": limitation},
                                {"id": "M01", "record_sequence": 2, "anchor": CRITICAL, "result": "carried an undefined MSW", "limitation": limitation}],
                "branches": [{"id": "R01", "result": "undetermined at event 2", "limitation": "question chosen with full text"}],
                "observations": [{"id": "O01", "source_id": "K01", "reader_question": "What is the MSW?", "classification": "absent_at_critical_use",
                                  "editor_check": "B0004 defines it after B0003.", "evidence": [DEFINITION], "candidate_id": "C01"},
                                 {"id": "O02", "source_id": "R01", "reader_question": "What does MSW stand for?", "classification": "adequate",
                                  "editor_check": "the threshold alone was usable", "evidence": [], "candidate_id": None}],
                "candidates": [{"id": data["next_candidate_id"], "anchor": A("B0005", "91 percent"), "question": "Is 91 percent compared with anything?", "evidence": []}]}

    def step_decisions(self, data, batch, attempt):
        common = {"verification": "v", "countercheck": "c", "evidence": []}
        return {"summary": "five decisions", "decisions": [
            {**common, "candidate_id": "C01", "status": "accepted", "evidence": [DEFINITION], "category": "late_prerequisite", "diagnosis": "MSW is used in B0003 before B0004 defines it.", "severity": "major", "confidence": "high"},
            {**common, "candidate_id": "C02", "status": "rejected"},
            {**common, "candidate_id": "C03", "status": "merged", "merged_into": "C01"},
            {**common, "candidate_id": "C04", "status": "needs_author", "category": "unsupported_claim", "diagnosis": "The pair count has no stated source.", "severity": "minor", "confidence": "medium"},
            {**common, "candidate_id": "C05", "status": "accepted", "category": "unsupported_claim", "diagnosis": "The agreement rate has no comparison.", "severity": "minor", "confidence": "medium"}]}

    def step_repairs(self, data, batch, attempt):
        if "audit_feedback" in data:
            assert [f["candidate_id"] for f in data["audit_feedback"]] == ["C05"]
            return {"summary": "revised after audit", "repairs": [
                {"candidate_id": "C05", "kind": "edit", "edit_scope": "term", "action": "State the comparison rate in B0005.", "proposed_wording": RATE_REVISED, "original": [RATE_ORIGINAL], "basis": [],
                 "scientific_fidelity": "rate preserved", "new_terms_check": "none", "affected_block_ids": ["B0005"], "author_question": None}]}
        return {"summary": "three repairs", "repairs": [
            {"candidate_id": "C01", "kind": "move", "action": "Move the definition ahead of B0003.", "proposed_wording": WORDING, "original": [ORIGINAL],
             "basis": [{"fact": "MSW is the time between consecutive measurements", "location": "B0004"}], "scientific_fidelity": "threshold preserved", "new_terms_check": "drops MSW",
             "affected_block_ids": ["B0003", "B0004"], "author_question": None},
            {"candidate_id": "C04", "kind": "author_question", "action": "Ask the author how the 412 pairs in B0005 were counted.", "proposed_wording": None, "original": [], "basis": [],
             "scientific_fidelity": "no change", "new_terms_check": "none", "affected_block_ids": ["B0005"], "author_question": "How were the 412 pairs selected?"},
            {"candidate_id": "C05", "kind": "edit", "edit_scope": "apposition", "action": "State the comparison rate in B0005.", "proposed_wording": RATE_WORDING, "original": [RATE_ORIGINAL], "basis": [],
             "scientific_fidelity": "rate preserved", "new_terms_check": "none", "affected_block_ids": ["B0005"], "author_question": None}]}

    def step_review(self, data, batch, attempt):
        return {"summary": "Three findings on a four-block excerpt; one reader checkpoint, one masked run.", "issue_order": ["C01", "C05", "C04"]}

    def step_report(self, data, batch, attempt):
        return {"report_markdown": "# Academic writing review\n\n## Scope and reader\n\n- " + data["report_language"] + "\n"}

    # ------------------------------------------------------------ readers and auditor

    def prefix_reader(self, packet):
        observation = {k: packet[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256")}
        observation.update({"understanding": "Agreement fell when something called MSW was large.",
                            "issues": [{"id": "R1", "category": "unavailable_prerequisite", "anchor": A("B0003", "the MSW"), "reader_question": "What is the MSW?",
                                        "consequence": "The threshold cannot be interpreted.", "severity": "major", "confidence": "high"}]})
        return json.dumps(observation)

    def masked_reader(self, packet):
        seq = packet["sequence"]
        ids = [m["id"] for m in packet["materials"]]
        notes = list(packet["carry_memory"]["notes"]) + [{"id": f"n{seq}", "kind": "established", "text": f"Window {seq} read.", "sources": ids, "since": seq}]
        return json.dumps({"understanding": f"window {seq}", "understood": [{"claim": "Records were compared.", "basis": "explicit", "anchor": {"source_id": ids[0], "quote": ""}}],
                           "uncertain": [{"question": "What is the MSW?", "needed_now": True, "anchor": {"source_id": ids[0], "quote": ""}}] if seq == 2 else [],
                           "resolved": [], "next_action": {"kind": "advance", "target": None, "reason": "go on"},
                           "carry_memory": {"gist": f"up to window {seq}", "notes": notes, "dropped": []}, "source_recognition": {"recognized": False, "description": ""}})

    def branch_reader(self, packet):
        return json.dumps({"answer": "Only a threshold is given.", "support": "undetermined", "anchors": [{"source_id": "B0003", "quote": "the MSW exceeded two seconds"}],
                           "inference": "", "background_used": "", "undetermined": "what the MSW measures", "source_recognition": {"recognized": False, "description": ""}})

    def auditor(self, request):
        checks = {**{k: "same" for k in rs.AUDIT_CHECKS}, "hedges": [{"term": t, "status": "kept"} for t in request.get("hedges_in_original", [])]}
        verdict = "preserved"
        notes = "No property changed."
        if request["candidate_id"] == "C05":
            checks["claim_strength"] = "changed"
            verdict, notes = "changed", "The wording asserts a comparison the original does not make."
        return json.dumps({"candidate_id": request["candidate_id"], "proposed_wording": request["proposed_wording"], "original": request["original"], "verdict": verdict,
                           "checks": checks, "notes": notes, "auditor": {"mode": "bounded-request", "agent_id": "filled in by the controller"}})


HEAVY_WORDING = "The agreement rate observed within this restricted set of record pairs reached a value of 91 percent overall."


class HeavyEditLLM(FakeLLM):
    """C05's wording passes the audit but replaces more than half of the original's words, and the retry sends the same wording again."""

    def __init__(self):
        super().__init__()
        self.edit_feedback = []

    def step_repairs(self, data, batch, attempt):
        assert "audit_feedback" not in data, "every audit in this scenario is preserved"
        rows = super().step_repairs(data, batch, attempt)
        if "edit_feedback" in data:
            self.edit_feedback.append(data["edit_feedback"])
            assert [r["decision"]["candidate_id"] for r in data["retained"]] == ["C05"]
            return {"summary": "the same wording, still without a reason", "repairs": [
                {"candidate_id": "C05", "kind": "edit", "edit_scope": "apposition", "action": "State the comparison rate in B0005.", "proposed_wording": HEAVY_WORDING, "original": [RATE_ORIGINAL], "basis": [],
                 "scientific_fidelity": "rate preserved", "new_terms_check": "none", "affected_block_ids": ["B0005"], "author_question": None}]}
        for row in rows["repairs"]:
            if row["candidate_id"] == "C05":
                row["proposed_wording"] = HEAVY_WORDING
        return rows

    def auditor(self, request):
        checks = {**{k: "same" for k in rs.AUDIT_CHECKS}, "hedges": [{"term": t, "status": "kept"} for t in request.get("hedges_in_original", [])]}
        return json.dumps({"candidate_id": request["candidate_id"], "proposed_wording": request["proposed_wording"], "original": request["original"], "verdict": "preserved",
                           "checks": checks, "notes": "No property changed.", "auditor": {"mode": "bounded-request", "agent_id": "filled in by the controller"}})


class HeavyAndChangedLLM(FakeLLM):
    """C05's first wording is both heavy and audited as changed; the retry gives a light wording the auditor preserves."""

    def __init__(self):
        super().__init__()
        self.retry_inputs = []

    def step_repairs(self, data, batch, attempt):
        if "edit_feedback" in data or "audit_feedback" in data:
            self.retry_inputs.append(data)
            return {"summary": "narrowed to one word", "repairs": [
                {"candidate_id": "C05", "kind": "edit", "edit_scope": "term", "action": "State the comparison rate in B0005.", "proposed_wording": RATE_REVISED, "original": [RATE_ORIGINAL], "basis": [],
                 "scientific_fidelity": "rate preserved", "new_terms_check": "none", "affected_block_ids": ["B0005"], "author_question": None}]}
        rows = super().step_repairs(data, batch, attempt)
        for row in rows["repairs"]:
            if row["candidate_id"] == "C05":
                row["proposed_wording"] = HEAVY_WORDING
        return rows

    def auditor(self, request):
        checks = {**{k: "same" for k in rs.AUDIT_CHECKS}, "hedges": [{"term": t, "status": "kept"} for t in request.get("hedges_in_original", [])]}
        verdict, notes = "preserved", "No property changed."
        if request["proposed_wording"] == HEAVY_WORDING:
            checks["added_qualification"] = "changed"
            verdict, notes = "changed", "The wording adds 'overall' and 'record pairs' with no basis."
        return json.dumps({"candidate_id": request["candidate_id"], "proposed_wording": request["proposed_wording"], "original": request["original"], "verdict": verdict,
                           "checks": checks, "notes": notes, "auditor": {"mode": "bounded-request", "agent_id": "filled in by the controller"}})


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.old_key = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = API_KEY
        self.config = self.root / "llm.json"
        self.use_server(FakeLLM())
        self.manuscript = self.root / "paper.md"
        self.manuscript.write_text(TEXT, encoding="utf-8")
        self.out = self.root / "run"

    def tearDown(self):
        self.server.close()
        if self.old_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = self.old_key
        self.tmp.cleanup()

    def use_server(self, server):
        if getattr(self, "server", None) is not None:
            self.server.close()
        self.server = server
        self.config.write_text(json.dumps({"base_url": self.server.base_url, "model": "fake-llm", "api_key_env": "LLM_API_KEY", "temperature": 0,
                                           "max_tokens": 4096, "json_mode": True, "timeout_seconds": 30, "retry_backoff_seconds": 0.01}))

    def run_pipeline(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = orch.main(["run", "--manuscript", str(self.manuscript), "--out", str(self.out), "--config", str(self.config),
                              "--batch-blocks", "2", "--masked-windows", "2", "--masked-budget", "20", "--prefix-checkpoints", "2", *extra])
        return code, out.getvalue(), err.getvalue()

    def journal_names(self):
        return sorted(p.name for p in (self.out / "journal").iterdir())

    def log_events(self):
        return [json.loads(line) for line in (self.out / "log.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_pipeline_runs_end_to_end_on_the_fake_endpoint(self):
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.journal_names(), JOURNAL_FILES)
        self.assertIn("review: 3 issues passed check-review and check-final", out)
        # The checks receipt exists before any reader event file is created.
        receipt = self.out / "journal" / "02-checks.receipt.json"
        reader_files = [p for p in list((self.out / "readers").rglob("*")) + list((self.out / "masked").rglob("*")) + list((self.out / "audits").rglob("*")) if p.is_file()]
        self.assertTrue(reader_files)
        self.assertLessEqual(receipt.stat().st_mtime_ns, min(p.stat().st_mtime_ns for p in reader_files))
        events = [e["event"] for e in self.log_events()]
        self.assertLess(events.index("checks_frozen"), events.index("reader_dispatch"))
        self.assertLess(events.index("stage_recorded"), events.index("reader_dispatch"))
        recorded = [e["stage"] for e in self.log_events() if e["event"] == "stage_recorded"]
        self.assertEqual(recorded, ["scope", "reading", "checks", "reconcile", "decisions", "repairs"])
        self.assertLess(events.index("reader_done"), events.index("ready"))
        # Retry path: the malformed reading response and the helper rejection both come back in the next prompt.
        second = (self.out / "editor" / "01-reading-batch01-b.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("Correction note from the controller", second.split("\n")[2])
        self.assertIn("The response was not one JSON object (JSONDecodeError", second)
        third = (self.out / "editor" / "01-reading-batch01-c.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("Attempt 2: The helper rejected it: Reading notes must cover each included block exactly once.", third)
        self.assertFalse((self.out / "editor" / "01-reading-batch01-d.prompt.txt").exists())
        self.assertTrue((self.out / "editor" / "01-reading-batch02.response.json").exists())
        reading = json.loads((self.out / "journal" / "01-reading.notes.json").read_text(encoding="utf-8"))
        self.assertEqual([p["anchor"]["block_id"] for p in reading["paragraphs"]], INCLUDED)
        self.assertEqual(reading["summary"], "batch 1 batch 2")
        # Batches were merged and renumbered.
        checks = json.loads((self.out / "journal" / "02-checks.notes.json").read_text(encoding="utf-8"))
        self.assertEqual([c["id"] for c in checks["candidates"]], ["C01", "C02", "C03", "C04"])
        self.assertEqual([c["concept_id"] for c in checks["concepts"]], ["K01"])
        self.assertEqual([p["id"] for p in checks["checkpoint_plan"]], ["P01"])
        self.assertEqual([p["block_id"] for p in checks["passages"]], INCLUDED)
        self.assertNotIn("branch_question", checks)
        self.assertNotIn("no_concepts_reason", checks)
        self.assertEqual(json.loads((self.out / "branch-question.json").read_text())["question"], "What does MSW stand for?")
        # Readers: one prefix checkpoint, a two-window masked run over B0002 and B0003, one branch from event 2.
        self.assertTrue((self.out / "readers" / "P01" / "P01.observation.json").exists())
        case = json.loads((self.out / "masked" / "case.json").read_text())
        self.assertEqual(case["windows"], [["B0002"], ["B0003"]])
        self.assertEqual(sorted(p.name for p in (self.out / "masked" / "reader" / "records").iterdir()), ["000001", "000002"])
        branch = json.loads((self.out / "masked" / "reader" / "branches" / "000001" / "packet.json").read_text())
        self.assertEqual(branch["branched_from_sequence"], 2)
        reconcile = json.loads((self.out / "journal" / "03-reconcile.notes.json").read_text(encoding="utf-8"))
        self.assertEqual([(c["id"], c["mode"], c.get("plan_id"), c["figure_policy"]) for c in reconcile["checkpoints"]],
                         [("K01", "bounded-request", "P01", "text-only"), ("M01", "masked-sequential", None, "strict-prefix")])
        self.assertEqual(reconcile["checkpoints"][0]["packet_path"], str(self.out / "readers" / "P01" / "P01.packet.json"))
        self.assertEqual(reconcile["branches"][0]["branch_sequence"], 1)
        self.assertEqual([c["id"] for c in reconcile["candidates"]], ["C05"])
        receipt3 = json.loads((self.out / "journal" / "03-reconcile.receipt.json").read_text())
        self.assertEqual(len(receipt3["attachments"]), 2 + 4 + 4)
        # Audit gate: C01 kept its wording; C05 failed twice and became an author question.
        repairs = {r["candidate_id"]: r for r in json.loads((self.out / "journal" / "05-repairs.notes.json").read_text(encoding="utf-8"))["repairs"]}
        self.assertEqual(repairs["C01"]["audit_path"], str(self.out / "audits" / "C01.audit.json"))
        self.assertEqual(repairs["C01"]["proposed_wording"], WORDING)
        self.assertEqual((repairs["C05"]["kind"], repairs["C05"]["proposed_wording"], repairs["C05"]["audit_path"]), ("author_question", None, None))
        self.assertIn("independent fidelity audit", repairs["C05"]["author_question"])
        self.assertIn("claim_strength", repairs["C05"]["author_question"])
        self.assertIn(RATE_REVISED, repairs["C05"]["author_question"])
        self.assertEqual(sorted(p.name for p in (self.out / "audits").glob("*.audit.json")), ["C01.audit.json", "C05-2.audit.json", "C05.audit.json"])
        retry_prompt = (self.out / "editor" / "05-repairs-audit.prompt.txt").read_text(encoding="utf-8")
        self.assertIn('"audit_feedback"', retry_prompt)
        self.assertIn('"edit_feedback"', retry_prompt, "RATE_WORDING is both audited as changed and heavy, so it is in both lists")
        self.assertIn(RATE_WORDING, retry_prompt)
        self.assertIn("The wording asserts a comparison the original does not make.", retry_prompt)
        self.assertNotIn("basis", json.dumps(repairs))
        # Every audit request named the hedges found in the original (none in these sentences), and every audit file carries its round.
        audit_requests = [json.loads(r["body"]["messages"][1]["content"].rsplit("\n\n", 1)[1]) for r in self.server.requests if r["body"]["messages"][0]["content"].startswith("You are the auditor")]
        self.assertEqual([(r["candidate_id"], r["hedges_in_original"]) for r in audit_requests], [("C01", []), ("C05", []), ("C05", [])])
        self.assertEqual([r["proposed_wording"] for r in audit_requests], [WORDING, RATE_WORDING, RATE_REVISED])
        self.assertEqual(json.loads((self.out / "audits" / "C01.audit.json").read_text())["audit_round"], 1)
        self.assertEqual(json.loads((self.out / "audits" / "C05.audit.json").read_text())["audit_round"], 1)
        self.assertEqual(json.loads((self.out / "audits" / "C05-2.audit.json").read_text())["audit_round"], 2)
        # Lexical comparison files: one per audited wording; the recorded repair with wording points at its own.
        self.assertEqual(sorted(p.name for p in (self.out / "audits").glob("*.features.json")), ["C01.features.json", "C05-2.features.json", "C05.features.json"])
        self.assertEqual(repairs["C01"]["compare_path"], str(self.out / "audits" / "C01.features.json"))
        self.assertIsNone(repairs["C05"]["compare_path"])
        self.assertIsNone(repairs["C04"]["compare_path"])
        features = json.loads((self.out / "audits" / "C01.features.json").read_text())
        self.assertEqual((features["command"], features["lexical_only"], features["comparisons"][0]["candidate_id"], features["comparisons"][0]["proposed_wording"]), ("compare", True, "C01", WORDING))
        self.assertEqual(features["comparisons"][0]["lexical"]["edit_weight"], "heavy")  # a move: no heavy_edit_reason needed
        self.assertEqual([e["candidate_id"] for e in self.log_events() if e["event"] == "compare"], ["C01", "C05", "C05"])
        # Final review and report.
        review = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        self.assertEqual([(i["id"], i["candidate_id"], i["decision_status"]) for i in review["issues"]], [("W01", "C01", "accepted"), ("W02", "C05", "accepted"), ("W03", "C04", "needs_author")])
        self.assertEqual(review["issues"][0]["proposed_wording"], WORDING)
        self.assertEqual(review["issues"][2]["author_question"], "How were the 412 pairs selected?")
        self.assertEqual(review["concept_ledger"][0]["term"], "MSW")
        self.assertEqual(rs.check_final(self.out / "journal", rp.load_json(self.out / "index.json"), review)["issue_count"], 3)
        self.assertTrue((self.out / "report.md").read_text(encoding="utf-8").startswith("# Academic writing review"))
        # Summary with literal counts.
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["totals"]["editor"]["calls"], 13)
        self.assertEqual(summary["totals"]["readers"]["calls"], 7)
        self.assertEqual(summary["totals"]["readers"]["by_kind"], {"prefix": 1, "masked": 2, "branch": 1, "audit": 3})
        self.assertEqual(summary["totals"]["calls"], 20)
        self.assertEqual(summary["totals"]["retries"], 2)
        self.assertEqual(summary["totals"]["editor"]["input_tokens"], 1300)
        self.assertEqual([c["label"] for c in summary["editor_calls"]], ["00-scope", "01-reading-batch01", "01-reading-batch01-b", "01-reading-batch01-c", "01-reading-batch02", "02-checks-batch01", "02-checks-batch02", "03-reconcile", "04-decisions", "05-repairs", "05-repairs-audit", "06-review", "07-report"])
        self.assertEqual([c["status"] for c in summary["editor_calls"]][:4], ["ok", "rejected", "rejected", "ok"])
        self.assertEqual(summary["stages"]["decisions"], {"status": "recorded", "accepted": 2, "needs_author": 1, "rejected": 1, "merged": 1})
        self.assertEqual(summary["stages"]["repairs"], {"status": "recorded", "repairs": 3, "with_wording": 1, "author_questions": 2, "audits": 3, "converted_by_audit": ["C05"], "withheld_heavy_edits": []})
        self.assertEqual(summary["stages"]["repairs"]["withheld_heavy_edits"], [])
        self.assertNotIn("edit_withheld", [e["event"] for e in self.log_events()])
        self.assertEqual(summary["stages"]["review"]["issues"], 3)
        self.assertEqual([(a["label"], a["verdict"], a["accepted"]) for a in summary["audits"]], [("C01", "preserved", True), ("C05", "changed", False), ("C05-2", "changed", False)])
        self.assertEqual(len(self.server.requests), 20)
        # Prompts: the system prompt names the role; editor prompts embed the reference sections; no file holds the key.
        self.assertEqual(self.server.requests[0]["body"]["messages"][0]["content"], orch.llm.EDITOR_SYSTEM_PROMPT)
        first = (self.out / "editor" / "00-scope.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("## 1. Record the source, reader, and scope", first)
        self.assertIn('"task": "review",', first)
        for path in [p for p in self.out.rglob("*") if p.is_file()]:
            self.assertNotIn(API_KEY.encode(), path.read_bytes(), path.name)

    def test_heavy_edit_without_a_reason_is_withheld_after_one_revision(self):
        self.use_server(HeavyEditLLM())
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.journal_names(), JOURNAL_FILES)
        self.assertIn("repair C05: heavy edit without a reason after revision; wording withheld", out)
        # The retry carried edit_feedback for C05 only, with the heavy weight; no audit feedback, since every audit was preserved.
        self.assertEqual(len(self.server.edit_feedback), 1)
        feedback = self.server.edit_feedback[0]
        self.assertEqual([(f["candidate_id"], f["proposed_wording"], f["edit_weight"]) for f in feedback], [("C05", HEAVY_WORDING, "heavy")])
        self.assertGreater(feedback[0]["edit_ratio"], 0.5)
        self.assertIn("heavy_edit_reason", feedback[0]["note"])
        retry_prompt = (self.out / "editor" / "05-repairs-audit.prompt.txt").read_text(encoding="utf-8")
        self.assertIn('"edit_feedback"', retry_prompt)
        self.assertNotIn('"audit_feedback"', retry_prompt)
        self.assertIn(HEAVY_WORDING, retry_prompt)
        # Recorded repairs: C05 keeps its action and kind but no wording; C01 keeps everything.
        repairs = {r["candidate_id"]: r for r in json.loads((self.out / "journal" / "05-repairs.notes.json").read_text(encoding="utf-8"))["repairs"]}
        c05 = repairs["C05"]
        self.assertEqual((c05["kind"], c05["edit_scope"], c05["proposed_wording"], c05["compare_path"], c05["audit_path"], c05["author_question"]), ("edit", "apposition", None, None, None, None))
        self.assertTrue(c05["wording_withheld_reason"].startswith("withheld:"), c05["wording_withheld_reason"])
        self.assertIn("no stated reason after one revision", c05["wording_withheld_reason"])
        self.assertEqual(c05["action"], "State the comparison rate in B0005.")
        self.assertNotIn("original", c05)
        self.assertNotIn("basis", c05)
        self.assertEqual((repairs["C01"]["proposed_wording"], repairs["C01"]["audit_path"], repairs["C01"]["compare_path"]),
                         (WORDING, str(self.out / "audits" / "C01.audit.json"), str(self.out / "audits" / "C01.features.json")))
        self.assertEqual((repairs["C04"]["kind"], repairs["C04"]["proposed_wording"], repairs["C04"]["compare_path"]), ("author_question", None, None))
        # Both C05 wordings were audited (preserved) and compared (heavy); the files remain for the record.
        self.assertEqual(sorted(p.name for p in (self.out / "audits").glob("*.audit.json")), ["C01.audit.json", "C05-2.audit.json", "C05.audit.json"])
        self.assertEqual(sorted(p.name for p in (self.out / "audits").glob("*.features.json")), ["C01.features.json", "C05-2.features.json", "C05.features.json"])
        second = json.loads((self.out / "audits" / "C05-2.audit.json").read_text())
        self.assertEqual((second["audit_round"], second["verdict"], second["proposed_wording"]), (2, "preserved", HEAVY_WORDING))
        self.assertEqual(json.loads((self.out / "audits" / "C05.audit.json").read_text())["audit_round"], 1)
        for name in ("C05.features.json", "C05-2.features.json"):
            row = json.loads((self.out / "audits" / name).read_text())["comparisons"][0]
            self.assertEqual((row["candidate_id"], row["proposed_wording"], row["lexical"]["edit_weight"]), ("C05", HEAVY_WORDING, "heavy"), name)
        events = self.log_events()
        self.assertEqual([e["candidate_id"] for e in events if e["event"] == "edit_withheld"], ["C05"])
        self.assertEqual([(e["label"], e["edit_weight"]) for e in events if e["event"] == "compare"], [("C01", "heavy"), ("C05", "heavy"), ("C05-2", "heavy")])
        self.assertNotIn("audit_converted", [e["event"] for e in events])
        # The journal validates through to the final review; the withheld repair reaches the review without wording.
        review = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        self.assertEqual([(i["id"], i["candidate_id"], i["decision_status"], i["proposed_wording"]) for i in review["issues"]],
                         [("W01", "C01", "accepted", WORDING), ("W02", "C05", "accepted", None), ("W03", "C04", "needs_author", None)])
        self.assertNotIn("author_question", review["issues"][1])
        self.assertEqual(review["issues"][1]["action"], "State the comparison rate in B0005.")
        self.assertEqual(rs.check_final(self.out / "journal", rp.load_json(self.out / "index.json"), review)["issue_count"], 3)
        self.assertTrue((self.out / "report.md").exists())
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["stages"]["repairs"], {"status": "recorded", "repairs": 3, "with_wording": 1, "author_questions": 1, "audits": 3, "converted_by_audit": [], "withheld_heavy_edits": ["C05"]})
        self.assertEqual([(a["label"], a["verdict"], a["accepted"]) for a in summary["audits"]], [("C01", "preserved", True), ("C05", "preserved", True), ("C05-2", "preserved", True)])
        self.assertEqual([c["label"] for c in summary["editor_calls"]], ["00-scope", "01-reading-batch01", "01-reading-batch01-b", "01-reading-batch01-c", "01-reading-batch02", "02-checks-batch01", "02-checks-batch02", "03-reconcile", "04-decisions", "05-repairs", "05-repairs-audit", "06-review", "07-report"])
        self.assertEqual(summary["totals"]["readers"]["by_kind"], {"prefix": 1, "masked": 2, "branch": 1, "audit": 3})
        self.assertEqual(summary["totals"]["calls"], 20)

    def test_a_wording_that_is_heavy_and_changed_gets_both_feedback_lists_and_a_light_retry_is_kept(self):
        self.use_server(HeavyAndChangedLLM())
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.journal_names(), JOURNAL_FILES)
        # One retry, whose input carried C05 in both lists.
        self.assertEqual(len(self.server.retry_inputs), 1)
        retry = self.server.retry_inputs[0]
        self.assertEqual([r["decision"]["candidate_id"] for r in retry["retained"]], ["C05"])
        self.assertEqual([(f["candidate_id"], f["audited_wording"], f["verdict"], f["changed"]) for f in retry["audit_feedback"]], [("C05", HEAVY_WORDING, "changed", ["added_qualification"])])
        self.assertIn("no basis", retry["audit_feedback"][0]["auditor_notes"])
        self.assertEqual([(f["candidate_id"], f["proposed_wording"], f["edit_weight"]) for f in retry["edit_feedback"]], [("C05", HEAVY_WORDING, "heavy")])
        retry_prompt = (self.out / "editor" / "05-repairs-audit.prompt.txt").read_text(encoding="utf-8")
        self.assertIn('"audit_feedback"', retry_prompt)
        self.assertIn('"edit_feedback"', retry_prompt)
        self.assertFalse((self.out / "editor" / "05-repairs-audit-b.prompt.txt").exists())
        # The light, preserved revision is recorded with its own audit and comparison; nothing withheld or converted.
        repairs = {r["candidate_id"]: r for r in json.loads((self.out / "journal" / "05-repairs.notes.json").read_text(encoding="utf-8"))["repairs"]}
        c05 = repairs["C05"]
        self.assertEqual((c05["kind"], c05["edit_scope"], c05["proposed_wording"], c05["author_question"]), ("edit", "term", RATE_REVISED, None))
        self.assertEqual((c05["audit_path"], c05["compare_path"]), (str(self.out / "audits" / "C05-2.audit.json"), str(self.out / "audits" / "C05-2.features.json")))
        self.assertNotIn("wording_withheld_reason", c05)
        self.assertEqual(sorted(p.name for p in (self.out / "audits").glob("*.audit.json")), ["C01.audit.json", "C05-2.audit.json", "C05.audit.json"])
        self.assertEqual(sorted(p.name for p in (self.out / "audits").glob("*.features.json")), ["C01.features.json", "C05-2.features.json", "C05.features.json"])
        first = json.loads((self.out / "audits" / "C05.audit.json").read_text())
        second = json.loads((self.out / "audits" / "C05-2.audit.json").read_text())
        self.assertEqual((first["audit_round"], first["verdict"], first["checks"]["added_qualification"]), (1, "changed", "changed"))
        self.assertEqual((second["audit_round"], second["verdict"], second["proposed_wording"]), (2, "preserved", RATE_REVISED))
        self.assertEqual(json.loads((self.out / "audits" / "C05.features.json").read_text())["comparisons"][0]["lexical"]["edit_weight"], "heavy")
        self.assertEqual(json.loads((self.out / "audits" / "C05-2.features.json").read_text())["comparisons"][0]["lexical"]["edit_weight"], "light")
        events = [e["event"] for e in self.log_events()]
        self.assertNotIn("edit_withheld", events)
        self.assertNotIn("audit_converted", events)
        review = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        self.assertEqual([(i["candidate_id"], i["decision_status"], i["proposed_wording"]) for i in review["issues"]], [("C01", "accepted", WORDING), ("C05", "accepted", RATE_REVISED), ("C04", "needs_author", None)])
        self.assertEqual(rs.check_final(self.out / "journal", rp.load_json(self.out / "index.json"), review)["issue_count"], 3)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["stages"]["repairs"], {"status": "recorded", "repairs": 3, "with_wording": 2, "author_questions": 1, "audits": 3, "converted_by_audit": [], "withheld_heavy_edits": []})
        self.assertEqual([(a["label"], a["verdict"], a["accepted"]) for a in summary["audits"]], [("C01", "preserved", True), ("C05", "changed", False), ("C05-2", "preserved", True)])
        self.assertEqual(summary["totals"]["readers"]["by_kind"], {"prefix": 1, "masked": 2, "branch": 1, "audit": 3})
        self.assertEqual(summary["totals"]["calls"], 20)

    def test_a_step_that_never_validates_stops_with_the_journal_intact(self):
        self.server.break_step = "decisions"
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 1, out + err)
        self.assertEqual(self.journal_names(), JOURNAL_FILES[:7])
        self.assertEqual(sorted(p.name for p in (self.out / "editor").glob("04-decisions*.prompt.txt")), ["04-decisions-b.prompt.txt", "04-decisions-c.prompt.txt", "04-decisions-d.prompt.txt", "04-decisions.prompt.txt"])
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["failure"]["stage"], "decisions")
        self.assertIn("4 attempts were not accepted", summary["failure"]["message"])
        self.assertIn("04-decisions-d.response.raw", summary["failure"]["fix"])
        self.assertEqual(summary["totals"]["retries"], 5)
        self.assertIn("STOPPED at decisions", out)
        self.assertEqual(self.log_events()[-1]["event"], "stopped")

    def test_reconcile_candidate_id_that_is_not_a_safe_file_name_is_rejected(self):
        original = self.server.step_reconcile

        def step_reconcile(data, batch, attempt):
            value = original(data, batch, attempt)
            if attempt == 1:
                value["candidates"][0]["id"] = "../C05"
            return value

        self.server.step_reconcile = step_reconcile
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        second = (self.out / "editor" / "03-reconcile-b.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("Each new candidate id must be letters, digits, '_' or '-' only", second)
        self.assertEqual([c["id"] for c in json.loads((self.out / "journal" / "03-reconcile.notes.json").read_text())["candidates"]], ["C05"])
        self.assertEqual(sorted(p.name for p in (self.out / "audits").glob("*.audit.json")), ["C01.audit.json", "C05-2.audit.json", "C05.audit.json"])
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["totals"]["retries"], 3)

    def test_render_quotes_reference_sections_verbatim(self):
        staged = (PACKAGE / "references" / "staged-review.md").read_text(encoding="utf-8")
        section = orch.section_of(staged, "## 2. Save reading notes")
        self.assertTrue(section.startswith("## 2. Save reading notes\n"))
        self.assertIn("review_steps.py record --journal run/journal --index run/index.json --stage reading", section)
        self.assertNotIn("## 3. Save checks", section)
        rendered = orch.render("reading", {"batch": 1, "batches": 1, "block_range": "B0002 to B0005", "input": {"k": "v"}})
        self.assertIn(section, rendered)
        self.assertIn((PACKAGE / "assets" / "notes" / "reading.json").read_text(encoding="utf-8").rstrip("\n"), rendered)
        self.assertTrue(rendered.endswith('{\n "k": "v"\n}'))
        rubric = (PACKAGE / "references" / "review-rubric.md").read_text(encoding="utf-8")
        paragraphs = orch.section_of(rubric, "## 3. Paragraph and section function")
        self.assertIn("### Proposals", paragraphs)
        self.assertNotIn("## 4. Definition versus evidence", paragraphs)
        with self.assertRaises(KeyError):
            orch.section_of(rubric, "## No such heading")
        for step in orch.STEPS:
            text = orch.render(step, {"batch": 1, "batches": 1, "block_range": "x", "input": {}})
            self.assertNotIn("{{", text, step)
            self.assertTrue(text.startswith(f"Editor step: {step}"), step)

    def test_existing_out_directory_is_refused_before_any_call(self):
        self.out.mkdir()
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(self.server.requests, [])

    def test_missing_key_variable_is_refused_before_the_run_directory_exists(self):
        del os.environ["LLM_API_KEY"]
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 2)
        self.assertIn("Environment variable LLM_API_KEY", err)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
