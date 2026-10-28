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


if __name__ == "__main__":
    unittest.main()
