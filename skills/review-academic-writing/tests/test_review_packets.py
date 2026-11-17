import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_packets.py"
SPEC = importlib.util.spec_from_file_location("review_packets", SCRIPT)
rp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rp)

TEXT = (
    "# Agreement study\n\n"
    "We compare the MSW across runs. The MSW is short.\n\n"
    "| run | MSW |\n| --- | --- |\n| 1 | 2 |\n\n"
    "We define the MSW as the measurement separation window.\n"
)
PROFILE = {"audience": "A trained researcher new to this project"}


def anchor(block_id, quote):
    return {"block_id": block_id, "quote": quote}


class ReviewPacketsTests(unittest.TestCase):
    def setUp(self):
        self.index = rp.build_index(TEXT)

    def packet(self, **selector):
        return rp.build_packet(self.index, PROFILE, **selector)

    def observation(self, packet, issues=None):
        ids = {k: packet[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256")}
        if issues is None:
            issues = [self.obs_issue("R1", anchor("B0002", "The MSW is short."))]
        return {**ids, "understanding": "Runs are compared on an undefined MSW.", "issues": issues}

    def obs_issue(self, issue_id, where, **overrides):
        issue = {"id": issue_id, "category": "unavailable_prerequisite", "anchor": where,
                 "reader_question": "What is MSW?", "consequence": "The comparison is opaque.",
                 "severity": "major", "confidence": "high"}
        return {**issue, **overrides}

    def review(self, **overrides):
        issue = {"id": "W1", "category": "late_prerequisite", "anchor": anchor("B0002", "the MSW across runs"),
                 "diagnosis": "MSW is used before B0004 defines it.", "action": "Define MSW at first use.",
                 "scientific_fidelity": "Meaning unchanged.", "severity": "major", "confidence": "high",
                 "evidence": [anchor("B0004", "We define the MSW")]}
        issue.update(overrides)
        return {"snapshot_id": self.index["snapshot_id"], "summary": "One late definition.", "issues": [issue]}

    def test_index_blocks_kinds_and_ranges(self):
        blocks = self.index["blocks"]
        self.assertEqual([(b["block_id"], b["kind"]) for b in blocks],
                         [("B0001", "heading"), ("B0002", "text"), ("B0003", "table"), ("B0004", "text")])
        self.assertEqual({k: blocks[1][k] for k in ("start_offset", "end_offset", "start_line", "end_line")},
                         {"start_offset": 19, "end_offset": 68, "start_line": 3, "end_line": 3})
        self.assertEqual({k: blocks[2][k] for k in ("start_offset", "end_offset", "start_line", "end_line")},
                         {"start_offset": 70, "end_offset": 107, "start_line": 5, "end_line": 7})
        self.assertEqual(blocks[2]["text"], "| run | MSW |\n| --- | --- |\n| 1 | 2 |")
        self.assertEqual(self.index["snapshot_id"], "sha256:" + hashlib.sha256(TEXT.encode("utf-8")).hexdigest())
        crlf = rp.build_index(TEXT.replace("\n", "\r\n"))
        self.assertEqual(crlf["snapshot_id"], self.index["snapshot_id"])
        self.assertEqual(crlf["text"], TEXT)

    def test_stop_after_text_packet_is_prefix_only(self):
        packet = self.packet(stop_after_text="We compare the MSW")
        self.assertEqual(packet["visible_text"], "# Agreement study\n\nWe compare the MSW")
        self.assertEqual(packet["stop_offset"], 37)
        self.assertNotIn("across runs", json.dumps(packet))
        self.assertEqual([(b["block_id"], b["end_offset"], b["text"]) for b in packet["blocks"]],
                         [("B0001", 17, "# Agreement study"), ("B0002", 37, "We compare the MSW")])
        self.assertEqual(packet["prefix_sha256"], hashlib.sha256(packet["visible_text"].encode()).hexdigest())

    def test_packet_selectors(self):
        with self.assertRaisesRegex(rp.ReviewDataError, "ambiguous; supply a one-based occurrence"):
            self.packet(stop_after_text="the MSW")
        second = self.packet(stop_after_text="the MSW", occurrence=2)
        self.assertEqual(second["visible_text"], TEXT[:126])
        self.assertTrue(second["visible_text"].endswith("We define the MSW"))
        with self.assertRaisesRegex(rp.ReviewDataError, "outside the available exact matches"):
            self.packet(stop_after_text="the MSW", occurrence=3)
        through = self.packet(through_block="B0003")
        self.assertEqual(through["stop_offset"], 107)
        self.assertEqual([b["block_id"] for b in through["blocks"]], ["B0001", "B0002", "B0003"])
        self.assertEqual(self.packet(stop_offset=5)["visible_text"], "# Agr")
        with self.assertRaisesRegex(rp.ReviewDataError, "Choose exactly one of"):
            self.packet(through_block="B0002", stop_offset=5)
        with self.assertRaisesRegex(rp.ReviewDataError, "Unknown through_block: B0009"):
            self.packet(through_block="B0009")

    def test_valid_observation_counts_issues(self):
        packet = self.packet(through_block="B0002")
        issues = [self.obs_issue("R1", anchor("B0002", "The MSW is short.")),
                  self.obs_issue("R2", anchor("B0001", "Agreement"), category="scope_ambiguity", severity="minor")]
        result = rp.check_observation(packet, self.observation(packet, issues))
        self.assertEqual((result["ok"], result["issue_count"], result["checkpoint_id"]),
                         (True, 2, packet["checkpoint_id"]))

    def test_observation_rejections(self):
        packet = self.packet(stop_after_text="We compare the MSW")
        cases = [
            ([self.obs_issue("R1", anchor("B0002", "across runs"))], "issues\\[0\\].anchor.quote is not an exact substring"),
            ([self.obs_issue("R1", anchor("B0004", "We define"))], "issues\\[0\\].anchor.block_id is not present"),
            ([self.obs_issue("R1", anchor("B0002", "MSW"), category="late_prerequisite")], "issues\\[0\\].category must be one of"),
            ([self.obs_issue("R1", anchor("B0002", "MSW"), severity="blocker")], "issues\\[0\\].severity must be one of"),
            ([self.obs_issue("R1", anchor("B0002", "MSW"), confidence="certain")], "issues\\[0\\].confidence must be one of"),
            ([self.obs_issue("R1", anchor("B0002", "MSW")), self.obs_issue("R1", anchor("B0002", "compare"))], "Duplicate issue id: R1"),
        ]
        for issues, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(rp.ReviewDataError, message):
                rp.check_observation(packet, self.observation(packet, issues))
        stale = self.observation(packet)
        stale["packet_sha256"] = self.packet(stop_after_text="We compare the MSW across")["packet_sha256"]
        with self.assertRaisesRegex(rp.ReviewDataError, "Observation packet_sha256 does not match"):
            rp.check_observation(packet, stale)

    def test_observation_cannot_place_entries(self):
        packet = self.packet(through_block="B0002")
        observation = self.observation(packet)
        observation["cannot_place"] = [{"anchor": anchor("B0002", "the MSW"), "note": "No earlier mention of MSW."}]
        result = rp.check_observation(packet, observation)
        self.assertEqual((result["ok"], result["issue_count"], result["cannot_place_count"]), (True, 1, 1))
        self.assertIn("cannot_place entries quote visible text", result["scope"]["verified"])
        without = rp.check_observation(packet, self.observation(packet))
        self.assertEqual((without["issue_count"], without["cannot_place_count"]), (1, 0))
        self.assertEqual(rp.check_observation(packet, {**self.observation(packet), "cannot_place": []})["cannot_place_count"], 0)
        cases = [
            ([{"anchor": anchor("B0002", "the measurement window"), "note": "n"}], "cannot_place\\[0\\].anchor.quote is not an exact substring"),
            ([{"anchor": anchor("B0004", "We define"), "note": "n"}], "cannot_place\\[0\\].anchor.block_id is not present"),
            ([{"anchor": anchor("B0002", "the MSW"), "note": ""}], "cannot_place\\[0\\].note must be a nonempty string"),
            ([{"anchor": anchor("B0002", "the MSW"), "note": "n", "severity": "major"}], "cannot_place\\[0\\] has unsupported fields: severity"),
            ([{"anchor": anchor("B0002", "the MSW")}], "cannot_place\\[0\\] is missing fields: note"),
            ({"anchor": anchor("B0002", "the MSW"), "note": "n"}, "cannot_place must be an array"),
        ]
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(rp.ReviewDataError, message):
                rp.check_observation(packet, {**self.observation(packet), "cannot_place": value})

    def test_review_checks(self):
        result = rp.check_review(self.index, self.review())
        self.assertEqual((result["ok"], result["issue_count"]), (True, 1))
        wrong = self.review()
        wrong["snapshot_id"] = rp.build_index(TEXT + "More.\n")["snapshot_id"]
        with self.assertRaisesRegex(rp.ReviewDataError, "Review snapshot_id does not match"):
            rp.check_review(self.index, wrong)
        with self.assertRaisesRegex(rp.ReviewDataError, "issues\\[0\\].category must be one of"):
            rp.check_review(self.index, self.review(category="scope_ambiguity"))
        with self.assertRaisesRegex(rp.ReviewDataError, "issues\\[0\\].evidence\\[0\\].quote is not an exact substring"):
            rp.check_review(self.index, self.review(evidence=[anchor("B0004", "We define MSW")]))

    def test_review_concept_ledger_anchors(self):
        review = self.review()
        review["concept_ledger"] = [{"concept_id": "K1", "first_mention": anchor("B0002", "MSW"),
                                     "earliest_usable_explanation": anchor("B0004", "measurement separation window"),
                                     "first_critical_use": None}]
        self.assertEqual(rp.check_review(self.index, review)["issue_count"], 1)
        bad = copy.deepcopy(review)
        bad["concept_ledger"][0]["earliest_usable_explanation"] = anchor("B0003", "separation")
        with self.assertRaisesRegex(rp.ReviewDataError, "concept_ledger\\[0\\].earliest_usable_explanation.quote is not an exact substring"):
            rp.check_review(self.index, bad)
        bad["concept_ledger"] = [{"concept_id": "K1"}, {"concept_id": "K1"}]
        with self.assertRaisesRegex(rp.ReviewDataError, "Duplicate concept_id: K1"):
            rp.check_review(self.index, bad)

    def test_find_hedges_lists_each_phrase_once_in_order(self):
        cases = (
            ("Counts may rise under these conditions.", ["may", "under these conditions"]),
            ("Risk is associated with age in 3 of 5 cohorts, if the model holds.", ["associated with", "in 3 of 5", "if"]),
            ("It may rise and it may fall.", ["may"]),
            ("May counts rise? They may.", ["May"]),
            ("The mayor said nothing.", []),
            ("Sniff the gift.", []),
            ("Counts rose.", []),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(rp.find_hedges(text), expected)

    def test_find_hedges_extra_patterns_and_keys(self):
        self.assertEqual(rp.find_hedges("We believe counts rose."), [])
        self.assertEqual(rp.find_hedges("We believe counts rose.", ["\\bwe believe\\b"]), ["We believe"])
        self.assertEqual(rp.find_hedges("We believe counts may rise.", ("\\bwe believe\\b",)), ["We believe", "may"])
        self.assertEqual(rp.hedge_key("  Under  These Conditions "), "under these conditions")
        self.assertEqual(rp.hedge_key("May"), rp.hedge_key("may"))
        self.assertIn(r"\bmay\b", rp.HEDGE_PATTERNS)

    def test_hedge_survives_through_a_listed_hedge_or_the_kept_phrase_itself(self):
        self.assertTrue(rp.hedge_survives(["may"], "Counts might rise."))
        self.assertTrue(rp.hedge_survives(["may"], "Counts may rise."))
        self.assertFalse(rp.hedge_survives(["may"], "Counts rise."))
        self.assertTrue(rp.hedge_survives(["We believe"], "We believe counts increased."), "the kept phrase itself, though not a built-in hedge")
        self.assertTrue(rp.hedge_survives(["we believe"], "WE  BELIEVE counts increased."), "whitespace and case are normalized")
        self.assertFalse(rp.hedge_survives(["We believe"], "We think counts increased."))
        self.assertTrue(rp.hedge_survives(["We believe"], "We think counts increased.", ["\\bwe think\\b"]), "an extra pattern counts as a listed hedge")
        self.assertFalse(rp.hedge_survives([], "Counts rise."))

    def test_write_json_atomic_refuses_existing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            path.write_bytes(b"original\n")
            with self.assertRaises(rp.ReviewDataError) as caught:
                rp.write_json_atomic(path, {"a": 1})
            self.assertEqual(caught.exception.code, "output_exists")
            self.assertEqual(path.read_bytes(), b"original\n")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["out.json"])
            fresh = Path(tmp) / "new.json"
            rp.write_json_atomic(fresh, {"a": 1})
            self.assertEqual(fresh.read_text(encoding="utf-8"), '{\n  "a": 1\n}\n')

    def run_cli(self, cwd, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                              cwd=cwd, capture_output=True, text=True)

    def test_cli_index_packet_check_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "paper.md").write_text(TEXT, encoding="utf-8")
            (root / "profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
            done = self.run_cli(root, "index", "--input", root / "paper.md", "--out", root / "index.json")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout), {"ok": True, "command": "index", "snapshot_id": self.index["snapshot_id"],
                                                       "output": str(root / "index.json")})
            done = self.run_cli(root, "packet", "--index", root / "index.json", "--profile", root / "profile.json",
                                "--through-block", "B0002", "--out", root / "packet.json")
            self.assertEqual(done.returncode, 0, done.stderr)
            packet = json.loads((root / "packet.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["visible_text"], TEXT[:68])
            self.assertEqual(json.loads(done.stdout), {"ok": True, "command": "packet", "checkpoint_id": packet["checkpoint_id"],
                                                       "prefix_sha256": hashlib.sha256(TEXT[:68].encode()).hexdigest(),
                                                       "output": str(root / "packet.json")})
            (root / "obs.json").write_text(json.dumps(self.observation(packet)), encoding="utf-8")
            done = self.run_cli(root, "check-observation", "--packet", root / "packet.json", "--observation", root / "obs.json")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual({k: json.loads(done.stdout)[k] for k in ("ok", "command", "issue_count")},
                             {"ok": True, "command": "check-observation", "issue_count": 1})
            done = self.run_cli(root, "index", "--input", root / "paper.md", "--out", root / "index.json")
            self.assertEqual((done.returncode, done.stdout), (2, ""))
            self.assertEqual(json.loads(done.stderr), {"ok": False, "error": {
                "code": "output_exists", "message": "Output path already exists; choose a new path."}})

    def test_other_script_strings_names_each_string_and_skips_copied_text(self):
        value = {"summary": "One finding.", "decisions": [
            {"diagnosis": "fine"},
            {"diagnosis": "The term 定义 comes late and the sentence goes on for a good many more words.", "countercheck": ["checked", "проверено"],
             "evidence": [{"block_id": "B0002", "quote": "引文"}], "proposed_wording": "改写", "kept_as": "可能", "in_context": "[[可能]]", "term": "术语",
             "audit_path": "/home/读者/audit.json", "events_dir": "/home/读者/events"}]}
        self.assertEqual(rp.other_script_strings(value), [("decisions[1].diagnosis", "定义 comes late and the sentence goes on f"), ("decisions[1].countercheck[1]", "проверено")],
                         "the snippet is at most 40 characters from the first character of the other script")
        self.assertEqual(rp.other_script_strings({"notes": "α and β are angles; the café is naïve — “quoted”."}), [], "Greek letters and accented Latin are not another script here")
        for sample in ("кириллица", "עברית", "عربى", "हिन्दी", "ไทย", "かな", "漢字", "한글", "𠀋"):
            with self.subTest(sample=sample):
                self.assertEqual(rp.other_script_strings([sample]), [("[0]", sample)])

    def test_english_check_on_observations_and_reviews(self):
        packet = self.packet(through_block="B0002")
        observation = self.observation(packet)
        observation["understanding"] = "MSW 没有定义。"
        self.assertEqual(rp.check_observation(packet, observation)["issue_count"], 1, "the library call checks the language only when asked")
        with self.assertRaisesRegex(rp.ReviewDataError, "^observation: records are written in English; copy quotations and proposed wording exactly as they are in the manuscript\\. "
                                    "Text in another script at: understanding \\('没有定义。'\\)\\.$"):
            rp.check_observation(packet, observation, english=True)
        many = self.review()
        many["issues"] = [{**many["issues"][0], "id": f"W{n}", "diagnosis": "太晚"} for n in range(7)]
        with self.assertRaisesRegex(rp.ReviewDataError, "issues\\[4\\]\\.diagnosis \\('太晚'\\); and 2 more\\.$"):
            rp.check_review(self.index, many, english=True)
        # A manuscript that itself holds text in another script may be quoted anywhere, so its records are not checked.
        mixed = rp.build_index(TEXT + "\nThe window (测量间隔) is short.\n")
        review = {"snapshot_id": mixed["snapshot_id"], "summary": "The gloss 测量间隔 is used once.", "issues": []}
        self.assertEqual(rp.check_review(mixed, review, english=True)["issue_count"], 0)

    def test_cli_check_review_refuses_a_summary_in_another_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.json").write_text(json.dumps(self.index), encoding="utf-8")
            (root / "review.json").write_text(json.dumps(self.review()), encoding="utf-8")
            done = self.run_cli(root, "check-review", "--index", root / "index.json", "--review", root / "review.json")
            self.assertEqual((done.returncode, json.loads(done.stdout)["issue_count"]), (0, 1), done.stderr)
            (root / "chinese.json").write_text(json.dumps({**self.review(), "summary": "一处定义出现得太晚。"}), encoding="utf-8")
            done = self.run_cli(root, "check-review", "--index", root / "index.json", "--review", root / "chinese.json")
            self.assertEqual((done.returncode, done.stdout), (2, ""))
            self.assertEqual(json.loads(done.stderr), {"ok": False, "error": {"code": "validation_error", "message":
                "review: records are written in English; copy quotations and proposed wording exactly as they are in the manuscript. "
                "Text in another script at: summary ('一处定义出现得太晚。')."}})



class HedgeReportTests(unittest.TestCase):
    """review_packets.check_hedge_report on the cases of the October 2026 diagnosis (each audit reports every hedge kept)."""

    def check(self, original, wording, kept_as, **checks):
        found = rp.find_hedge_occurrences([original])
        report = [{"id": o["id"], "term": o["term"], "status": "kept", "kept_as": k} for o, k in zip(found, kept_as)]
        rp.check_hedge_report(found, report, checks, wording)

    def test_a_kept_report_the_wording_cannot_carry_is_refused(self):
        with self.assertRaisesRegex(rp.ReviewDataError, "reports H1, H2 kept, but the proposed wording does not hold a separate qualifying phrase"):
            self.check("X may reduce errors. Y may increase latency.", "X reduces errors. Y may increase latency.", ["may", "may"])
        with self.assertRaisesRegex(rp.ReviewDataError, "does not hold a separate qualifying phrase"):
            self.check("X may reduce errors under these conditions.", "X reduces errors under these conditions.", ["under these conditions", "under these conditions"])

    def test_paraphrase_and_overlapping_quotations(self):
        self.check("X may reduce errors.", "It is possible that X reduces errors.", ["It is possible that"])
        # Two kept hedges may quote different words that do not overlap; overlapping quotations are one place.
        self.check("X may reduce errors under these conditions.", "X might reduce errors under these conditions.", ["might", "under these conditions"])
        with self.assertRaisesRegex(rp.ReviewDataError, "does not hold a separate qualifying phrase"):
            self.check("X may possibly reduce errors.", "X might reduce errors.", ["might", "might reduce"])

    def test_a_hedge_moved_to_another_claim_is_left_to_the_auditor(self):
        # One "may" before and after, on a different claim: the texts cannot show the move, so the check passes.
        # The audit prompt asks the auditor to compare which claim each hedge qualifies.
        self.check("X may reduce errors. Y increases latency.", "X reduces errors. Y may increase latency.", ["may"])



class ChallengeTests(unittest.TestCase):
    """review_packets.check_challenges: a challenger's answers held to the text."""

    TEXT = "# Study\n\nWe report counts of agreement. Counts rose.\n\nWe define agreement as matching records.\n"

    def setUp(self):
        self.blocks = {b["block_id"]: b for b in rp.build_index(self.TEXT)["blocks"]}
        self.requests = [
            {"candidate_id": "C01", "anchor": {"block_id": "B0002", "quote": "counts of agreement"}, "question": "What is agreement?"},
            {"candidate_id": "C02", "anchor": {"block_id": "B0003", "quote": "matching records"}, "question": "Which records?"},
            {"candidate_id": "C03", "anchor": {"block_id": "B0002", "quote": "Counts rose."}, "question": "Counts of what?"},
        ]

    def answer(self, key, verdict, answer=None, duplicate_of=None, reason="r"):
        return {"candidate_id": key, "verdict": verdict, "answer": answer, "duplicate_of": duplicate_of, "reason": reason}

    def test_positions_are_worked_out_from_the_text(self):
        later = {"block_id": "B0003", "quote": "We define agreement as matching records."}
        earlier = {"block_id": "B0002", "quote": "We report counts of agreement."}
        joined = rp.check_challenges(self.requests, [self.answer("C01", "answered", later), self.answer("C02", "answered", later), self.answer("C03", "answered", earlier)], self.blocks)
        self.assertEqual([(e["candidate_id"], e["verdict"], e["answer_position"]) for e in joined], [("C01", "answered", "after"), ("C02", "answered", "at"), ("C03", "answered", "before")])
        self.assertEqual([rp.challenge_disputes(e) for e in joined], [False, True, True])
        self.assertEqual(joined[0]["question"], "What is agreement?")
        other = rp.check_challenges(self.requests, [self.answer("C03", "duplicate", duplicate_of="C01"), self.answer("C01", "stands"), self.answer("C02", "not_material")], self.blocks)
        self.assertEqual([(e["candidate_id"], e["verdict"], e["answer_position"], rp.challenge_disputes(e)) for e in other],
                         [("C01", "stands", None, False), ("C02", "not_material", None, True), ("C03", "duplicate", None, True)])

    def test_malformed_answers_are_refused(self):
        stands = [self.answer(k, "stands") for k in ("C01", "C02", "C03")]
        cases = (
            ({"C01": "stands"}, "challenges must be an array with one"),
            (stands[:2], "challenges does not answer every candidate; missing: C03\\."),
            ([*stands, self.answer("C01", "stands")], "challenges answers C01 twice; one entry per candidate\\."),
            ([*stands[:2], self.answer("C09", "stands")], "challenges\\[2\\] answers 'C09', which is not a candidate of this request\\."),
            ([*stands[:2], {**stands[2], "note": "x"}], "challenges\\[2\\] must be an object with exactly candidate_id, verdict, answer, duplicate_of, and reason\\."),
            ([*stands[:2], self.answer("C03", "adequate")], "challenges\\[2\\]\\.verdict must be one of: answered, duplicate, not_material, stands\\."),
            ([*stands[:2], self.answer("C03", "stands", reason=" ")], "challenges\\[2\\]\\.reason must be a nonempty string"),
            ([*stands[:2], self.answer("C03", "answered")], "challenges\\[2\\]\\.answer must be a JSON object"),
            ([*stands[:2], self.answer("C03", "answered", {"block_id": "B0003", "quote": "We define agreement elsewhere"})], "challenges\\[2\\]\\.answer\\.quote is not an exact substring of its supplied block\\."),
            ([*stands[:2], self.answer("C03", "stands", {"block_id": "B0002", "quote": "Counts"})], "challenges\\[2\\]\\.answer must be null unless the verdict is answered\\."),
            ([*stands[:2], self.answer("C03", "stands", duplicate_of="C01")], "challenges\\[2\\]\\.duplicate_of must be null unless the verdict is duplicate\\."),
            ([*stands[:2], self.answer("C03", "duplicate", duplicate_of="C03")], "Challenge of C03: duplicate_of must name another candidate"),
            ([stands[0], self.answer("C02", "duplicate", duplicate_of="C03"), self.answer("C03", "duplicate", duplicate_of="C02")], "duplicate_of must name another candidate of this request that is not itself reported as a duplicate\\."),
        )
        for answers, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(rp.ReviewDataError, message):
                    rp.check_challenges(self.requests, answers, self.blocks)

    def test_requests_and_saved_files_are_checked(self):
        for value, message in (([], "must be a nonempty array"), ([{**self.requests[0], "diagnosis": "x"}], "has unsupported fields: diagnosis"),
                               ([self.requests[0], self.requests[0]], "lists C01 twice"), ([{**self.requests[0], "anchor": {"block_id": "B0002", "quote": "nowhere"}}], "quote is not an exact substring")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(rp.ReviewDataError, message):
                    rp.challenge_requests(value, self.blocks)
        snapshot = rp.build_index(self.TEXT)["snapshot_id"]
        joined = rp.check_challenges(self.requests[:1], [self.answer("C01", "answered", {"block_id": "B0003", "quote": "We define agreement"})], self.blocks)
        saved = {"snapshot_id": snapshot, "challenges": joined, "challenger": {"mode": "bounded-request", "agent_id": "x-1"}}
        self.assertEqual(rp.check_challenge_file(saved, self.blocks, snapshot), joined)
        moved = {**saved, "challenges": [{**joined[0], "answer_position": "before"}]}
        for value, message in ((moved, "records an answer_position that the text does not give"), ({**saved, "snapshot_id": "sha256:" + "0" * 64}, "made for a different source snapshot"),
                               ({**saved, "challenger": {"mode": "full-context", "agent_id": "x"}}, "challenge\\.challenger\\.mode must be one of")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(rp.ReviewDataError, message):
                    rp.check_challenge_file(value, self.blocks, snapshot)


class SectionRuleTests(unittest.TestCase):
    TEXT = (
        "Preface line before any heading.\n\n"
        "# Study\n\n"
        "## Methods\n\n"
        "We counted visits.\n\n"
        "### Sensors ###\n\n"
        "Each feeder had one sensor.\n\n"
        "| feeder | sensor |\n| --- | --- |\n| 1 | beam |\n\n"
        "Results\n-------\n\n"
        "Counts rose. Counts rose.\n\n"
        "Closing\n=======\n"
    )

    def setUp(self):
        self.index = rp.build_index(self.TEXT)
        self.blocks = {b["block_id"]: b for b in self.index["blocks"]}

    def ends(self, **options):
        return [(row["block_id"], row["heading"]) for row in rp.section_ends(self.index, **options)]

    def test_heading_level_and_text(self):
        cases = (("# A", 1, "A"), ("###  Deep title ##", 3, "Deep title"), ("## C# notes", 2, "C# notes"), ("Title\n===", 1, "Title"), ("Sub\n---", 2, "Sub"))
        for text, level, words in cases:
            with self.subTest(text=text):
                self.assertEqual((rp.heading_level(text), rp.heading_text(text)), (level, words))
        for text in ("plain words", "#nospace", "a | b\n--- | ---\n1 | 2", "one\ntwo\nthree"):
            with self.subTest(text=text):
                self.assertIsNone(rp.heading_level(text))
        self.assertEqual([b["kind"] for b in self.index["blocks"]],
                         ["text", "heading", "heading", "text", "heading", "text", "table", "heading", "text", "heading"])

    def test_section_ends_at_every_heading(self):
        self.assertEqual(self.ends(), [("B0001", None), ("B0004", "Methods"), ("B0007", "Sensors"), ("B0009", "Results")])

    def test_a_heading_deeper_than_max_level_does_not_end_a_section(self):
        self.assertEqual(self.ends(max_level=2), [("B0001", None), ("B0007", "Methods"), ("B0009", "Results")])
        self.assertEqual(self.ends(max_level=1), [("B0001", None), ("B0009", "Study")])

    def test_only_included_blocks_end_a_section(self):
        self.assertEqual(self.ends(included=["B0004", "B0006", "B0009"]), [("B0004", "Methods"), ("B0006", "Sensors"), ("B0009", "Results")])
        self.assertEqual(self.ends(included=["B0006"], max_level=2), [("B0006", "Methods")])
        self.assertEqual(self.ends(included=[]), [])

    def test_closing_quote_is_the_shortest_closing_words_that_name_one_place(self):
        self.assertEqual(rp.closing_quote("one two three four five", 3), "three four five")
        self.assertEqual(rp.closing_quote("one two", 3), "one two")
        self.assertEqual(rp.closing_quote("the cat sat. the cat sat. the cat sat.", 3), "sat. the cat sat. the cat sat.")
        self.assertEqual(rp.closing_quote("a b a b", 2), "b a b")
        self.assertEqual(rp.closing_quote("x x x", 2), "x x x")
        long_block = "Visits were merged when the gap between two detections of one tag was shorter than five seconds in every feeder."
        self.assertEqual(rp.closing_quote(long_block), "detections of one tag was shorter than five seconds in every feeder.")

    def test_anchor_stop_offset_needs_a_quote_that_names_one_place(self):
        block = self.blocks["B0009"]
        self.assertEqual(rp.anchor_stop_offset(block, "rose. Counts rose."), block["end_offset"])
        self.assertEqual(rp.anchor_stop_offset(block, "Counts rose. C"), block["start_offset"] + len("Counts rose. C"))
        with self.assertRaisesRegex(rp.ReviewDataError, r"The anchor quote occurs 2 times in B0009, so it does not say where the reader stops; lengthen the quote so that it names one place\."):
            rp.anchor_stop_offset(block, "Counts rose.")
        with self.assertRaisesRegex(rp.ReviewDataError, r"The anchor quote is not exact text of B0009\."):
            rp.anchor_stop_offset(block, "Counts fell.")
        with self.assertRaisesRegex(rp.ReviewDataError, "anchor.quote must be a nonempty string"):
            rp.anchor_stop_offset(block, "")

    def test_a_packet_cut_at_a_closing_quote_ends_with_its_block(self):
        for row in rp.section_ends(self.index):
            block = self.blocks[row["block_id"]]
            stop = rp.anchor_stop_offset(block, rp.closing_quote(block["text"]))
            packet = rp.build_packet(self.index, PROFILE, stop_offset=stop)
            self.assertEqual((stop, packet["blocks"][-1]["text"]), (block["end_offset"], block["text"]))


if __name__ == "__main__":
    unittest.main()
