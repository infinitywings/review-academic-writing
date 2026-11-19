import contextlib
import http.server
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import subprocess
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


# MinerU and pdftotext stand-ins for server/review-paper.sh: the parse writes a Markdown edition with a word split by
# layout parsing, and the text layer holds the words whole.
FAKE_MINERU = """#!/bin/sh
printf '%s\\n' "$*" >> '{log}'
case "$1" in
  server) [ "$2" = status ] && printf '%s\\n' '{"running": true, "parse_server": {"local": {"mode": "managed", "healthy": true, "starting": false, "managed_running": true}}}'; exit 0 ;;
  parse) while [ $# -gt 0 ]; do [ "$1" = "-o" ] && cp '{parsed}' "$2"; shift; done; exit 0 ;;
esac
exit 1
"""
FAKE_PDFTOTEXT = """#!/bin/sh
cat '{layer}'
"""


def schema_errors(value, schema, path="$"):
    """Where `value` breaks `schema`, for the JSON Schema keywords the schemas sent by the server use."""
    if "anyOf" in schema:
        return [] if any(not schema_errors(value, branch, path) for branch in schema["anyOf"]) else [f"{path}: matches no anyOf branch"]
    if "const" in schema and value != schema["const"]:
        return [f"{path}: {value!r} is not {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} is not one of {schema['enum']}"]
    kinds = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    kind = schema.get("type")
    if kind is not None and (not isinstance(value, kinds[kind]) or (kind == "integer" and isinstance(value, bool))):
        return [f"{path}: is not {kind}"]
    errors = []
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            errors.append(f"{path}: {len(value)} items is out of range")
        for n, item in enumerate(value):
            errors += schema_errors(item, schema.get("items", {}), f"{path}[{n}]")
    elif kind == "object":
        errors += [f"{path}: missing {key}" for key in schema["required"] if key not in value]
        errors += [f"{path}: unexpected {key}" for key in value if key not in schema["properties"]]
        for key, item in value.items():
            if key in schema["properties"]:
                errors += schema_errors(item, schema["properties"][key], f"{path}.{key}")
    return errors


def schema_keywords(schema, path="$"):
    """(path, keyword) for every JSON Schema keyword in `schema`; property names and const or enum values are data, not keywords."""
    found = []
    for key, value in schema.items():
        found.append((path, key))
        if key == "properties":
            for name, sub in value.items():
                found += schema_keywords(sub, f"{path}.{name}")
        elif key == "items":
            found += schema_keywords(value, f"{path}[]")
        elif key == "anyOf":
            for n, sub in enumerate(value):
                found += schema_keywords(sub, f"{path}|{n}")
    return found


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
        if system.startswith("You are the checker"):
            return self.challenger(json.loads(user.rsplit("\n\n", 1)[1]))
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
                                  "editor_check": "the threshold alone was usable", "evidence": [], "candidate_id": None, "resolved_by": CRITICAL, "background": None}],
                "candidates": [{"id": data["next_candidate_id"], "anchor": A("B0005", "91 percent"), "question": "Is 91 percent compared with anything?", "evidence": []}]}

    def step_recheck(self, data, batch, attempt):
        return {"summary": f"pass {data['pass']} found nothing new", "candidates": []}

    def step_decisions(self, data, batch, attempt):
        common = {"verification": "v", "countercheck": "c", "evidence": []}
        disputed = {c["candidate_id"] for c in data["challenges"] or [] if c["disputed"]}
        answers = {"C05": "The sentence states the rate; nothing in B0005 says what it is compared with."}
        decisions = {"summary": "five decisions", "decisions": [
            {**common, "candidate_id": "C01", "status": "accepted", "evidence": [DEFINITION], "category": "late_prerequisite", "diagnosis": "MSW is used in B0003 before B0004 defines it.",
             "close_criterion": "The reader can say what the MSW measures on reaching the threshold in B0003.", "severity": "misleads", "confidence": "high"},
            {**common, "candidate_id": "C02", "status": "rejected"},
            {**common, "candidate_id": "C03", "status": "merged", "merged_into": "C01"},
            {**common, "candidate_id": "C04", "status": "needs_author", "category": "unsupported_claim", "diagnosis": "The pair count has no stated source.",
             "close_criterion": "The reader can say how the 412 pairs were selected.", "severity": "minor", "confidence": "medium"},
            {**common, "candidate_id": "C05", "status": "accepted", "category": "unsupported_claim", "diagnosis": "The agreement rate has no comparison.",
             "close_criterion": "The reader can name the rate that 91 percent is compared with.", "severity": "hinders", "confidence": "medium"}]}
        for row in decisions["decisions"]:
            if row["candidate_id"] in disputed and row["status"] in ("accepted", "needs_author"):
                row["challenge_response"] = answers[row["candidate_id"]]
        return decisions

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
        return {"summary": "Three findings on a four-block excerpt; one reader checkpoint, one masked run.",
                "scope_note": "All four paragraphs were read; one prefix reader, a two-window masked run, and one branch question were run.", "issue_order": ["C01", "C05", "C04"]}

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

    def challenger(self, request):
        """C01 is answered after its anchor (an order problem), C02 is not material, C03 repeats C01, C04 stands, and C05 is
        answered by the very sentence it is anchored in (a dispute the editor must answer)."""
        ids = [c["candidate_id"] for c in request["candidates"]]
        planned = {"C01": ("answered", A("B0004", "We call the time between consecutive measurements the Measurement Separation Window (MSW).")),
                   "C02": ("not_material", None), "C03": ("duplicate", None), "C05": ("answered", A("B0005", "Agreement in this set was 91 percent."))}
        rows = []
        for key in ids:
            verdict, answer = planned.get(key, ("stands", None))
            duplicate = "C01" if verdict == "duplicate" else None
            if verdict == "duplicate" and duplicate not in ids:
                verdict = "stands"
                duplicate = None
            rows.append({"candidate_id": key, "verdict": verdict, "answer": answer, "duplicate_of": duplicate, "reason": f"{key} checked against the manuscript."})
        return json.dumps({"challenges": rows})

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


class PrefixOnlyLLM(FakeLLM):
    """The editor of a run without a masked reader: its reconcile step cites the prefix checkpoint only."""

    def step_reconcile(self, data, batch, attempt):
        assert data["masked_run"] is None and data["branch"] is None
        value = super().step_reconcile(data, batch, attempt)
        return {**value, "checkpoints": value["checkpoints"][:1], "branches": [], "observations": value["observations"][:1]}


