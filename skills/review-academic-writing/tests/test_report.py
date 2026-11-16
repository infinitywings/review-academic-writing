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

TEXT = """# Thaw timing in shallow ponds

We logged the ice cover of twelve ponds each spring. Each pond was visited at dawn.

Thaw came earlier when the DLI rose, so we kept only ponds with a full record.

We call the light a pond receives in one day the daily light integral (DLI). We measured the DLI with one sensor.

The kept set held 9 ponds. Thaw in this set came 6 days earlier than in the reference decade.

Earlier thaw may lengthen the growing season. The sensor was cleaned weekly.
"""
PROFILE = {"audience": "A trained ecologist new to this field site"}
INCLUDED = ["B0002", "B0003", "B0004", "B0005", "B0006"]
# One candidate per phrase, C01 to C16, in document order.
PHRASES = [("B0002", "We logged the ice cover"), ("B0002", "twelve ponds"), ("B0002", "each spring"), ("B0002", "visited at dawn"),
           ("B0003", "Thaw came earlier"), ("B0003", "the DLI rose"), ("B0003", "a full record"),
           ("B0004", "We call the light a pond receives in one day the daily light integral (DLI)."), ("B0004", "one sensor"),
           ("B0005", "The kept set"), ("B0005", "9 ponds"), ("B0005", "6 days earlier"), ("B0005", "the reference decade"),
           ("B0006", "Earlier thaw may lengthen"), ("B0006", "the growing season"), ("B0006", "cleaned weekly")]
# Twelve accepted findings that mislead or hinder, one accepted minor item, an author question that hinders, a rejected candidate, a minor author question.
WIDE = [("accepted", "hinders"), ("accepted", "hinders"), ("accepted", "misleads"), ("accepted", "hinders"), ("accepted", "hinders"), ("accepted", "hinders"),
        ("accepted", "misleads"), ("accepted", "hinders"), ("accepted", "hinders"), ("accepted", "hinders"), ("accepted", "hinders"), ("accepted", "hinders"),
        ("accepted", "minor"), ("needs_author", "hinders"), ("rejected", None), ("needs_author", "minor")]
WIDE_PRIORITY = ["C03", "C07", "C01", "C02", "C04", "C05", "C06", "C08", "C09", "C10"]
SCOPE_NOTE = "The plain-text edition was read in full (B0002 to B0006); two full-context checkpoints were recorded and no fresh reader was run."
NOT_ASSESSED = ["whether the claims are true", "whether the evidence is sufficient", "whether the work is novel"]


