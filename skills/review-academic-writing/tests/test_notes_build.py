import contextlib
import importlib.util
import io
import json
from pathlib import Path
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


nb = load("notes_build")
rs, rp = nb.rs, nb.rp

TEXT = """# Counting repeated visits to a feeder

We recorded every visit to a single feeder over ten days. Each visit was tagged by the camera at the entrance.

Visits shorter than the RVG were merged into the preceding visit, so a bird that left and returned at once counted once.

We call the shortest gap that separates two visits the Return Visit Gap (RVG). We set the RVG to five seconds.

The merged record held 1284 visits. Repeated visitors accounted for 63 percent of them.
"""
PROFILE = {"audience": "A trained researcher new to this project"}
INCLUDED = ["B0002", "B0003", "B0004", "B0005"]
CRITICAL = {"block_id": "B0003", "quote": "Visits shorter than the RVG"}
ORIGINAL = {"block_id": "B0003", "quote": "Visits shorter than the RVG were merged into the preceding visit"}
DEFINITION = {"block_id": "B0004", "quote": "We call the shortest gap that separates two visits the Return Visit Gap (RVG)."}
SETTING = {"block_id": "B0004", "quote": "We set the RVG to five seconds."}
WORDING = "Visits shorter than the five-second gap between visits were merged into the preceding visit."
OTHER_WORDING = "Visits briefer than the gap were folded into the visit before them."
CLAIM_CHECK = {"claim_strength": "no absolute or comparative claim in this block", "counterexample": "no formula, metric, or rule to try a counterexample on",
               "citation_check": "no citation carries a comparison or novelty claim here"}
AUDIT_CHECKS = ("claim_strength", "quantifiers", "assumptions", "population", "measured_outcome", "time_horizon", "numerical_values", "uncertainty", "causal_vs_associative", "added_qualification")


def A(block_id, quote):
    return {"block_id": block_id, "quote": quote}


class NotesBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.index = rp.build_index(TEXT)
        self.sid = self.index["snapshot_id"]
        self.text = {b["block_id"]: b["text"] for b in self.index["blocks"]}
        self.index_path = self.root / "index.json"
        self.index_path.write_text(json.dumps(self.index), encoding="utf-8")
        self.journal = self.root / "journal"
        scope = {"task": "review", "included_block_ids": INCLUDED, "reading_path": "linear", "extraction_notes": "plain",
                 "omissions": [{"block_id": "B0001", "reason": "title"}]}
        rs.start_run(self.index, PROFILE, scope, self.journal)
        self.audits = self.root / "audits"
        self.audits.mkdir()
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    # ---------------------------------------------------------------- helpers

    def write(self, relative, value):
        """Write a JSON fragment (dict) or a Python fragment (str) under the temp root and return its path."""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def out_path(self, stage):
        self.counter += 1
        return self.root / f"out-{self.counter:02d}-{stage}.json"

    def build(self, stage, sources, audits=None, compare=None, out=None):
        out = out or self.out_path(stage)
        result = nb.build(stage, self.journal, self.index_path, [str(s) for s in sources], out, audits, compare)
        return result, rp.load_json(out)

    def record(self, stage, path):
        return rs.record_stage(self.journal, self.index, stage, Path(path).read_bytes())

    def build_and_record(self, stage, sources, audits=None):
        result, value = self.build(stage, sources, audits=audits)
        self.record(stage, result["output"])
        return result, value

    def assertRefused(self, message, stage, sources, audits=None, compare=None):
        out = self.out_path(stage)
        with self.assertRaisesRegex(rp.ReviewDataError, message):
            nb.build(stage, self.journal, self.index_path, [str(s) for s in sources], out, audits, compare)
        self.assertFalse(out.exists(), "a refused build must not write its output")

    def through(self, last):
        """Build and record the stages up to and including `last` from the standard fragments, skipping stages already recorded."""
        recorded = rs.load_run(self.journal, self.index)[1]
        for stage in rs.STAGES[: rs.STAGES.index(last) + 1]:
            if stage in recorded:
                continue
            if stage == "repairs":
                self.write_audits()
                self.build_and_record(stage, [self.write("drafts/repairs.json", self.repairs_fragment())], audits=self.audits)
            else:
                self.build_and_record(stage, [self.write(f"drafts/{stage}.json", self.fragment(stage))])

    def fragment(self, stage):
        if stage == "reading":
            return self.reading_fragment()
        if stage == "checks":
            return self.checks_fragment()
        if stage == "reconcile":
            return {"summary": "No prefix reader was dispatched.", "no_checkpoints_reason": "none run",
                    "candidates": [{"id": "C04", "anchor": A("B0005", "63 percent"), "question": "Compared with what rate?"}]}
        if stage == "decisions":
            return self.decisions_fragment()
        if stage == "repairs":
            return self.repairs_fragment()
        return {"summary": "Three findings.", "coverage": "all four paragraphs read"}

    def reading_fragment(self, blocks=INCLUDED):
        paragraphs = {b: {"paraphrase": f"paraphrase of {b}", "role": "r", "required_context": [], "new_information": [], "open_questions": []} for b in blocks}
        if "B0002" in paragraphs:
            paragraphs["B0002"]["open_questions"] = ["Which camera?"]
        return {"summary": "Four paragraphs read.", "paragraphs": paragraphs}

    def checks_fragment(self, blocks=INCLUDED):
        return {"summary": "Checks done.",
                "concepts": [{"term": "RVG", "first_mention": CRITICAL, "earliest_usable_explanation": DEFINITION, "first_critical_use": CRITICAL, "assessment": "used in B0003 before B0004 defines it"}],
                "passages": {b: {"structure_check": "s", "wording_check": "w", "claim_check": CLAIM_CHECK} for b in blocks},
                "checkpoint_plan": [{"id": "P01", "anchor": CRITICAL, "mode": "fresh-prefix", "reason": "first critical use of RVG"}],
                "candidates": [{"id": "C01", "anchor": CRITICAL, "question": "Is RVG defined before this use?", "evidence": [DEFINITION]},
                               {"id": "C02", "anchor": A("B0005", "1284 visits"), "question": "How were the visits counted?"},
                               {"id": "C03", "anchor": A("B0002", "tagged by the camera"), "question": "Which camera?"}]}

    def decisions_fragment(self):
        common = {"verification": "v", "countercheck": "c"}
        return {"summary": "Decided.", "decisions": {
            "C01": {**common, "status": "accepted", "evidence": [DEFINITION, SETTING], "category": "late_prerequisite",
                    "diagnosis": "RVG is used in B0003 before B0004 defines it.", "severity": "major", "confidence": "high"},
            "C02": {**common, "status": "needs_author", "category": "unsupported_claim", "diagnosis": "The visit count has no stated source.", "severity": "minor", "confidence": "medium"},
            "C03": {**common, "status": "rejected"},
            "C04": {**common, "status": "accepted", "category": "unsupported_claim", "diagnosis": "The share of repeated visitors has no comparison.", "severity": "minor", "confidence": "medium"}}}

    def repairs_fragment(self):
        return {"summary": "Repaired.", "repairs": {
            "C01": {"kind": "edit", "edit_scope": "term", "action": "Replace the RVG in B0003 with the gap it names.",
                    "scientific_fidelity": "threshold preserved", "new_terms_check": "drops RVG", "affected_block_ids": ["B0003"]},
            "C02": {"action": "Ask the author how the 1284 visits in B0005 were counted.", "author_question": "How were the 1284 visits counted?",
                    "scientific_fidelity": "no change", "new_terms_check": "none", "affected_block_ids": ["B0005"]},
            "C04": {"kind": "edit", "edit_scope": "apposition", "action": "State the comparison rate beside 63 percent in B0005.", "proposed_wording": None,
                    "wording_withheld_reason": "the comparison rate is not in the manuscript; the author must supply it",
                    "scientific_fidelity": "rate preserved", "new_terms_check": "none", "affected_block_ids": ["B0005"]}}}

    def audit(self, audit_round=1, verdict="preserved", wording=WORDING, **changed):
        checks = {name: "same" for name in AUDIT_CHECKS}
        checks.update(changed)
        return {"candidate_id": "C01", "proposed_wording": wording, "original": [ORIGINAL], "verdict": verdict, "checks": {**checks, "hedges": []},
                "notes": "Threshold kept; RVG spelled out from B0004.", "audit_round": audit_round, "auditor": {"mode": "bounded-request", "agent_id": "auditor-1"},
                "hedges_in_original": nb.rp.find_hedge_occurrences([ORIGINAL["quote"]]), "hedge_patterns": []}

    def write_audits(self):
        """Round 1 changed the quantifiers; round 2 preserved the meaning: the edit's wording must come from round 2."""
        self.write("audits/C01.audit.json", self.audit(1, "changed", OTHER_WORDING, quantifiers="changed"))
        self.write("audits/C01-2.audit.json", self.audit(2, "preserved"))

    def compare_result(self, wording=WORDING):
        return {"command": "compare", "comparisons": [nb.rs.df.lexical_comparison("C01", ORIGINAL["quote"], wording)]}

    # ---------------------------------------------------------------- tests

    def test_end_to_end_through_all_six_stages(self):
        result, reading = self.build_and_record("reading", [self.write("drafts/reading.json", self.reading_fragment())])
        self.assertEqual((result["ok"], result["command"], result["counts"]), (True, "reading", {"paragraphs": 4}))
        self.assertEqual(result["next"], f"review_steps.py record --stage reading --notes {result['output']}")
        self.assertEqual([p["anchor"]["block_id"] for p in reading["paragraphs"]], INCLUDED)
        self.assertEqual(reading["paragraphs"][0]["anchor"], A("B0002", "We recorded every visit to a single feeder over ten days. Each visit"))

        result, checks = self.build_and_record("checks", [self.write("drafts/checks.json", self.checks_fragment())])
        self.assertEqual(result["counts"], {"concepts": 1, "passages": 4, "checkpoint_plan": 1, "candidates": 3, "no_candidate": 0})
        self.assertEqual(checks["concepts"][0]["concept_id"], "K01")
        self.assertEqual(checks["passages"][1]["evidence"], [A("B0003", "Visits shorter than the RVG were merged")])
        self.assertEqual(checks["candidates"][1]["evidence"], [])

        result, reconcile = self.build_and_record("reconcile", [self.write("drafts/reconcile.json", self.fragment("reconcile"))])
        self.assertEqual(result["counts"], {"checkpoints": 0, "branches": 0, "observations": 0, "candidates": 1})
        self.assertEqual(reconcile["no_checkpoints_reason"], "none run")

        result, decisions = self.build_and_record("decisions", [self.write("drafts/decisions.json", self.decisions_fragment())])
        self.assertEqual(result["counts"], {"decisions": 4})
        self.assertEqual([d["candidate_id"] for d in decisions["decisions"]], ["C01", "C02", "C03", "C04"])
        self.assertEqual(decisions["decisions"][2]["evidence"], [])

        self.write_audits()
        result, repairs = self.build_and_record("repairs", [self.write("drafts/repairs.json", self.repairs_fragment())], audits=self.audits)
        self.assertEqual(result["counts"], {"repairs": 3})
        by_id = {r["candidate_id"]: r for r in repairs["repairs"]}
        self.assertEqual(by_id["C01"]["proposed_wording"], WORDING)
        self.assertEqual(by_id["C01"]["audit_path"], str(self.audits / "C01-2.audit.json"))
        self.assertEqual(result["audit_selection"], [{"candidate_id": "C01", "audit_round": 2, "audit_path": str(self.audits / "C01-2.audit.json"), "verdict": "preserved", "rounds_seen": 2}])
        self.assertEqual((by_id["C02"]["kind"], by_id["C02"]["proposed_wording"], by_id["C02"]["audit_path"]), ("author_question", None, None))
        self.assertIsNone(by_id["C04"]["proposed_wording"])

        result, review = self.build("review", [self.write("drafts/review.json", self.fragment("review"))])
        self.assertEqual(result["counts"], {"issues": 3, "accepted": 2, "needs_author": 1, "concept_ledger": 1})
        self.assertEqual(result["next"], "run review_steps.py ready, then check-final with this file")
        self.assertEqual(rs.ready_run(self.journal, self.index)["ready_to_draft"], True)
        final = rs.check_final(self.journal, self.index, review)
        self.assertEqual((final["command"], final["issue_count"]), ("check-final", 3))

    def test_fragments_from_python_modules(self):
        self.write("py/01-paragraphs.py", "\n".join([
            "def build(ctx):",
            "    assert ctx.included == ['B0002', 'B0003', 'B0004', 'B0005']",
            "    rows = {}",
            "    for b in ctx.included:",
            "        rows[b] = {'paraphrase': ctx.text[b][:10], 'role': 'r', 'required_context': [], 'new_information': [], 'open_questions': []}",
            "    rows['B0002']['open_questions'] = ['Which camera?']",
            "    rows['B0003']['anchor'] = ctx.q('B0003', 'the RVG')",
            "    rows['B0004']['anchor'] = ctx.opening('B0004', 20)",
            "    return {'paragraphs': rows}",
        ]))
        self.write("py/02-summary.py", "FRAGMENT = {'summary': 'Built from modules.'}\n")
        result, reading = self.build("reading", [self.root / "py"])
        self.assertEqual((result["ok"], reading["summary"]), (True, "Built from modules."))
        self.assertEqual(reading["paragraphs"][1]["anchor"], A("B0003", "the RVG"))
        # ctx.opening cuts at the last space inside the first n characters, so a word that ends exactly at n is dropped too
        self.assertEqual(reading["paragraphs"][2]["anchor"], A("B0004", "We call the"))
        self.assertEqual(reading["paragraphs"][0]["paraphrase"], "We recorde")
        self.write("neither/x.py", "VALUE = 1\n")
        self.assertRefused("defines neither FRAGMENT nor build\\(ctx\\)", "reading", [self.root / "neither"])
        self.write("notdict/y.py", "FRAGMENT = ['not', 'a', 'dict']\n")
        self.assertRefused("must be a JSON-like object, not list", "reading", [self.root / "notdict"])

    def test_merge_rules(self):
        self.write("parts/a.json", {"summary": "first", "paragraphs": self.reading_fragment(["B0002", "B0003"])["paragraphs"]})
        self.write("parts/b.json", {"summary": "second", "paragraphs": self.reading_fragment(["B0004", "B0005"])["paragraphs"]})
        result, reading = self.build("reading", [self.root / "parts"])
        self.assertEqual((reading["summary"], [p["anchor"]["block_id"] for p in reading["paragraphs"]]), ("second", INCLUDED))
        self.write("clash/a.json", {"summary": "s", "paragraphs": self.reading_fragment(["B0002", "B0003"])["paragraphs"]})
        self.write("clash/b.json", {"summary": "s", "paragraphs": self.reading_fragment(["B0003", "B0004", "B0005"])["paragraphs"]})
        self.assertRefused("b.json: paragraphs repeats B0003 already given by an earlier fragment", "reading", [self.root / "clash"])
        merged = nb.merge([(Path("a.json"), {"concepts": [{"term": "x"}], "summary": "a"}),
                           (Path("b.json"), {"concepts": [{"term": "y"}], "summary": "b", "passages": [{"block_id": "B0002", "k": 1}, {"block_id": "B0003", "k": 2}]})])
        self.assertEqual(merged, {"concepts": [{"term": "x"}, {"term": "y"}], "summary": "b", "passages": {"B0002": {"block_id": "B0002", "k": 1}, "B0003": {"block_id": "B0003", "k": 2}}})
        with self.assertRaisesRegex(nb.BuildError, "b.json:passages lists B0002 twice"):
            nb.merge([(Path("b.json"), {"passages": [{"block_id": "B0002"}, {"block_id": "B0002"}]})])
        with self.assertRaisesRegex(nb.BuildError, r"c.json:passages\[0\] must be an object with block_id"):
            nb.merge([(Path("c.json"), {"passages": [{"k": 1}]})])
        with self.assertRaisesRegex(nb.BuildError, "d.json: concepts must be a list"):
            nb.merge([(Path("d.json"), {"concepts": {"term": "x"}})])
        with self.assertRaisesRegex(nb.BuildError, "e.json:passages must be a mapping keyed by block_id or a list of rows"):
            nb.merge([(Path("e.json"), {"passages": "B0002"})])
        self.through("reading")
        fragment = self.checks_fragment()
        fragment["passages"] = [{"block_id": b, **row} for b, row in fragment["passages"].items()]
        result, checks = self.build("checks", [self.write("drafts/checks-list.json", fragment)])
        self.assertEqual([p["block_id"] for p in checks["passages"]], INCLUDED)

    def test_quote_verification(self):
        fragment = self.reading_fragment()
        fragment["paragraphs"]["B0003"]["anchor"] = "the feeder gap"
        message = r"1 quotation\(s\) are not exact text of the index: \$\.paragraphs\[1\]\.anchor: not exact text of B0003: 'the feeder gap'"
        self.assertRefused(message, "reading", [self.write("drafts/bad-quote.json", fragment)])
        self.write("ctxq/a.py", "def build(ctx):\n    return {'summary': 's', 'paragraphs': {b: {'anchor': ctx.q(b, 'no such words'), 'paraphrase': 'p', 'role': 'r', 'required_context': [], 'new_information': [], 'open_questions': []} for b in ctx.included}}\n")
        self.assertRefused(r"ctx\.q\(\) recorded quotes that are not exact text: B0002: 'no such words'; B0003: 'no such words'", "reading", [self.root / "ctxq"])
        blocks = {b["block_id"]: b for b in self.index["blocks"]}
        value = {"rows": [{"anchor": A("B0002", "every visit")}, {"anchor": A("B0002", "zzz")}, {"anchor": A("B0099", "x")}, {"anchor": A("B0003", " ")}]}
        self.assertEqual(nb.anchor_failures(value, blocks), ["$.rows[1].anchor: not exact text of B0002: 'zzz'",
                                                             "$.rows[2].anchor: block 'B0099' is not in the index",
                                                             "$.rows[3].anchor: empty quote for B0003"])
        self.assertEqual(nb.anchor_failures({"block_id": "B0002", "quote": "x", "note": "not an anchor shape"}, blocks), [])

    def test_coverage(self):
        self.assertRefused("Reading paragraphs must cover every included block exactly once; missing 1: B0005\\.", "reading",
                           [self.write("drafts/short.json", self.reading_fragment(["B0002", "B0003", "B0004"]))])
        self.assertRefused("not in scope: B0001", "reading", [self.write("drafts/wide.json", self.reading_fragment(["B0001", *INCLUDED]))])
        self.through("reading")
        self.assertRefused("Passage checks must cover every included block exactly once; missing 1: B0004", "checks",
                           [self.write("drafts/short-checks.json", self.checks_fragment(["B0002", "B0003", "B0005"]))])
        self.assertRefused("Passage checks must cover every included block exactly once; not in scope: B0001", "checks",
                           [self.write("drafts/wide-checks.json", self.checks_fragment(["B0001", *INCLUDED]))])

    def test_field_vocabularies_go_through_the_validator(self):
        self.through("reading")
        plan = self.checks_fragment()
        plan["checkpoint_plan"][0]["mode"] = "weird"
        self.assertRefused("checkpoint_plan.mode must be one of", "checks", [self.write("drafts/plan.json", plan)])
        self.through("reconcile")
        decisions = self.decisions_fragment()
        decisions["decisions"]["C01"]["category"] = "nonsense"
        self.assertRefused("decision.category must be one of", "decisions", [self.write("drafts/bad-decisions.json", decisions)])
        self.through("decisions")
        self.write_audits()
        repairs = self.repairs_fragment()
        repairs["repairs"]["C01"]["edit_scope"] = "huge"
        self.assertRefused("repair.edit_scope must be one of", "repairs", [self.write("drafts/bad-repairs.json", repairs)], audits=self.audits)

    def test_decisions_accounting(self):
        self.through("reconcile")
        decisions = self.decisions_fragment()
        decisions["decisions"]["C99"] = decisions["decisions"].pop("C03")
        self.assertRefused("Decisions must account for every candidate exactly once; missing: C03; unknown candidates: C99\\.", "decisions",
                           [self.write("drafts/d1.json", decisions)])
        decisions = self.decisions_fragment()
        del decisions["decisions"]["C01"]["severity"]
        self.assertRefused(r"Decision C01 \(accepted\) lacks severity\.", "decisions", [self.write("drafts/d2.json", decisions)])
        decisions = self.decisions_fragment()
        decisions["decisions"]["C02"]["diagnosis"] = ""
        self.assertRefused(r"Decision C02 \(needs_author\) lacks diagnosis\.", "decisions", [self.write("drafts/d3.json", decisions)])

    def test_repairs(self):
        self.through("decisions")
        self.write_audits()
        empty = self.root / "no-audits"
        empty.mkdir()
        # needs_author must carry a question that ends with a question mark
        repairs = self.repairs_fragment()
        del repairs["repairs"]["C02"]["author_question"]
        self.assertRefused("Repair C02: needs_author requires author_question ending with a question mark; got None", "repairs", [self.write("drafts/r1.json", repairs)], audits=self.audits)
        repairs = self.repairs_fragment()
        repairs["repairs"]["C02"]["author_question"] = "The author should say how the visits were counted."
        self.assertRefused("question mark", "repairs", [self.write("drafts/r2.json", repairs)], audits=self.audits)
        repairs = self.repairs_fragment()
        repairs["repairs"]["C02"]["kind"] = "edit"
        self.assertRefused("Repair C02: the decision is needs_author, so kind must be author_question", "repairs", [self.write("drafts/r3.json", repairs)], audits=self.audits)
        # an edit with no audit and no withheld reason
        self.assertRefused("Repair C01 is an edit with no proposed_wording and no audit under --audits; give wording_withheld_reason or audit a wording",
                           "repairs", [self.write("drafts/r4.json", self.repairs_fragment())], audits=empty)
        self.assertRefused("Repair C01 is an edit with no proposed_wording and no audit under --audits", "repairs", [self.root / "drafts/r4.json"])
        self.assertRefused("--audits .* is not a directory", "repairs", [self.root / "drafts/r4.json"], audits=self.root / "nowhere")
        # the latest audit found the meaning changed: no wording, a reason that names the round and the changed properties
        self.write("audits/C01.audit.json", self.audit(1, "preserved"))
        self.write("audits/C01-2.audit.json", self.audit(2, "changed", OTHER_WORDING, quantifiers="changed", numerical_values="changed"))
        result, repairs = self.build("repairs", [self.root / "drafts/r4.json"], audits=self.audits)
        row = {r["candidate_id"]: r for r in repairs["repairs"]}["C01"]
        self.assertIsNone(row["proposed_wording"])
        self.assertIsNone(row["audit_path"])
        self.assertEqual(row["wording_withheld_reason"], "the latest audited wording (round 2, C01-2.audit.json) was changed with changed quantifiers, numerical_values; no wording is recorded")
        self.assertEqual(result["audit_selection"], [{"candidate_id": "C01", "audit_round": 2, "audit_path": str(self.audits / "C01-2.audit.json"), "verdict": "changed", "rounds_seen": 2, "withheld": True}])
        self.assertEqual({r["candidate_id"]: r["kind"] for r in repairs["repairs"]}, {"C01": "edit", "C02": "author_question", "C04": "edit"})
        # a round number read from the file name when the audit lacks audit_round
        audits3 = self.root / "audits3"
        audits3.mkdir()
        self.write("audits3/C01.audit.json", self.audit(1, "preserved"))
        audit = self.audit(2, "changed", OTHER_WORDING, quantifiers="changed")
        del audit["audit_round"]
        self.write("audits3/C01-2.audit.json", audit)
        result, _ = self.build("repairs", [self.root / "drafts/r4.json"], audits=audits3)
        self.assertEqual((result["audit_selection"][0]["audit_round"], result["audit_selection"][0]["verdict"]), (2, "changed"))
        # proposed_wording given: bound to the audit that audited exactly that wording, or refused
        self.write("audits/C01-2.audit.json", self.audit(2, "preserved"))
        repairs = self.repairs_fragment()
        repairs["repairs"]["C01"]["proposed_wording"] = WORDING
        result, built = self.build("repairs", [self.write("drafts/r5.json", repairs)], audits=self.audits)
        row = {r["candidate_id"]: r for r in built["repairs"]}["C01"]
        self.assertEqual((row["proposed_wording"], row["audit_path"], row["compare_path"]), (WORDING, str(self.audits / "C01-2.audit.json"), None))
        self.assertEqual(result["audit_selection"], [{"candidate_id": "C01", "audit_round": 2, "audit_path": str(self.audits / "C01-2.audit.json"), "verdict": "preserved", "rounds_seen": 2}])
        repairs["repairs"]["C01"]["proposed_wording"] = "A wording that nobody audited."
        self.assertRefused("Repair C01 gives proposed_wording but no audit_path, and no audit under --audits audited that exact wording", "repairs",
                           [self.write("drafts/r6.json", repairs)], audits=self.audits)
        # <cid>.features.json beside the audits becomes compare_path; notes_build only looks for the file, the validator reads it
        self.write("audits/C01.features.json", {"not": "a compare result"})
        self.assertRefused("compare_path for C01 is not a difficulty_features.py compare result", "repairs", [self.root / "drafts/r5.json"], audits=self.audits)
        self.write("audits/C01.features.json", self.compare_result())
        result, built = self.build("repairs", [self.root / "drafts/r5.json"], audits=self.audits)
        row = {r["candidate_id"]: r for r in built["repairs"]}["C01"]
        self.assertEqual(row["compare_path"], str(self.audits / "C01.features.json"))
        compare = self.root / "compare"
        compare.mkdir()
        self.write("compare/C01.features.json", self.compare_result())
        result, built = self.build("repairs", [self.root / "drafts/r5.json"], audits=self.audits, compare=compare)
        self.assertEqual({r["candidate_id"]: r["compare_path"] for r in built["repairs"]}, {"C01": str(compare / "C01.features.json"), "C02": None, "C04": None})
        # a repair fragment that is not keyed by the accepted candidates
        repairs = self.repairs_fragment()
        repairs["repairs"]["C03"] = repairs["repairs"].pop("C04")
        self.assertRefused("Repairs must correspond exactly to accepted and needs_author candidates; missing: C04; not accepted or needs_author: C03", "repairs",
                           [self.write("drafts/r7.json", repairs)], audits=self.audits)
        repairs = self.repairs_fragment()
        del repairs["repairs"]["C01"]["new_terms_check"]
        self.assertRefused("Repair C01 lacks new_terms_check", "repairs", [self.write("drafts/r8.json", repairs)], audits=self.audits)

    def test_reconcile_expands_prefix_checkpoints(self):
        self.through("checks")
        events = self.root / "events"
        events.mkdir()
        packet = rp.build_packet(self.index, PROFILE, through_block="B0003")
        observation = {k: packet[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256")}
        observation.update(understanding="Visits are merged by a gap the text has not defined.",
                           issues=[{"id": "R1", "category": "unavailable_prerequisite", "anchor": A("B0003", "the RVG"), "severity": "major", "confidence": "high",
                                    "reader_question": "What is the RVG?", "consequence": "the merging rule cannot be applied"}],
                           cannot_place=[{"anchor": A("B0003", "the preceding visit"), "note": "which visit precedes is unclear"}])
        self.assertEqual(rp.check_observation(packet, observation)["cannot_place_count"], 1)
        self.write("events/P01.packet.json", packet)
        self.write("events/P01.observation.json", observation)

        def fragment(**point):
            entry = {"id": "K1", "plan_id": "P01", "events_dir": str(events), "limitation": "the reader saw text only", **point}
            return {"summary": "One prefix reader.", "checkpoints": [entry],
                    "observations": [{"source_id": "K1", "reader_question": "What is the RVG?", "classification": "absent_at_critical_use", "editor_check": "agrees with C01", "evidence": [CRITICAL], "candidate_id": "C01"}],
                    "candidates": [{"id": "C04", "anchor": A("B0005", "63 percent"), "question": "Compared with what rate?"}]}

        result, reconcile = self.build("reconcile", [self.write("drafts/rc1.json", fragment())])
        self.assertEqual(result["counts"], {"checkpoints": 1, "branches": 0, "observations": 1, "candidates": 1})
        point = reconcile["checkpoints"][0]
        self.assertEqual((point["packet_path"], point["observation_path"]), (str(events / "P01.packet.json"), str(events / "P01.observation.json")))
        self.assertEqual((point["anchor"], point["mode"], point["figure_policy"], point["limitation"]), (CRITICAL, "fresh-prefix", "text-only", "the reader saw text only"))
        self.assertIn("1 issue(s), 1 cannot_place entry; understanding: Visits are merged by a gap", point["result"])
        self.assertEqual(reconcile["observations"][0]["id"], "O01")
        self.assertNotIn("no_checkpoints_reason", reconcile)
        # build already ran the record validator (rs.validate_notes reads the packet and observation), so the output records
        self.assertEqual(self.record("reconcile", result["output"])["next_stage"], "decisions")
        (self.journal / "03-reconcile.notes.json").unlink()
        (self.journal / "03-reconcile.receipt.json").unlink()

        _, given = self.build("reconcile", [self.write("drafts/rc5.json", fragment(result="the reader asked what the RVG is"))])
        self.assertEqual(given["checkpoints"][0]["result"], "the reader asked what the RVG is")
        # a mode given on the point overrides the plan's, but the validator then refuses the mismatch with the plan entry
        self.assertRefused("A prefix checkpoint must cite a checks-stage plan entry with the same anchor and mode", "reconcile",
                           [self.write("drafts/rc5b.json", fragment(mode="bounded-request"))])
        self.assertRefused("Checkpoint K1: plan_id P09 is not in the recorded checkpoint_plan", "reconcile", [self.write("drafts/rc2.json", fragment(plan_id="P09", label="P01"))])
        self.assertRefused("Checkpoint K1 needs a limitation sentence", "reconcile", [self.write("drafts/rc3.json", fragment(limitation=""))])
        self.assertRefused(r"Checkpoint K1: P02\.packet\.json or P02\.observation\.json is missing under", "reconcile", [self.write("drafts/rc4.json", fragment(label="P02"))])
        clash = fragment()
        clash["candidates"][0]["id"] = "C02"
        self.assertRefused(r"Reconcile candidates reuse checks ids: C02; give new ids \(the checks stage used up to C03\)", "reconcile", [self.write("drafts/rc6.json", clash)])
        self.assertRefused("reconcile with no checkpoints needs no_checkpoints_reason", "reconcile",
                           [self.write("drafts/rc7.json", {"summary": "s", "candidates": []})])
        (events / "P01.observation.json").unlink()
        self.assertRefused(r"Checkpoint K1: P01\.packet\.json or P01\.observation\.json is missing under", "reconcile", [self.root / "drafts/rc1.json"])

    def test_journal_gating(self):
        self.assertRefused("Building checks needs the earlier stages recorded in the journal; missing: reading\\.", "checks",
                           [self.write("drafts/checks.json", self.checks_fragment())])
        self.assertRefused("The review needs every stage recorded; missing: reading, checks, reconcile, decisions, repairs\\.", "review",
                           [self.write("drafts/review.json", self.fragment("review"))])
        self.through("reading")
        self.assertRefused("The journal already records reading; build the next stage, or start a corrected run", "reading",
                           [self.write("drafts/reading.json", self.reading_fragment())])
        self.assertRefused("Building repairs needs the earlier stages recorded in the journal; missing: checks, reconcile, decisions\\.", "repairs",
                           [self.write("drafts/repairs.json", self.repairs_fragment())])
        self.assertRefused("The review needs every stage recorded; missing: checks, reconcile, decisions, repairs\\.", "review",
                           [self.write("drafts/review.json", self.fragment("review"))])
        with self.assertRaisesRegex(nb.BuildError, "stage must be one of: reading, checks, reconcile, decisions, repairs, review"):
            nb.build("drafting", self.journal, self.index_path, [], self.out_path("x"), None, None)

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = nb.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli(self):
        source = self.write("drafts/reading.json", self.reading_fragment())
        out = self.root / "reading.json"
        code, stdout, stderr = self.run_cli(["reading", "--journal", str(self.journal), "--index", str(self.index_path), "--source", str(source), "--out", str(out)])
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads(stdout)
        self.assertEqual((result["ok"], result["command"], result["output"], result["counts"]), (True, "reading", str(out), {"paragraphs": 4}))
        self.assertEqual(rp.load_json(out)["summary"], "Four paragraphs read.")
        code, stdout, stderr = self.run_cli(["reading", "--journal", str(self.journal), "--index", str(self.index_path), "--source", str(source), "--out", str(out)])
        self.assertEqual((code, stdout), (2, ""))
        error = json.loads(stderr)
        self.assertEqual((error["ok"], error["error"]["code"]), (False, "output_exists"))
        code, stdout, stderr = self.run_cli(["drafting", "--journal", str(self.journal), "--index", str(self.index_path), "--source", str(source), "--out", str(self.root / "x.json")])
        self.assertEqual((code, json.loads(stderr)["error"]["code"]), (2, "cli_error"))
        code, stdout, stderr = self.run_cli(["reading", "--journal", str(self.journal), "--index", str(self.index_path), "--source", str(self.root / "missing"), "--out", str(self.root / "y.json")])
        self.assertEqual(code, 2)
        self.assertIn("--source", json.loads(stderr)["error"]["message"])
        self.assertFalse((self.root / "y.json").exists())

    def test_review_stage(self):
        self.through("repairs")
        fragment = {"summary": "Three findings.", "coverage": "all four paragraphs read", "issues": [{"id": "ignored"}], "snapshot_id": "ignored"}
        result, review = self.build("review", [self.write("drafts/review.json", fragment)])
        self.assertEqual(result["counts"], {"issues": 3, "accepted": 2, "needs_author": 1, "concept_ledger": 1})
        self.assertEqual((review["snapshot_id"], review["summary"], review["coverage"]), (self.sid, "Three findings.", "all four paragraphs read"))
        self.assertEqual(set(review), {"snapshot_id", "summary", "issues", "coverage", "concept_ledger"})
        issues = review["issues"]
        self.assertEqual([(i["id"], i["candidate_id"], i["decision_status"]) for i in issues], [("W01", "C01", "accepted"), ("W02", "C02", "needs_author"), ("W03", "C04", "accepted")])
        self.assertEqual(issues[0]["evidence"], [DEFINITION, SETTING])
        self.assertEqual((issues[0]["anchor"], issues[0]["category"], issues[0]["severity"], issues[0]["confidence"]), (CRITICAL, "late_prerequisite", "major", "high"))
        self.assertEqual((issues[0]["proposed_wording"], issues[0]["action"], issues[0]["scientific_fidelity"]), (WORDING, "Replace the RVG in B0003 with the gap it names.", "threshold preserved"))
        self.assertNotIn("author_question", issues[0])
        self.assertEqual(issues[1]["author_question"], "How were the 1284 visits counted?")
        self.assertNotIn("evidence", issues[1])
        self.assertNotIn("author_question", issues[2])
        self.assertIsNone(issues[2]["proposed_wording"])
        self.assertEqual(review["concept_ledger"], [{"concept_id": "K01", "term": "RVG", "first_mention": CRITICAL, "earliest_usable_explanation": DEFINITION, "first_critical_use": CRITICAL, "assessment": "used in B0003 before B0004 defines it"}])
        self.assertEqual(rp.check_review(self.index, review)["issue_count"], 3)
        self.assertRefused("review fragments need a nonempty summary", "review", [self.write("drafts/review-nosummary.json", {"coverage": "x"})])

    def test_reading_fields_and_assigned_ids(self):
        fragment = self.reading_fragment()
        fragment["paragraphs"]["B0003"]["required_context"] = "RVG"
        fragment["paragraphs"]["B0004"]["new_information"] = ""
        del fragment["paragraphs"]["B0005"]["role"]
        self.assertRefused("Reading paragraph B0005 lacks role", "reading", [self.write("drafts/lacking.json", fragment)])
        fragment["paragraphs"]["B0005"]["role"] = "closing count"
        result, reading = self.build_and_record("reading", [self.write("drafts/strings.json", fragment)])
        self.assertEqual((reading["paragraphs"][1]["required_context"], reading["paragraphs"][2]["new_information"]), (["RVG"], []))
        checks = self.checks_fragment()
        checks["candidates"] = [{k: v for k, v in c.items() if k != "id"} for c in checks["candidates"]]
        checks["candidates"][1]["id"] = "C01"
        checks["no_candidate"] = [{"block_id": "B0002", "question": "Which camera?", "reason": "the camera is named in B0002"}]
        del checks["concepts"]
        self.assertRefused("checks with no concepts need no_concepts_reason", "checks", [self.write("drafts/noconcepts.json", checks)])
        checks["no_concepts_reason"] = "no term questioned"
        self.assertRefused("no_candidate contradicts a candidate that already carries the question", "checks", [self.write("drafts/contradict.json", checks)])
        checks["candidates"] = checks["candidates"][:2]
        result, built = self.build("checks", [self.write("drafts/autoids.json", checks)])
        # with_ids fills unused numbers in order (its docstring says "continuing after the highest given one"; with C01 given the two read the same)
        self.assertEqual([c["id"] for c in built["candidates"]], ["C02", "C01"])
        self.assertEqual(nb.with_ids([{"id": "C03"}, {}, {}], "C"), [{"id": "C03"}, {"id": "C01"}, {"id": "C02"}])
        self.assertEqual(result["counts"], {"concepts": 0, "passages": 4, "checkpoint_plan": 1, "candidates": 2, "no_candidate": 1})
        with self.assertRaisesRegex(nb.BuildError, "K rows must be objects"):
            nb.with_ids(["RVG"], "K", "concept_id")

    def test_edit_with_withheld_reason_may_omit_proposed_wording(self):
        # workflow.md: --audits decides only for an edit without proposed_wording AND without wording_withheld_reason;
        # an edit that gives the reason and omits the key gets proposed_wording null.
        self.through("decisions")
        self.write_audits()
        fragment = self.repairs_fragment()
        del fragment["repairs"]["C04"]["proposed_wording"]
        result, repairs = self.build("repairs", [self.write("drafts/omit.json", fragment)], audits=self.audits)
        self.assertEqual({r["candidate_id"]: r["proposed_wording"] for r in repairs["repairs"]}, {"C01": WORDING, "C02": None, "C04": None})


if __name__ == "__main__":
    unittest.main()