class AnchorCanonicalizationTests(unittest.TestCase):
    TEXT = ("# Residual networks\n\nWe compare 56-layer \u201cplain\u201d networks with the 34- layer\nbaseline.\n\n"
            "Error fell from 7.1\u20137.5 percent in the \ufb01nal run.\n\nWe tested the \u201ca\u201d model and the \u201ca\u201d model again.\n")

    def setUp(self):
        self.blocks = {b["block_id"]: b for b in rp.build_index(self.TEXT)["blocks"]}

    def test_straight_quotes_resolve_to_the_curly_block_text(self):
        response = {"anchor": A("B0002", '56-layer "plain" networks')}
        value, rewrites = orch.canonical_anchors(response, self.blocks)
        self.assertEqual(value, {"anchor": A("B0002", "56-layer \u201cplain\u201d networks")})
        self.assertEqual(rewrites, [{"block_id": "B0002", "quote": '56-layer "plain" networks', "replacement": "56-layer \u201cplain\u201d networks"}])
        self.assertEqual(response, {"anchor": A("B0002", '56-layer "plain" networks')})

    def test_repaired_extraction_spacing_resolves_to_the_block_text(self):
        value, _ = orch.canonical_anchors(A("B0002", "the 34-layer baseline."), self.blocks)
        self.assertEqual(value, A("B0002", "the 34- layer\nbaseline."))

    def test_ascii_hyphen_and_letters_resolve_to_an_en_dash_and_a_ligature(self):
        value, _ = orch.canonical_anchors(A("B0003", "7.1-7.5 percent in the final run"), self.blocks)
        self.assertEqual(value, A("B0003", "7.1\u20137.5 percent in the \ufb01nal run"))

    def test_ascii_hyphens_resolve_to_unicode_hyphens(self):
        blocks = {b["block_id"]: b for b in rp.build_index("# T\n\nA state\u2011of\u2010the\u2012art model.\n")["blocks"]}
        value, _ = orch.canonical_anchors(A("B0002", "state-of-the-art model"), blocks)
        self.assertEqual(value, A("B0002", "state\u2011of\u2010the\u2012art model"))

    def test_an_exact_quote_is_never_rewritten(self):
        response = [A("B0002", " networks with"), A("B0002", '"plain" networks')]
        value, rewrites = orch.canonical_anchors(response, self.blocks)
        self.assertEqual(value, [A("B0002", " networks with"), A("B0002", "\u201cplain\u201d networks")])
        self.assertIs(value[0]["quote"], response[0]["quote"])
        self.assertEqual([r["quote"] for r in rewrites], ['"plain" networks'])

    def test_a_quote_that_folds_to_two_spans_or_none_is_left_for_the_helper(self):
        response = [A("B0004", 'the "a" model'), A("B0004", 'the "b" model'), A("B0099", 'the "a" model'), A("B0004", 'We tested the "a"')]
        value, rewrites = orch.canonical_anchors(response, self.blocks)
        self.assertEqual(value, [A("B0004", 'the "a" model'), A("B0004", 'the "b" model'), A("B0099", 'the "a" model'), A("B0004", "We tested the \u201ca\u201d")])
        self.assertEqual([r["quote"] for r in rewrites], ['We tested the "a"'])

    def test_a_garbled_or_unmarked_formula_resolves_to_the_block_formula(self):
        blocks = {b["block_id"]: b for b in rp.build_index("# T\n\nWe need $R _ { 1 } , \\ldots , R _ { n }$ here, so $A \\therefore B$ holds. "
                                                          "The set $S ( I )$ survives if $R ,$ holds.\n")["blocks"]}
        dropped = A("B0002", "We need $R _ { 1 } ,  , R _ { n }$ here")  # \ldots is not a JSON escape, so the decoder left it out
        tabbed = A("B0002", "so $A \therefore B$ holds.")  # one backslash: JSON reads \t as a tab
        unmarked = A("B0002", "The set S(I) survives if R, holds.")
        value, _ = orch.canonical_anchors([dropped, tabbed, unmarked], blocks)
        self.assertEqual(value, [A("B0002", "We need $R _ { 1 } , \\ldots , R _ { n }$ here"), A("B0002", "so $A \\therefore B$ holds."),
                                 A("B0002", "The set $S ( I )$ survives if $R ,$ holds.")])

    def test_a_quote_that_ends_inside_a_formula_keeps_the_whole_formula(self):
        blocks = {b["block_id"]: b for b in rp.build_index("# T\n\nThe set of possibilities $S ( I )$ contains every case.\n")["blocks"]}
        value, _ = orch.canonical_anchors(A("B0002", "The set of possibilities S(I)"), blocks)
        self.assertEqual(value, A("B0002", "The set of possibilities $S ( I )$"))

    def test_anchors_at_every_depth_are_canonicalized(self):
        response = {"summary": 'kept "as is"',
                    "concepts": [{"term": "plain", "first_mention": A("B0002", '"plain"'), "earliest_usable_explanation": A("B0003", "7.1-7.5")}],
                    "passages": [{"block_id": "B0002", "structure_check": "s", "evidence": [A("B0002", "34-layer baseline")]}],
                    "observations": [{"id": "O01", "resolved_by": A("B0003", "the final run")}]}
        value, rewrites = orch.canonical_anchors(response, self.blocks)
        self.assertEqual(value, {"summary": 'kept "as is"',
                                 "concepts": [{"term": "plain", "first_mention": A("B0002", "\u201cplain\u201d"), "earliest_usable_explanation": A("B0003", "7.1\u20137.5")}],
                                 "passages": [{"block_id": "B0002", "structure_check": "s", "evidence": [A("B0002", "34- layer\nbaseline")]}],
                                 "observations": [{"id": "O01", "resolved_by": A("B0003", "the \ufb01nal run")}]})
        self.assertEqual([r["block_id"] for r in rewrites], ["B0002", "B0003", "B0002", "B0003"])


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
        self.assertEqual([(o["id"], o["classification"], o.get("resolved_by")) for o in reconcile["observations"]], [("O01", "absent_at_critical_use", None), ("O02", "adequate", CRITICAL)])
        reconcile_input = json.loads((self.out / "editor" / "03-reconcile.prompt.txt").read_text(encoding="utf-8").rsplit("\n=== INPUT (one JSON object) ===\n", 1)[1])
        self.assertEqual(reconcile_input["reader_profile"], rp.load_json(self.out / "reader-profile.json"), "the editor can quote the profile as background")
        receipt3 = json.loads((self.out / "journal" / "03-reconcile.receipt.json").read_text())
        self.assertEqual(len(receipt3["attachments"]), 2 + 4 + 4)
        # Independent challenge: one batch of five candidates, run through dispatch_reader with the reader config after
        # reconcile; every decision is bound to the file, and C05, disputed and kept, says why.
        dispatched = [e for e in self.log_events() if e["event"] == "reader_dispatch"]
        self.assertEqual([e["kind"] for e in dispatched], ["prefix", "masked", "branch", "challenge", "audit", "audit", "audit"])
        self.assertEqual(dispatched[3]["argv"], ["challenge", "--runtime", "http", "--config", str(self.config), "--events", str(self.out / "challenges" / "CH01"),
                                                 "--input", str(self.out / "challenges" / "inputs" / "challenge-input-01.json"), "--index", str(self.out / "index.json"),
                                                 "--profile", str(self.out / "reader-profile.json"), "--out", str(self.out / "challenges" / "CH01.challenge.json"), "--label", "CH01"])
        order = [e.get("stage", e["event"]) for e in self.log_events() if e["event"] in ("stage_recorded", "challenge_done")]
        self.assertEqual(order, ["scope", "reading", "checks", "reconcile", "challenge_done", "decisions", "repairs"])
        challenge_input = json.loads((self.out / "challenges" / "inputs" / "challenge-input-01.json").read_text(encoding="utf-8"))
        self.assertEqual([sorted(c) for c in challenge_input["candidates"]], [["anchor", "candidate_id", "question"]] * 5)
        challenge = json.loads((self.out / "challenges" / "CH01.challenge.json").read_text(encoding="utf-8"))
        self.assertEqual([(c["candidate_id"], c["verdict"], c["answer_position"], c["duplicate_of"]) for c in challenge["challenges"]],
                         [("C01", "answered", "after", None), ("C02", "not_material", None, None), ("C03", "duplicate", None, "C01"), ("C04", "stands", None, None), ("C05", "answered", "at", None)])
        self.assertEqual(challenge["challenger"], {"mode": "bounded-request", "agent_id": "http-0017"})
        decisions_notes = json.loads((self.out / "journal" / "04-decisions.notes.json").read_text(encoding="utf-8"))
        self.assertNotIn("no_challenge_reason", decisions_notes)
        self.assertEqual({d["candidate_id"]: d["challenge_path"] for d in decisions_notes["decisions"]}, {key: str(self.out / "challenges" / "CH01.challenge.json") for key in ("C01", "C02", "C03", "C04", "C05")})
        self.assertEqual([(d["candidate_id"], d.get("challenge_response")) for d in decisions_notes["decisions"]],
                         [("C01", None), ("C02", None), ("C03", None), ("C04", None), ("C05", "The sentence states the rate; nothing in B0005 says what it is compared with.")])
        decisions_input = json.loads((self.out / "editor" / "04-decisions.prompt.txt").read_text(encoding="utf-8").rsplit("\n=== INPUT (one JSON object) ===\n", 1)[1])
        self.assertEqual([(c["candidate_id"], c["disputed"]) for c in decisions_input["challenges"]], [("C01", False), ("C02", True), ("C03", True), ("C04", False), ("C05", True)])
        self.assertEqual(decisions_input["support"], [{"candidate_id": "C01", "reader_sources": 1}, {"candidate_id": "C02", "reader_sources": 0}, {"candidate_id": "C03", "reader_sources": 0},
                                                      {"candidate_id": "C04", "reader_sources": 0}, {"candidate_id": "C05", "reader_sources": 0}])
        self.assertIsNone(decisions_input["no_challenge_reason"])
        receipt4 = json.loads((self.out / "journal" / "04-decisions.receipt.json").read_text())
        self.assertEqual([a["path"] for a in receipt4["attachments"]], [str(self.out / "challenges" / "CH01.challenge.json")])
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
        self.assertEqual(review["issues"][0]["close_criterion"], "The reader can say what the MSW measures on reaching the threshold in B0003.")
        self.assertEqual(review["issues"][2]["author_question"], "How were the 412 pairs selected?")
        self.assertEqual(review["concept_ledger"][0]["term"], "MSW")
        self.assertEqual(rs.check_final(self.out / "journal", rp.load_json(self.out / "index.json"), review)["issue_count"], 3)
        self.assertEqual((review["scope_note"], review["not_assessed"], review["priority_list"]),
                         ("All four paragraphs were read; one prefix reader, a two-window masked run, and one branch question were run.",
                          ["whether the claims are true", "whether the evidence is sufficient", "whether the work is novel"], ["C01", "C05"]))
        self.assertEqual([(i["candidate_id"], i["severity"], i["reader_sources"], i["challenge"]) for i in review["issues"]],
                         [("C01", "misleads", 1, "answered"), ("C05", "hinders", 0, "answered"), ("C04", "minor", 0, "stands")])
        # The report is rendered from the checked review, not written by the editor model.
        report = (self.out / "report.md").read_text(encoding="utf-8").split("\n")
        self.assertEqual(review["depth"], "deep", "a masked checkpoint is recorded")
        self.assertEqual(report[:12], [
            "# Writing review",
            "",
            "## What was reviewed",
            "All four paragraphs were read; one prefix reader, a two-window masked run, and one branch question were run.",
            "",
            "Not assessed: whether the claims are true; whether the evidence is sufficient; whether the work is novel.",
            "",
            "Depth: deep.",
            "",
            "Records: 4 of 5 blocks read; reader checkpoints: 1 bounded-request, 1 masked-sequential; candidates: 2 accepted, 1 author question, 1 merged, 1 rejected; "
            "independent challenge: run on all 5 candidates; fidelity audits attached: 1.",
            "",
            "## Priority list",
        ])
        self.assertEqual(report[12:14], ['1. [misleads] B0003 "the MSW exceeded two seconds" — Is MSW defined first? (W01)', '2. [hinders] B0005 "91 percent" — Is 91 percent compared with anything? (W02)'])
        self.assertEqual([line for line in report if line.startswith("### ")], ["### W01 · misleads · confidence high · B0003", "### W02 · hinders · confidence medium · B0005"])
        self.assertEqual(report[-3:], ["## Minor items", "1 minor item does not hinder understanding; it is listed in report-minor.md.", ""])
        self.assertIn("### W03 · minor · confidence medium · B0005", (self.out / "report-minor.md").read_text(encoding="utf-8").split("\n"))
        self.assertFalse((self.out / "editor" / "07-report.prompt.txt").exists())
        self.assertEqual(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["stages"]["report"],
                         {"status": "written", "path": str(self.out / "report.md"), "appendix": str(self.out / "report-minor.md"), "priority_list": 2, "findings": 2, "minor": 1})
        # Summary with literal counts.
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["totals"]["editor"]["calls"], 16)  # two recheck passes over two batches
        self.assertEqual(summary["totals"]["readers"]["calls"], 8)
        self.assertEqual(summary["totals"]["readers"]["by_kind"], {"prefix": 1, "masked": 2, "branch": 1, "challenge": 1, "audit": 3})
        self.assertEqual(summary["totals"]["calls"], 24)
        self.assertEqual(summary["totals"]["retries"], 2)
        self.assertEqual(summary["totals"]["editor"]["input_tokens"], 1600)
        self.assertEqual([c["label"] for c in summary["editor_calls"]], ["00-scope", "01-reading-batch01", "01-reading-batch01-b", "01-reading-batch01-c", "01-reading-batch02", "02-checks-batch01", "02-checks-batch02", "02-checks-pass2-batch01", "02-checks-pass2-batch02", "02-checks-pass3-batch01", "02-checks-pass3-batch02", "03-reconcile", "04-decisions", "05-repairs", "05-repairs-audit", "06-review"])
        self.assertEqual([c["status"] for c in summary["editor_calls"]][:4], ["ok", "rejected", "rejected", "ok"])
        self.assertEqual(summary["stages"]["checks"], {"status": "recorded", "batches": 2, "passes": 3, "candidates_by_pass": [4, 0, 0], "rechecks_skipped": [], "concepts": 1,
                                                       "candidates": 4, "checkpoint_plan": 1, "branch_question": True})
        self.assertEqual(summary["options"]["checks_passes"], 3)
        self.assertEqual(summary["stages"]["challenge"], {"status": "challenged", "batches": 1, "failed": [], "candidates": 5,
                                                          "verdicts": {"answered": 2, "duplicate": 1, "not_material": 1, "stands": 1}, "disputed": ["C02", "C03", "C05"]})
        self.assertEqual(summary["options"]["challenge_batch"], 10)
        self.assertEqual(summary["stages"]["decisions"], {"status": "recorded", "accepted": 2, "needs_author": 1, "rejected": 1, "merged": 1})
        self.assertEqual(summary["stages"]["repairs"], {"status": "recorded", "repairs": 3, "with_wording": 1, "author_questions": 2, "audits": 3, "converted_by_audit": ["C05"], "withheld_heavy_edits": []})
        self.assertEqual(summary["stages"]["repairs"]["withheld_heavy_edits"], [])
        self.assertNotIn("edit_withheld", [e["event"] for e in self.log_events()])
        self.assertEqual(summary["stages"]["review"]["issues"], 3)
        self.assertEqual([(a["label"], a["verdict"], a["accepted"]) for a in summary["audits"]], [("C01", "preserved", True), ("C05", "changed", False), ("C05-2", "changed", False)])
        self.assertEqual(len(self.server.requests), 24)
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
        self.assertEqual([c["label"] for c in summary["editor_calls"]], ["00-scope", "01-reading-batch01", "01-reading-batch01-b", "01-reading-batch01-c", "01-reading-batch02", "02-checks-batch01", "02-checks-batch02", "02-checks-pass2-batch01", "02-checks-pass2-batch02", "02-checks-pass3-batch01", "02-checks-pass3-batch02", "03-reconcile", "04-decisions", "05-repairs", "05-repairs-audit", "06-review"])
        self.assertEqual(summary["totals"]["readers"]["by_kind"], {"prefix": 1, "masked": 2, "branch": 1, "challenge": 1, "audit": 3})
        self.assertEqual(summary["totals"]["calls"], 24)

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
        self.assertEqual(summary["totals"]["readers"]["by_kind"], {"prefix": 1, "masked": 2, "branch": 1, "challenge": 1, "audit": 3})
        self.assertEqual(summary["totals"]["calls"], 24)

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

    def test_a_run_without_masked_options_dispatches_no_masked_reader_and_its_review_is_standard(self):
        self.use_server(PrefixOnlyLLM())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = orch.main(["run", "--manuscript", str(self.manuscript), "--out", str(self.out), "--config", str(self.config), "--batch-blocks", "2", "--prefix-checkpoints", "2"])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["options"], {"batch_blocks": 2, "checks_passes": 3, "challenge_batch": 10, "prefix_checkpoints": 2, "masked_windows": 0, "masked_budget": 500})
        self.assertEqual(summary["totals"]["readers"]["by_kind"], {"prefix": 1, "masked": 0, "branch": 0, "challenge": 1, "audit": 3})
        self.assertEqual(summary["stages"]["readers"], {"status": "done", "prefix_ok": 1, "failed": [], "masked": False, "branch": False})
        self.assertFalse((self.out / "masked").exists())
        self.assertFalse((self.out / "branch-question.json").exists())
        reconcile = json.loads((self.out / "journal" / "03-reconcile.notes.json").read_text(encoding="utf-8"))
        self.assertEqual([(c["id"], c["mode"]) for c in reconcile["checkpoints"]], [("K01", "bounded-request")])
        review = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        self.assertEqual((review["depth"], review["issues"][0]["proposed_wording"]), ("standard", WORDING))
        report = (self.out / "report.md").read_text(encoding="utf-8").split("\n")
        self.assertEqual(report[7], "Depth: standard. Not done at this depth: masked reading.")

    def test_a_plan_anchor_that_names_two_places_goes_back_to_the_editor(self):
        original = self.server.step_checks

        def step_checks(data, batch, attempt):
            value = original(data, batch, attempt)
            if batch == 1 and attempt == 1:
                value["checkpoint_plan"][0]["anchor"] = A("B0003", "the ")
            return value

        self.server.step_checks = step_checks
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        second = (self.out / "editor" / "02-checks-batch01-b.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("checkpoint_plan anchor: The anchor quote occurs 2 times in B0003, so it does not say where the reader stops; lengthen the quote so that it names one place.", second)
        plan = json.loads((self.out / "journal" / "02-checks.notes.json").read_text())["checkpoint_plan"]
        self.assertEqual([entry["anchor"] for entry in plan], [CRITICAL])
        packet = json.loads((self.out / "readers" / "P01" / "P01.packet.json").read_text())
        self.assertTrue(packet["visible_text"].endswith("when the MSW exceeded two seconds"))

    def test_straight_quoted_anchor_against_curly_text_is_canonicalized_without_a_re_prompt(self):
        self.manuscript.write_text(TEXT.replace("records agreed.", "records \u201cagreed\u201d."), encoding="utf-8")
        original = self.server.step_checks

        def step_checks(data, batch, attempt):
            value = original(data, batch, attempt)
            if batch == 1:
                value["passages"][0]["evidence"] = [A("B0002", 'two independent records "agreed"')]
            return value

        self.server.step_checks = step_checks
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertFalse((self.out / "editor" / "02-checks-batch01-b.prompt.txt").exists())
        rewrites = [{k: v for k, v in e.items() if k != "at"} for e in self.log_events() if e["event"] == "anchor_canonicalized"]
        self.assertEqual(rewrites, [{"event": "anchor_canonicalized", "step": "checks", "label": "02-checks-batch01", "block_id": "B0002",
                                     "quote": 'two independent records "agreed"', "replacement": "two independent records \u201cagreed\u201d"}])
        checks = json.loads((self.out / "journal" / "02-checks.notes.json").read_text(encoding="utf-8"))
        self.assertEqual(checks["passages"][0]["evidence"], [A("B0002", "two independent records \u201cagreed\u201d")])
        model = json.loads((self.out / "editor" / "02-checks-batch01.response.json").read_text(encoding="utf-8"))
        self.assertEqual(model["passages"][0]["evidence"], [A("B0002", 'two independent records "agreed"')])
        totals = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["totals"]
        self.assertEqual((totals["retries"], totals["anchors_canonicalized"]), (2, 1))

    def test_a_re_prompt_that_omits_fields_is_accepted_with_the_fields_carried(self):
        original = self.server.step_checks

        def step_checks(data, batch, attempt):
            value = original(data, batch, attempt)
            if batch == 1 and attempt == 1:
                value["passages"][0]["evidence"] = [A("B0002", "records disagreed")]
            if batch == 1 and attempt == 2:
                value = {k: value[k] for k in ("concepts", "no_concepts_reason", "passages")}
            return value

        self.server.step_checks = step_checks
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        second = (self.out / "editor" / "02-checks-batch01-b.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("passage.evidence[0].quote is not an exact substring of its supplied block.", second)
        self.assertFalse((self.out / "editor" / "02-checks-batch01-c.prompt.txt").exists())
        carried = [{k: v for k, v in e.items() if k != "at"} for e in self.log_events() if e["event"] == "fields_carried"]
        self.assertEqual(carried, [{"event": "fields_carried", "step": "checks", "label": "02-checks-batch01-b", "fields": ["summary", "no_candidate", "checkpoint_plan", "candidates", "branch_question"]}])
        checks = json.loads((self.out / "journal" / "02-checks.notes.json").read_text(encoding="utf-8"))
        self.assertEqual(checks["summary"], "first half second half")
        self.assertEqual([c["id"] for c in checks["candidates"]], ["C01", "C02", "C03", "C04"])
        self.assertEqual([p["id"] for p in checks["checkpoint_plan"]], ["P01"])
        self.assertEqual(json.loads((self.out / "branch-question.json").read_text())["question"], "What does MSW stand for?")
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual([c["status"] for c in summary["editor_calls"] if c["label"].startswith("02-checks-batch01")], ["rejected", "ok"])
        self.assertEqual(summary["totals"]["retries"], 3)

    def test_a_left_out_array_is_named_as_missing_in_the_re_prompt(self):
        original = self.server.step_checks

        def step_checks(data, batch, attempt):
            value = original(data, batch, attempt)
            if batch == 1 and attempt == 1:
                del value["checkpoint_plan"]
            return value

        self.server.step_checks = step_checks
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        second = (self.out / "editor" / "02-checks-batch01-b.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("Attempt 1: checkpoint_plan is missing; give it as an array, [] when there are none.", second)
        self.assertNotIn("checkpoint_plan must be an array", second)

    def executable(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def review_paper(self, *args, **env):
        return subprocess.run(["bash", str(PACKAGE / "server" / "review-paper.sh"), *args], capture_output=True, text=True, timeout=120,
                              env={**os.environ, **env})

    def test_review_paper_sh_extracts_repairs_split_words_and_runs_the_review(self):
        (self.root / "parsed.md").write_text(TEXT.replace("independent records", "indep endent records"), encoding="utf-8")
        (self.root / "layer.txt").write_text(TEXT, encoding="utf-8")
        log = self.root / "mineru.log"
        mineru = self.executable("mineru", FAKE_MINERU.replace("{log}", str(log)).replace("{parsed}", str(self.root / "parsed.md")))
        pdftotext = self.executable("pdftotext", FAKE_PDFTOTEXT.replace("{layer}", str(self.root / "layer.txt")))
        pdf = self.root / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%fake\n")
        run = self.root / "runs" / "20261116-0900-paper"
        run.mkdir(parents=True)
        # The foreground half, as the background launcher starts it.
        result = self.review_paper(str(pdf), "--pages", "1-2", "--", "--batch-blocks", "2", "--masked-windows", "2", "--masked-budget", "20", "--prefix-checkpoints", "2",
                                   REVIEW_PAPER_RUN=str(run), REVIEW_PAPER_CONFIG=str(self.config), MINERU=str(mineru), PDFTOTEXT=str(pdftotext))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(log.read_text().splitlines(), ["server start", "server status --json", f"parse {pdf} --pages 1-2 --tier standard --wait 3600 -o {run}/extract/paper.md"])
        self.assertEqual((run / "manuscript.md").read_text(encoding="utf-8"), TEXT)
        self.assertIn("repaired 1 extraction word break(s)", (run / "extraction-notes.txt").read_text(encoding="utf-8"))
        self.assertIn("repaired 1 extraction word break(s)", (run / "review" / "editor" / "00-scope.prompt.txt").read_text(encoding="utf-8"))
        review = json.loads((run / "review" / "review.json").read_text(encoding="utf-8"))
        self.assertEqual([i["id"] for i in review["issues"]], ["W01", "W02", "W03"])
        self.assertTrue(result.stdout.rstrip("\n").endswith(f"done: {run}/review/report.md"), result.stdout)
        self.assertTrue((run / "review" / "report.md").is_file())

    def test_review_paper_sh_without_a_config_and_a_pdf_prints_its_usage(self):
        result = self.review_paper("--config", str(self.config))
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("# Usage: review-paper.sh --config llm.json PAPER.pdf [--pages 1-10] [-- other orchestrator.py run options]\n"), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_batches_default_to_twenty_blocks(self):
        # At 40 blocks per batch the checks output of Qwen3.8-Flash-Next ran away into repetition until the context was full.
        args = orch._parser().parse_args(["run", "--manuscript", "m.md", "--out", "o", "--config", "c.json"])
        self.assertEqual(args.batch_blocks, 20)

    def use_json_schema(self):
        config = json.loads(self.config.read_text())
        self.config.write_text(json.dumps({**config, "json_schema": True}))

    def schema_files(self):
        return sorted(p for p in self.out.rglob("*.schema.json"))

    def test_with_json_schema_each_editor_request_carries_its_step_schema(self):
        self.use_json_schema()
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        bodies = [r["body"] for r in self.server.requests]
        editor = [b for b in bodies if b["messages"][0]["content"].startswith("You are the editor")]
        self.assertEqual({b["response_format"]["type"] for b in editor}, {"json_schema"})
        auditor = [b for b in bodies if b["messages"][0]["content"].startswith("You are the auditor")]
        challenger = [b for b in bodies if b["messages"][0]["content"].startswith("You are the checker")]
        prefix_or_branch = [b for b in bodies if b["messages"][1]["content"].startswith("Act as the target academic reader") or "branch packet" in b["messages"][1]["content"][:200]]
        masked = [b for b in bodies if b not in editor + auditor + challenger + prefix_or_branch]
        self.assertEqual((len(editor), len(auditor), len(challenger), len(prefix_or_branch), len(masked)), (16, 3, 1, 2, 2))
        self.assertEqual({b["response_format"]["type"] for b in auditor + challenger + masked}, {"json_schema"})
        self.assertEqual([b["response_format"] for b in prefix_or_branch], [{"type": "json_object"}, {"type": "json_object"}])
        checks = next(b for b in editor if b["messages"][1]["content"].startswith("Editor step: checks (batch 1 of 2,"))
        schema = checks["response_format"]["json_schema"]["schema"]
        passages = schema["properties"]["passages"]
        self.assertEqual((passages["minItems"], passages["maxItems"], passages["items"]["properties"]["block_id"]["enum"]), (2, 2, ["B0002", "B0003"]))
        self.assertEqual(list(schema["properties"]), ["summary", "checkpoint_plan", "candidates", "no_candidate", "branch_question", "concepts", "no_concepts_reason", "passages"])
        self.assertEqual(schema["properties"]["no_candidate"], {"type": "array", "maxItems": 0}, "the fake reading notes raise no open question")
        self.assertEqual(schema["properties"]["branch_question"]["anyOf"][0]["properties"]["after_block_id"], {"type": "string", "enum": ["B0002", "B0003"]})
        saved = json.loads((self.out / "editor" / "02-checks-batch01.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, schema)
        provenance = json.loads((self.out / "editor" / "02-checks-batch01.provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["request"]["response_format"], {"type": "json_schema", "name": "response", "schema_sha256": orch.dr.sha256_bytes(orch.dr.encoded(saved))})
        decisions = json.loads((self.out / "editor" / "04-decisions.schema.json").read_text(encoding="utf-8"))["properties"]["decisions"]
        self.assertEqual((decisions["minItems"], decisions["maxItems"]), (5, 5))
        self.assertEqual(decisions["items"]["anyOf"][0]["properties"]["severity"], {"type": "string", "enum": ["hinders", "minor", "misleads"]})
        reconcile = json.loads((self.out / "editor" / "03-reconcile.schema.json").read_text(encoding="utf-8"))["properties"]
        self.assertEqual([branch["properties"]["id"] for branch in reconcile["checkpoints"]["items"]["anyOf"]], [{"type": "string", "enum": ["K01"]}, {"type": "string"}])
        self.assertEqual(reconcile["checkpoints"]["items"]["anyOf"][1]["properties"]["record_sequence"], {"type": "integer", "enum": [1, 2]})
        self.assertEqual(reconcile["observations"]["items"]["properties"]["background"]["anyOf"][0]["enum"], sorted(set(rs.string_leaves(rp.load_json(self.out / "reader-profile.json")))))

    def test_with_json_schema_the_challenger_answers_in_whole_sentences_tied_to_its_verdict(self):
        self.use_json_schema()
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        schema = json.loads((self.out / "challenges" / "CH01" / "CH01.schema.json").read_text(encoding="utf-8"))
        challenges = schema["properties"]["challenges"]
        self.assertEqual((challenges["minItems"], challenges["maxItems"]), (5, 5))
        answered, duplicate, other = challenges["items"]["anyOf"]
        self.assertEqual(list(answered["properties"]), ["candidate_id", "verdict", "answer", "duplicate_of", "reason"])
        self.assertEqual([branch["properties"]["verdict"] for branch in (answered, duplicate, other)],
                         [{"const": "answered"}, {"const": "duplicate"}, {"type": "string", "enum": ["not_material", "stands"]}])
        self.assertEqual((duplicate["properties"]["answer"], duplicate["properties"]["duplicate_of"]), ({"type": "null"}, {"type": "string", "enum": ["C01", "C02", "C03", "C04", "C05"]}))
        self.assertEqual(answered["properties"]["answer"]["anyOf"][-1], {"type": "object", "properties": {"block_id": {"const": "B0005"},
                         "quote": {"type": "string", "enum": ["Agreement in this set was 91 percent.", "The restricted analysis included 412 pairs."]}}, "required": ["block_id", "quote"], "additionalProperties": False})
        self.assertEqual([b["properties"]["block_id"]["const"] for b in answered["properties"]["answer"]["anyOf"]], ["B0001", "B0002", "B0003", "B0004", "B0005"])
        decisions = json.loads((self.out / "editor" / "04-decisions.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(decisions["properties"]["decisions"]["items"]["anyOf"][0]["properties"]["challenge_response"], {"anyOf": [{"type": "string"}, {"type": "null"}]})

    def test_candidates_are_challenged_in_batches_and_each_decision_names_its_file(self):
        code, out, err = self.run_pipeline("--challenge-batch", "3")
        self.assertEqual(code, 0, out + err)
        first, second = (str(self.out / "challenges" / f"CH0{n}.challenge.json") for n in (1, 2))
        self.assertEqual([[c["candidate_id"] for c in json.loads(Path(path).read_text())["challenges"]] for path in (first, second)], [["C01", "C02", "C03"], ["C04", "C05"]])
        decisions = json.loads((self.out / "journal" / "04-decisions.notes.json").read_text(encoding="utf-8"))
        self.assertEqual([(d["candidate_id"], d["challenge_path"]) for d in decisions["decisions"]], [("C01", first), ("C02", first), ("C03", first), ("C04", second), ("C05", second)])
        self.assertEqual([a["path"] for a in json.loads((self.out / "journal" / "04-decisions.receipt.json").read_text())["attachments"]], [first, second])
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["stages"]["challenge"]["status"], summary["stages"]["challenge"]["batches"]), ("challenged", 2))
        self.assertEqual(summary["totals"]["readers"]["by_kind"]["challenge"], 2)
        self.assertIn("independent challenge: run on all 5 candidates;", (self.out / "report.md").read_text(encoding="utf-8"))

    def test_a_challenger_that_never_answers_validly_leaves_every_candidate_unchallenged_and_says_why(self):
        self.server.challenger = lambda request: "I would rather not."
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(sorted(p.name for p in (self.out / "challenges" / "CH01").glob("*.response.raw")), ["CH01.response.raw", "CH01b.response.raw", "CH01c.response.raw"])
        self.assertFalse((self.out / "challenges" / "CH01.challenge.json").exists())
        reason = (f"The independent challenger did not return a valid answer for CH01 after dispatch_reader.py's corrections (see {self.out / 'challenges'}); "
                  "a schema 7 journal records a challenge for every candidate or for none, so none is recorded.")
        decisions = json.loads((self.out / "journal" / "04-decisions.notes.json").read_text(encoding="utf-8"))
        self.assertEqual(decisions["no_challenge_reason"], reason)
        self.assertEqual([d.get("challenge_path") for d in decisions["decisions"]], [None] * 5)
        decisions_input = json.loads((self.out / "editor" / "04-decisions.prompt.txt").read_text(encoding="utf-8").rsplit("\n=== INPUT (one JSON object) ===\n", 1)[1])
        self.assertEqual((decisions_input["challenges"], decisions_input["no_challenge_reason"]), (None, reason))
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["stages"]["challenge"], {"status": "failed", "batches": 1, "failed": ["CH01"], "candidates": 5,
                                                          "verdicts": {"answered": 0, "duplicate": 0, "not_material": 0, "stands": 0}, "disputed": []})
        self.assertEqual([e["failed"] for e in self.log_events() if e["event"] == "challenge_failed"], [["CH01"]])
        review = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        self.assertEqual([i["challenge"] for i in review["issues"]], [None, None, None])
        self.assertIn(f"independent challenge: not run ({reason})", (self.out / "report.md").read_text(encoding="utf-8"))

    def test_with_no_candidate_the_challenge_has_nothing_to_run_and_needs_no_reason(self):
        server, checks, reconcile = self.server, self.server.step_checks, self.server.step_reconcile

        def no_candidates(step):
            def answer(data, batch, attempt):
                value = step(data, batch, attempt)
                value["candidates"] = []
                for row in value.get("observations", []):
                    row["candidate_id"] = None
                return value
            return answer

        server.step_checks, server.step_reconcile = no_candidates(checks), no_candidates(reconcile)
        server.step_decisions = lambda data, batch, attempt: {"summary": "No candidate was recorded.", "decisions": []}
        server.step_repairs = lambda data, batch, attempt: {"summary": "Nothing to repair.", "repairs": []}
        server.step_review = lambda data, batch, attempt: {"summary": "No writing problem was recorded.", "scope_note": "All four paragraphs were read.", "issue_order": []}
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertIn("challenge: no candidates (0 candidates in 0 batch(es))", out)
        self.assertFalse((self.out / "challenges").exists())
        self.assertNotIn("challenge", [e["kind"] for e in self.log_events() if e["event"] == "reader_dispatch"])
        decisions = json.loads((self.out / "journal" / "04-decisions.notes.json").read_text(encoding="utf-8"))
        self.assertEqual((decisions["decisions"], "no_challenge_reason" in decisions), ([], False))
        self.assertEqual(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["stages"]["challenge"],
                         {"status": "no candidates", "batches": 0, "failed": [], "candidates": 0, "verdicts": {"answered": 0, "duplicate": 0, "not_material": 0, "stands": 0}, "disputed": []})
        self.assertIn("independent challenge: no candidates to challenge;", (self.out / "report.md").read_text(encoding="utf-8"))

    def test_without_json_schema_no_editor_request_carries_a_schema(self):
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertEqual({json.dumps(r["body"]["response_format"]) for r in self.server.requests}, {json.dumps({"type": "json_object"})})
        self.assertEqual(self.schema_files(), [])

    def test_every_accepted_response_fits_the_schema_its_call_sent(self):
        # A schema stricter than its validator would force a constrained model away from a valid answer.
        self.use_json_schema()
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        labels = [e["label"] for e in self.log_events() if e["event"] == "editor_call" and e["status"] == "ok"]
        self.assertEqual(sorted({label[:2] for label in labels}), ["00", "01", "02", "03", "04", "05", "06"])
        for label in labels:
            schema = json.loads((self.out / "editor" / f"{label}.schema.json").read_text(encoding="utf-8"))
            response = json.loads((self.out / "editor" / f"{label}.response.json").read_text(encoding="utf-8"))
            self.assertEqual(schema_errors(response, schema), [], label)
        checked = set()
        for path in [p for folder in ("masked", "challenges", "audits") for p in (self.out / folder).rglob("summary.json")]:
            summary = json.loads(path.read_text(encoding="utf-8"))
            for call in summary["calls"]:
                if call["status"] == "ok" and summary["command"] in ("masked", "challenge", "audit"):
                    schema = json.loads((path.parent / f"{call['label']}.schema.json").read_text(encoding="utf-8"))
                    response = json.loads((path.parent / f"{call['label']}.response.json").read_text(encoding="utf-8"))
                    self.assertEqual(schema_errors(response, schema), [], f"{path.parent.name}/{call['label']}")
                    checked.add(summary["command"])
        self.assertEqual(checked, {"masked", "challenge", "audit"})

    def test_no_schema_sent_uses_a_keyword_that_xgrammar_mishandles(self):
        # xgrammar compiles a string with minLength or maxLength to characters without a backslash (no \n, no \"), and a
        # pattern repeated in one schema trips an internal assertion; strings stay unbounded and max_tokens bounds them.
        self.assertEqual(orch.schemas.FORBIDDEN_KEYWORDS, ("minLength", "maxLength", "pattern"))
        self.assertEqual([k for _, k in schema_keywords({"type": "object", "properties": {"pattern": {"type": "string", "maxLength": 9}}})], ["type", "properties", "type", "maxLength"])
        self.use_json_schema()
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        sent = self.schema_files()
        self.assertEqual(sorted({p.parent.name for p in sent}), ["C01", "C05", "C05-2", "CH01", "editor", "events"])
        for path in sent:
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([(where, key) for where, key in schema_keywords(schema) if key in orch.schemas.FORBIDDEN_KEYWORDS], [], path.name)

    def test_every_step_schema_builder_avoids_the_keywords_that_xgrammar_mishandles(self):
        samples = {"scope": ("review", ["B0001", "B0002"]), "reading": (["B0002"],),
                   "checks": (["B0002"], ["B0002"], ["B0001", "B0002"], 1, {"B0002": ["What is X?"]}, ["B0002"]),
                   "reconcile": (["B0002"], ["B0001", "B0002"], ["K01"], [1, 2], ["R01"], ["A reader of the field."]),
                   "recheck": (["B0002"], ["B0001", "B0002"]), "decisions": (["C01", "C02"], ["B0002"], True), "repairs": (["C01"], ["B0002"]), "review": (["C01"],)}
        builders = {name: function for name, function in inspect.getmembers(orch.schemas, inspect.isfunction)
                    if function.__module__ == orch.schemas.__name__ and (orch.PROMPTS / f"{name}.txt").exists()}
        self.assertEqual(sorted(builders), sorted(p.stem for p in orch.PROMPTS.glob("*.txt")), "every editor template has a schema")
        self.assertEqual(sorted(builders), sorted(samples))
        for name, function in builders.items():
            schema = function(*samples[name])
            self.assertEqual([(where, key) for where, key in schema_keywords(schema) if key in orch.schemas.FORBIDDEN_KEYWORDS], [], name)
        checks = builders["checks"](*samples["checks"])
        self.assertEqual(checks["properties"]["no_candidate"]["items"]["anyOf"][0]["properties"]["question"], {"type": "string", "enum": ["What is X?"]})
        self.assertEqual(builders["decisions"](*samples["decisions"])["properties"]["decisions"]["items"]["anyOf"][0]["properties"]["challenge_response"],
                         {"anyOf": [{"type": "string"}, {"type": "null"}]})

    def test_later_checks_passes_add_only_what_the_earlier_ones_missed(self):
        def step_recheck(data, batch, attempt):
            found = [{"id": "x", "anchor": A("B0002", "a separate observer"), "question": "Separate from what?", "evidence": []}] if data["pass"] == 2 and batch == 1 else []
            return {"summary": "rechecked", "candidates": found}

        self.server.step_recheck = step_recheck
        self.server.break_step = "decisions"  # the fake's decisions name C01-C05 only; this test is about the checks stage
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 1, out + err)

        def recorded(label):
            prompt = (self.out / "editor" / f"{label}.prompt.txt").read_text(encoding="utf-8")
            return prompt.split("\n", 1)[0], [c["id"] for c in json.loads(prompt.rsplit("\n=== INPUT (one JSON object) ===\n", 1)[1])["already_recorded"]["candidates"]]

        self.assertEqual(recorded("02-checks-pass2-batch01"), ("Editor step: recheck (batch 1 of 2, pass 2 of 3, blocks B0002 to B0003)", ["C01", "C02", "C03", "C04"]))
        self.assertEqual(recorded("02-checks-pass3-batch02"), ("Editor step: recheck (batch 2 of 2, pass 3 of 3, blocks B0004 to B0005)", ["C01", "C02", "C03", "C04", "C05"]))
        checks = json.loads((self.out / "journal" / "02-checks.notes.json").read_text(encoding="utf-8"))
        self.assertEqual(checks["candidates"][-1], {"id": "C05", "anchor": A("B0002", "a separate observer"), "question": "Separate from what?", "evidence": []})
        reconcile_input = json.loads((self.out / "editor" / "03-reconcile.prompt.txt").read_text(encoding="utf-8").rsplit("\n=== INPUT (one JSON object) ===\n", 1)[1])
        self.assertEqual(reconcile_input["next_candidate_id"], "C06")
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["stages"]["checks"]["passes"], summary["stages"]["checks"]["candidates_by_pass"]), (3, [4, 1, 0]))
        self.assertEqual(summary["failure"]["stage"], "decisions")

    def test_a_recheck_batch_that_never_validates_is_skipped_and_the_run_completes(self):
        original = self.server.step_recheck

        def step_recheck(data, batch, attempt):
            if data["pass"] == 2 and batch == 1:
                return {"summary": "broken", "candidates": [{"id": "x", "anchor": A("B0002", "not in the text"), "question": "q", "evidence": []}]}
            return original(data, batch, attempt)

        self.server.step_recheck = step_recheck
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 0, out + err)
        self.assertIn("checks: pass 2/3 batch 1/2 skipped after every attempt failed", out)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["stages"]["checks"]["candidates_by_pass"], summary["stages"]["checks"]["rechecks_skipped"]), ([4, 0, 0], ["02-checks-pass2-batch01"]))
        self.assertEqual([e["label"] for e in self.log_events() if e["event"] == "recheck_skipped"], ["02-checks-pass2-batch01"])
        self.assertEqual(sorted(p.name for p in (self.out / "editor").glob("02-checks-pass2-batch01*.prompt.txt")),
                         ["02-checks-pass2-batch01-b.prompt.txt", "02-checks-pass2-batch01-c.prompt.txt", "02-checks-pass2-batch01-d.prompt.txt", "02-checks-pass2-batch01.prompt.txt"])
        self.assertEqual(summary["stages"]["review"]["issues"], 3)

    def test_a_recheck_candidate_that_repeats_a_declined_open_question_is_re_prompted(self):
        # The first pass declined the reading notes' open question in no_candidate; a later candidate that carries it
        # word for word would contradict that record, so the pass is asked again before anything is recorded.
        original_reading, original_checks = self.server.step_reading, self.server.step_checks
        question = "Who produced the records?"

        def step_reading(data, batch, attempt):
            value = original_reading(data, batch, attempt)
            if isinstance(value, dict) and batch == 1:
                value["paragraphs"][0]["open_questions"] = [question]
            return value

        def step_checks(data, batch, attempt):
            value = original_checks(data, batch, attempt)
            if batch == 1:
                value["no_candidate"] = [{"block_id": "B0002", "question": question, "reason": "the next sentence names the observers"}]
            return value

        def step_recheck(data, batch, attempt):
            text = question if attempt == 1 else "Are the observers independent of each other?"
            found = [{"id": "x", "anchor": A("B0002", "a separate observer"), "question": text, "evidence": []}] if data["pass"] == 2 and batch == 1 else []
            return {"summary": "rechecked", "candidates": found}

        self.server.step_reading, self.server.step_checks, self.server.step_recheck = step_reading, step_checks, step_recheck
        self.server.break_step = "decisions"
        code, out, err = self.run_pipeline()
        self.assertEqual(code, 1, out + err)
        second = (self.out / "editor" / "02-checks-pass2-batch01-b.prompt.txt").read_text(encoding="utf-8")
        self.assertIn("no_candidate contradicts a candidate that already carries the question for B0002: 'Who produced the records?'. "
                      "Ask the question in your own words: the first pass already accounted for the open questions of the reading notes.", second)
        checks = json.loads((self.out / "journal" / "02-checks.notes.json").read_text(encoding="utf-8"))
        self.assertEqual([(c["id"], c["question"]) for c in checks["candidates"][4:]], [("C05", "Are the observers independent of each other?")])

    def test_checks_asks_for_the_short_fields_before_the_long_passages(self):
        # Flash-Next followed the example's order and closed the object after passages, dropping the three fields after
        # it in 3 of 4 replays; with them first it kept all of them in 4 of 4.
        rendered = orch.render("checks", {"batch": 1, "batches": 2, "block_range": "B0002 to B0005", "input": {}})
        example = rendered[rendered.index('{"summary":'):]
        keys = ("summary", "checkpoint_plan", "candidates", "no_candidate", "branch_question", "concepts", "no_concepts_reason", "passages")
        positions = [example.index(f'"{key}":') for key in keys]
        self.assertEqual(positions, sorted(positions))

    def test_every_template_that_asks_for_quotes_says_to_keep_typographic_quotation_marks(self):
        # Writing a straight " for a curly one inside a JSON string ended the string early and broke the response apart
        # (3 of 4 first checks answers flawed); with this rule 3 of 4 came back clean.
        rule = "keeping typographic quotation marks such as \u201c \u201d \u2018 \u2019 exactly as they appear"
        values = {"batch": 1, "batches": 1, "pass": 2, "passes": 3, "block_range": "x", "input": {}}
        quoting = sorted(step for step in (p.stem for p in orch.PROMPTS.glob("*.txt")) if '"quote"' in orch.render(step, values).split("What to return:", 1)[1])
        self.assertEqual(quoting, ["checks", "decisions", "reading", "recheck", "reconcile", "repairs"])
        for step in quoting:
            self.assertIn(rule, orch.render(step, values), step)

    def test_checks_and_decisions_treat_a_word_broken_by_a_space_as_extraction(self):
        # MinerU split "assume" and "assessment" in one extracted proposal; the report listed both as the author's typos.
        values = {"batch": 1, "batches": 1, "block_range": "x", "input": {}}
        self.assertIn("- A word broken by a space (\u201cas sume\u201d, \u201cassess ment\u201d) is left by PDF extraction, not written by the author; do not raise it as a candidate.",
                      orch.render("checks", values))
        self.assertIn("- Reject a candidate whose only problem is a word broken by a space (\u201cas sume\u201d, \u201cassess ment\u201d)", orch.render("decisions", values))
        self.assertIn("- A word broken by a space (\u201cas sume\u201d, \u201cassess ment\u201d) is left by PDF extraction", orch.render("recheck", {**values, "pass": 2, "passes": 3}))

    def test_an_unfinished_placeholder_is_a_candidate_and_an_author_question(self):
        # A single-pass run saw "[TODO: ...]" in the qualifications paragraph and called it "a placeholder, not a writing issue".
        values = {"batch": 1, "batches": 1, "block_range": "x", "input": {}}
        self.assertIn("- An unfinished drafting placeholder (TODO, TBD, XX, [citation needed], an empty pair of brackets) is a candidate, not a note in wording_check",
                      orch.render("checks", values))
        self.assertIn("is needs_author, category other, severity hinders", orch.render("decisions", values))
        self.assertIn("- An unfinished drafting placeholder (TODO, TBD, XX, [citation needed], an empty pair of brackets) is a candidate: a reader takes the passage as incomplete.",
                      orch.render("recheck", {**values, "pass": 2, "passes": 3}))

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
        prompts = sorted(path.stem for path in (PACKAGE / "server" / "prompts").glob("*.txt"))
        self.assertEqual(prompts, sorted(set(orch.STEPS) - {"report"} | {"recheck"}),
                         "every step but the report has an editor prompt (the report is rendered by notes_build.py), and checks has a second one for its later passes")
        for step in prompts:
            text = orch.render(step, {"batch": 1, "batches": 1, "pass": 2, "passes": 3, "block_range": "x", "input": {}})
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