def cid(number):
    return f"C{number:02d}"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.index = rp.build_index(TEXT)
        self.sid = self.index["snapshot_id"]
        self.blocks = {b["block_id"]: b for b in self.index["blocks"]}
        self.index_path = self.root / "index.json"
        self.index_path.write_text(json.dumps(self.index), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, journal, stage, notes):
        rs.record_stage(journal, self.index, stage, json.dumps({"snapshot_id": self.sid, "summary": stage + " notes", **notes}).encode("utf-8"))

    def journal(self, name, specs, raised=None, challenged=True, schema=None):
        """A ready journal with one candidate per spec (status, severity) on PHRASES; raised maps a candidate to the checkpoints (K1, K2) that raised it."""
        journal = self.root / name
        scope = {"task": "review", "included_block_ids": INCLUDED, "reading_path": "linear", "extraction_notes": "plain", "omissions": [{"block_id": "B0001", "reason": "title"}]}
        rs.start_run(self.index, PROFILE, scope, journal)
        if schema is not None:
            path = journal / "00-start.json"
            start = json.loads(path.read_text())
            path.write_text(json.dumps(rs.sealed({**{k: v for k, v in start.items() if k != "receipt_sha256"}, "schema_version": schema})))
        anchors = [{"block_id": block, "quote": quote} for block, quote in PHRASES[:len(specs)]]
        claim_check = {"claim_strength": "none in this block", "counterexample": "no rule to try", "citation_check": "no citation"}
        self.record(journal, "reading", {"paragraphs": [{"anchor": {"block_id": b, "quote": self.blocks[b]["text"][:12]}, "paraphrase": "p", "role": "r", "required_context": [],
                                                         "new_information": [], "open_questions": []} for b in INCLUDED]})
        candidates = [{"id": cid(n), "anchor": anchor, "question": f"What does phrase {n} refer to?", "evidence": []} for n, anchor in enumerate(anchors, 1)]
        self.record(journal, "checks", {"concepts": [], "no_concepts_reason": "none questioned", "checkpoint_plan": [], "no_candidate": [], "candidates": candidates,
                                        "passages": [{"block_id": b, "structure_check": "s", "wording_check": "w", "claim_check": claim_check,
                                                      "evidence": [{"block_id": b, "quote": self.blocks[b]["text"][:12]}]} for b in INCLUDED]})
        pairs = [(key, source) for key, sources in (raised or {}).items() for source in sources]
        points = [{"id": source, "anchor": anchors[0], "mode": "full-context", "figure_policy": "text-only", "result": "an editorial reading", "limitation": "not an independent reader"}
                  for source in sorted({source for _key, source in pairs})]
        observations = [{"id": f"O{n}", "source_id": source, "reader_question": "What does this refer to?", "classification": "absent_at_critical_use",
                         "editor_check": "no referent before this point", "evidence": [], "candidate_id": key} for n, (key, source) in enumerate(pairs, 1)]
        self.record(journal, "reconcile", {"checkpoints": points, **({} if points else {"no_checkpoints_reason": "none run"}), "branches": [], "observations": observations, "candidates": []})
        decisions = []
        for n, (status, severity) in enumerate(specs, 1):
            row = {"candidate_id": cid(n), "status": status, "verification": "v", "countercheck": "c", "evidence": []}
            if status != "rejected":
                row.update(category="ambiguous_reference", diagnosis=f"Phrase {n} has no referent the reader can find.", close_criterion=f"The reader can say what phrase {n} refers to.",
                           severity=severity, confidence="medium")
            decisions.append(row)
        if challenged and decisions:
            joined = rp.check_challenges([{"candidate_id": c["id"], "anchor": c["anchor"], "question": c["question"]} for c in candidates],
                                         [{"candidate_id": c["id"], "verdict": "stands", "answer": None, "duplicate_of": None, "reason": "the question is open at this point"} for c in candidates], self.blocks)
            path = self.root / f"{name}-challenge.json"
            path.write_text(json.dumps({"snapshot_id": self.sid, "challenges": joined, "challenger": {"mode": "bounded-request", "agent_id": "challenger-1"}}), encoding="utf-8")
            for row in decisions:
                row["challenge_path"] = str(path)
        self.record(journal, "decisions", {"decisions": decisions, **({} if challenged else {"no_challenge_reason": "no challenger could be run in this environment"})})
        repairs = []
        for n, (status, _severity) in enumerate(specs, 1):
            block = PHRASES[n - 1][0]
            common = {"candidate_id": cid(n), "proposed_wording": None, "scientific_fidelity": "no claim is changed", "new_terms_check": "none", "affected_block_ids": [block]}
            if status == "accepted":
                repairs.append({**common, "kind": "edit", "edit_scope": "term", "action": f"Name the referent of phrase {n} in {block}.",
                                "wording_withheld_reason": "the manuscript does not name the referent", "author_question": None})
            elif status == "needs_author":
                repairs.append({**common, "kind": "author_question", "action": f"Ask the author what phrase {n} in {block} refers to.", "author_question": f"What does phrase {n} refer to in your data?"})
        self.record(journal, "repairs", {"repairs": repairs})
        rs.ready_run(journal, self.index)
        return journal

    def review(self, journal, **fragment):
        """The review notes_build.py builds for the journal from a fragment with a summary, the standard scope note, and the given extras."""
        source = self.root / f"{journal.name}-fragment.json"
        source.write_text(json.dumps({"summary": "The referents of several phrases are missing.", "scope_note": SCOPE_NOTE, **fragment}), encoding="utf-8")
        out = self.root / f"{journal.name}-review.json"
        nb.build("review", journal, self.index_path, [str(source)], out, None, None)
        return rp.load_json(out)

    def wide(self):
        return self.journal("wide", WIDE, raised={"C03": ["K1", "K2"], "C01": ["K1"]})

    def cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = nb.main([str(a) for a in argv])
        return code, stdout.getvalue(), stderr.getvalue()

    def render(self, journal, review, name="report.md", *extra):
        path = self.root / f"{journal.name}-checked-review.json"
        path.write_text(json.dumps(review), encoding="utf-8")
        return self.cli("report", "--journal", journal, "--index", self.index_path, "--review", path, "--out", self.root / name, *extra)

    # ---------------------------------------------------------------- the review frame

    def test_the_builder_fills_in_the_frame_and_each_issue_s_recorded_support(self):
        journal = self.journal("small", [("accepted", "hinders"), ("accepted", "misleads"), ("accepted", "minor"), ("needs_author", "hinders"), ("accepted", "misleads"), ("rejected", None)],
                               raised={"C02": ["K1", "K2"], "C01": ["K2"]})
        review = self.review(journal, not_assessed=["whether the statistics are appropriate", "whether the work is novel"])
        self.assertEqual(review["scope_note"], SCOPE_NOTE)
        self.assertEqual(review["not_assessed"], [*NOT_ASSESSED, "whether the statistics are appropriate"])
        self.assertEqual(review["priority_list"], ["C02", "C05", "C01"], "misleads before hinders, then document order; no minor item, no author question")
        self.assertEqual([(i["candidate_id"], i["reader_sources"], i["challenge"], i["initial_reader_question"]) for i in review["issues"]],
                         [("C01", 1, "stands", "What does phrase 1 refer to?"), ("C02", 2, "stands", "What does phrase 2 refer to?"), ("C03", 0, "stands", "What does phrase 3 refer to?"),
                          ("C04", 0, "stands", "What does phrase 4 refer to?"), ("C05", 0, "stands", "What does phrase 5 refer to?")])
        self.assertEqual(rs.check_final(journal, self.index, review)["issue_count"], 5)
        source = self.root / "no-scope-note.json"
        source.write_text(json.dumps({"summary": "The referents of several phrases are missing."}), encoding="utf-8")
        with self.assertRaisesRegex(nb.BuildError, "review fragments need scope_note: what was read \\(edition, sections or blocks, reader runs\\) and what was left out\\."):
            nb.build("review", journal, self.index_path, [str(source)], self.root / "unframed.json", None, None)
        self.assertFalse((self.root / "unframed.json").exists())
        source.write_text(json.dumps({"summary": "s", "scope_note": SCOPE_NOTE, "priority_list": ["C01", "C02", "C05"]}), encoding="utf-8")
        with self.assertRaisesRegex(rp.ReviewDataError, "review\\.priority_list puts C01 \\(hinders\\) before C02 \\(misleads\\); misleads entries come before hinders entries\\."):
            nb.build("review", journal, self.index_path, [str(source)], self.root / "misordered.json", None, None)
        self.assertFalse((self.root / "misordered.json").exists(), "a priority list that breaks the rules is refused when the review is built")

    def test_challenge_input_reports_how_many_readers_raised_each_candidate(self):
        journal = self.journal("counted", WIDE[:3], raised={"C03": ["K1", "K2"], "C01": ["K1"]})
        result = rs.challenge_inputs(journal, self.index, self.root / "challenge-inputs", 10)
        self.assertEqual(result["support"], [{"candidate_id": "C01", "reader_sources": 1}, {"candidate_id": "C02", "reader_sources": 0}, {"candidate_id": "C03", "reader_sources": 2}])
        self.assertEqual(sorted(rp.load_json(result["inputs"][0])["candidates"][0]), ["anchor", "candidate_id", "question"], "the count is for the editor; the challenger's input does not carry it")

    def test_check_final_holds_the_priority_list_to_its_rules(self):
        journal = self.wide()
        review = self.review(journal)
        self.assertEqual(review["priority_list"], WIDE_PRIORITY)
        self.assertEqual(rs.check_final(journal, self.index, review)["issue_count"], 15)
        cases = (
            ([*WIDE_PRIORITY, "C11"], "review\\.priority_list holds 11 entries; the limit is 10\\. Keep the most consequential; the report lists every finding under Findings\\."),
            ([*WIDE_PRIORITY[:9], "C13"], "review\\.priority_list names C13 \\(accepted, minor\\); a minor item or an author question cannot be on the priority list\\."),
            ([*WIDE_PRIORITY[:9], "C14"], "review\\.priority_list names C14 \\(needs_author, hinders\\); a minor item or an author question cannot be on the priority list\\."),
            (["C01", "C03", "C07", *WIDE_PRIORITY[3:]], "review\\.priority_list puts C01 \\(hinders\\) before C03 \\(misleads\\); misleads entries come before hinders entries\\."),
            (["C03", *WIDE_PRIORITY[2:], "C11"], "review\\.priority_list leaves out C07 \\(misleads\\) while it lists C11 \\(hinders\\); list the more severe finding instead\\."),
            (WIDE_PRIORITY[:9], "review\\.priority_list holds 9 of the 12 accepted findings that mislead or hinder; it must hold 10 of them\\."),
            ([*WIDE_PRIORITY[:9], "C03"], "review\\.priority_list names a candidate twice; list each finding once\\."),
            ([*WIDE_PRIORITY[:9], "C15"], "review\\.priority_list names C15, which is not a final issue of this review\\."),
            ("C03", "review\\.priority_list must be an array\\."),
        )
        for priority, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(rp.ReviewDataError, message):
                rs.check_final(journal, self.index, {**review, "priority_list": priority})
        same_severity = [*WIDE_PRIORITY[:9], "C12"]
        self.assertEqual(rs.check_final(journal, self.index, {**review, "priority_list": same_severity})["issue_count"], 15, "among findings of one severity the editor chooses which ten to list")
        few = self.journal("few", WIDE[:4])
        with self.assertRaisesRegex(rp.ReviewDataError, "review\\.priority_list holds 3 of the 4 accepted findings that mislead or hinder; it must hold all of them\\."):
            rs.check_final(few, self.index, {**self.review(few), "priority_list": ["C03", "C01", "C02"]})

    def test_check_final_holds_the_scope_note_the_not_assessed_list_and_the_support(self):
        journal = self.wide()
        review = self.review(journal)

        def issue_with(number, **change):
            return {**review, "issues": [{**issue, **change} if issue["candidate_id"] == cid(number) else issue for issue in review["issues"]]}

        without_sources = issue_with(3)
        without_sources["issues"] = [{k: v for k, v in issue.items() if k != "reader_sources"} for issue in without_sources["issues"]]
        cases = (
            ({k: v for k, v in review.items() if k != "scope_note"}, "review\\.scope_note must say, as a nonempty string, what was read \\(edition, sections or blocks, reader runs\\) and what was left out\\."),
            ({**review, "scope_note": "  "}, "review\\.scope_note must say, as a nonempty string, what was read"),
            ({**review, "not_assessed": NOT_ASSESSED[:2]}, "review\\.not_assessed must include: whether the work is novel\\. The review is about clarity only; add the entries \\(notes_build\\.py review inserts them\\)\\."),
            ({k: v for k, v in review.items() if k != "not_assessed"}, "review\\.not_assessed must be an array\\."),
            (issue_with(3, reader_sources=5), "Final issue C03\\.reader_sources is 5, but the records give 2; copy the recorded value \\(notes_build\\.py review fills it in\\)\\."),
            (issue_with(1, reader_sources=True), "Final issue C01\\.reader_sources is true, but the records give 1"),
            (without_sources, "Final issue C01\\.reader_sources is null, but the records give 1"),
            (issue_with(2, challenge="answered"), "Final issue C02\\.challenge is \"answered\", but the records give \"stands\""),
            (issue_with(2, challenge=None), "Final issue C02\\.challenge is null, but the records give \"stands\""),
        )
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(rp.ReviewDataError, message):
                rs.check_final(journal, self.index, value)
        unchallenged = self.journal("unchallenged", WIDE[:3], challenged=False)
        self.assertEqual([i["challenge"] for i in self.review(unchallenged)["issues"]], [None, None, None])

    # ---------------------------------------------------------------- the rendered report

    def test_report_lists_ten_priorities_and_every_finding_once(self):
        journal = self.wide()
        code, stdout, stderr = self.render(journal, self.review(journal))
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads(stdout)
        self.assertEqual((result["ok"], result["command"], result["output"], result["appendix"], result["counts"]),
                         (True, "report", str(self.root / "report.md"), str(self.root / "report-minor.md"), {"priority_list": 10, "findings": 13, "minor": 2}))
        report = (self.root / "report.md").read_text(encoding="utf-8")
        lines = report.split("\n")
        self.assertEqual(lines[:9], [
            "# Writing review",
            "",
            "## What was reviewed",
            SCOPE_NOTE,
            "",
            "Not assessed: whether the claims are true; whether the evidence is sufficient; whether the work is novel.",
            "",
            "Records: 5 of 6 blocks read; reader checkpoints: 2 full-context; candidates: 13 accepted, 2 author questions, 0 merged, 1 rejected; "
            "independent challenge: run on all 16 candidates; fidelity audits attached: 0.",
            "",
        ])
        start = lines.index("## Priority list")
        self.assertEqual(lines[start:start + 14], [
            "## Priority list",
            '1. [misleads] B0002 "each spring" — What does phrase 3 refer to? (W03)',
            '2. [misleads] B0003 "a full record" — What does phrase 7 refer to? (W07)',
            '3. [hinders] B0002 "We logged the ice cover" — What does phrase 1 refer to? (W01)',
            '4. [hinders] B0002 "twelve ponds" — What does phrase 2 refer to? (W02)',
            '5. [hinders] B0002 "visited at dawn" — What does phrase 4 refer to? (W04)',
            '6. [hinders] B0003 "Thaw came earlier" — What does phrase 5 refer to? (W05)',
            '7. [hinders] B0003 "the DLI rose" — What does phrase 6 refer to? (W06)',
            '8. [hinders] B0004 "We call the light a pond receives in one day the daily light…" — What does phrase 8 refer to? (W08)',
            '9. [hinders] B0004 "one sensor" — What does phrase 9 refer to? (W09)',
            '10. [hinders] B0005 "The kept set" — What does phrase 10 refer to? (W10)',
            "",
            "The list shows the 10 most consequential of 12; all are under Findings.",
            "",
        ])
        questions = lines.index("## Questions for the author")
        self.assertEqual(lines[questions:questions + 4], ["## Questions for the author", "- W14, B0006: What does phrase 14 refer to in your data?",
                                                          "- W15, B0006: What does phrase 16 refer to in your data?", ""])
        headings = [line.split(" ")[1] for line in lines if line.startswith("### ")]
        self.assertEqual(headings, ["W03", "W07", "W01", "W02", "W04", "W05", "W06", "W08", "W09", "W10", "W11", "W12", "W14"],
                         "every finding that misleads or hinders once: the priority list first, then the rest in issue order")
        finding = lines.index("### W03 · misleads · confidence medium · B0002")
        self.assertEqual(lines[finding:finding + 10], [
            "### W03 · misleads · confidence medium · B0002",
            "> each spring",
            "",
            "- Reader's question: What does phrase 3 refer to?",
            "- Diagnosis: Phrase 3 has no referent the reader can find.",
            "- Action: Name the referent of phrase 3 in B0002.",
            "- Meaning check: no claim is changed",
            "- Done when: The reader can say what phrase 3 refers to.",
            "- Support: raised at 2 reader checkpoints; challenger: stands.",
            "",
        ])
        self.assertIn("- Support: raised at 1 reader checkpoint; challenger: stands.", lines)
        asked = lines.index("### W14 · hinders · confidence medium · B0006")
        self.assertEqual(lines[asked + 5:asked + 7], ["- Action: Ask the author what phrase 14 in B0006 refers to.", "- Question for the author: What does phrase 14 refer to in your data?"])
        self.assertEqual(lines[-3:], ["## Minor items", "2 minor items do not hinder understanding; they are listed in report-minor.md.", ""])
        self.assertNotIn("W13", report)
        self.assertNotIn("### W15", report)
        appendix = (self.root / "report-minor.md").read_text(encoding="utf-8").split("\n")
        self.assertEqual(appendix[:3], ["# Writing review: minor items", "", "### W13 · minor · confidence medium · B0005"])
        self.assertEqual([line.split(" ")[1] for line in appendix if line.startswith("### ")], ["W13", "W15"])
        self.assertIn("- Question for the author: What does phrase 16 refer to in your data?", appendix)

    def test_report_refuses_existing_files_and_a_review_that_fails_the_final_check(self):
        journal = self.wide()
        review = self.review(journal)
        code, stdout, stderr = self.render(journal, {**review, "priority_list": WIDE_PRIORITY[:9]}, "bad.md")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("review.priority_list holds 9 of the 12 accepted findings that mislead or hinder", json.loads(stderr)["error"]["message"])
        self.assertFalse((self.root / "bad.md").exists())
        (self.root / "taken-minor.md").write_text("an earlier appendix\n", encoding="utf-8")
        code, stdout, stderr = self.render(journal, review, "taken.md")
        self.assertEqual((code, json.loads(stderr)["error"]), (2, {"code": "output_exists", "message": f"Output already exists: {self.root / 'taken-minor.md'}; choose a new path."}))
        self.assertFalse((self.root / "taken.md").exists(), "nothing is written when either output exists")
        code, stdout, stderr = self.render(journal, review, "taken.md", "--appendix", self.root / "elsewhere.md")
        self.assertEqual((code, json.loads(stdout)["appendix"]), (0, str(self.root / "elsewhere.md")))
        self.assertTrue((self.root / "taken.md").read_text(encoding="utf-8").rstrip("\n").endswith("they are listed in elsewhere.md."))
        code, stdout, stderr = self.render(journal, review, "taken.md", "--appendix", self.root / "third.md")
        self.assertEqual((code, json.loads(stderr)["error"]["code"]), (2, "output_exists"))
        code, stdout, stderr = self.cli("report", "--journal", journal, "--index", self.index_path, "--out", self.root / "no-review.md")
        self.assertEqual((code, json.loads(stderr)["error"]), (2, {"code": "cli_error", "message": "report needs --review PATH: the review.json that passed check-final."}))

    def test_report_of_minor_items_only_and_of_no_findings(self):
        journal = self.journal("minor-only", [("accepted", "minor")], challenged=False)
        code, stdout, stderr = self.render(journal, self.review(journal), "minor-only.md")
        self.assertEqual((code, stderr), (0, ""))
        lines = (self.root / "minor-only.md").read_text(encoding="utf-8").split("\n")
        self.assertEqual(lines[7:], [
            "Records: 5 of 6 blocks read; reader checkpoints: none; candidates: 1 accepted, 0 author questions, 0 merged, 0 rejected; "
            "independent challenge: not run (no challenger could be run in this environment); fidelity audits attached: 0.",
            "",
            "## Priority list",
            "No finding that misleads or hinders was accepted.",
            "",
            "## Findings",
            "None.",
            "",
            "## Minor items",
            "1 minor item does not hinder understanding; it is listed in minor-only-minor.md.",
            "",
        ])
        self.assertIn("- Support: raised by the editor's check, by no reader; challenger: not run.", (self.root / "minor-only-minor.md").read_text(encoding="utf-8").split("\n"))
        empty = self.journal("empty", [])
        code, stdout, stderr = self.render(empty, self.review(empty), "empty.md")
        self.assertEqual((code, json.loads(stdout)["appendix"], json.loads(stdout)["counts"]), (0, None, {"priority_list": 0, "findings": 0, "minor": 0}))
        self.assertFalse((self.root / "empty-minor.md").exists(), "no appendix is written without a minor item")
        self.assertEqual((self.root / "empty.md").read_text(encoding="utf-8").split("\n")[7:],
                         ["Records: 5 of 6 blocks read; reader checkpoints: none; candidates: 0 accepted, 0 author questions, 0 merged, 0 rejected; "
                          "independent challenge: no candidates to challenge; fidelity audits attached: 0.",
                          "", "## Priority list", "No finding that misleads or hinders was accepted.", "", "## Findings", "None.", ""])

    def test_report_is_for_schema_7_journals(self):
        older = self.journal("schema6", [("accepted", "minor")], challenged=False, schema=6)
        review = self.review(older)
        self.assertEqual(sorted(set(review) & {"scope_note", "not_assessed", "priority_list"}), ["scope_note"], "before schema 7 the builder adds no frame; the fragment's scope note passes through")
        code, stdout, stderr = self.render(older, review, "older.md")
        self.assertEqual((code, json.loads(stderr)["error"]["message"]),
                         (2, "notes_build.py report renders the review of a schema 7 journal; this journal is schema 6, whose review has no scope note or priority list. "
                             "Start a new run to get a rendered report."))
        self.assertFalse((self.root / "older.md").exists())


if __name__ == "__main__":
    unittest.main()
