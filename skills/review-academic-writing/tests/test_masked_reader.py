import base64
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
SPEC = importlib.util.spec_from_file_location("masked_reader", Path(__file__).parents[1] / "scripts" / "masked_reader.py")
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


def note(nid, text, sources, since, kind="established"):
    return {"id": nid, "kind": kind, "text": text, "sources": sources, "since": since}


def memory(gist, notes=(), dropped=()):
    return {"gist": gist, "notes": list(notes), "dropped": list(dropped)}


N1 = note("N1", "The article reports counts.", ["B1"], 1)


class MaskedReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.case_path = self.root / "case.json"
        self.profile_path = self.root / "profile.json"
        self.asset = self.root / "figure.png"
        self.asset.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII="))
        self.case = {
            "id": "case-with-private-label",
            "asset_policy": "reference-jump",
            "blocks": [{"id": "B1", "text": "Opening material refers to Figure F1.", "page": 1, "kind": "text"}, {"id": "B2", "text": "Unseen body only. Figure F2 gives the final counts.", "page": 2, "kind": "text"}],
            "windows": [["B1"], ["B2"]],
            "assets": [{"id": "F1", "path": "figure.png", "caption": "Observed counts.", "referenced_by": ["B1"]}, {"id": "F2", "path": "figure.png", "caption": "Unseen figure caption.", "referenced_by": ["B2"]}],
        }
        self.case_path.write_bytes(reader.encoded(self.case))
        self.counter = 0
        self.fresh_run({"task": "Read in order and describe your current understanding.", "memory_limit": 180})

    def tearDown(self):
        self.tmp.cleanup()

    def fresh_run(self, profile, name="run"):
        self.profile_path.write_bytes(reader.encoded(profile))
        self.run = self.root / name
        return reader.start(self.case_path, self.profile_path, self.run)

    def response(self, kind="advance", target=None, carry=None):
        return {"understanding": "A private full response that must not propagate.", "understood": [], "uncertain": [], "resolved": [], "next_action": {"kind": kind, "target": target, "reason": "Continue the reading task."}, "carry_memory": carry or memory("The study describes observed counts."), "source_recognition": {"recognized": False, "description": ""}}

    def issue(self, lookup=None):
        self.counter += 1
        path = self.root / f"packet-{self.counter}.json"
        reader.packet(self.run, path, lookup)
        return path

    def write(self, value, stem="response"):
        self.counter += 1
        path = self.root / f"{stem}-{self.counter}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n\n")
        return path

    def submit(self, packet, value=None):
        path = self.write(value or self.response())
        return reader.record(self.run, packet, path, "fixture-agent"), path

    def test_window_boundary_and_memory_only_propagation(self):
        p1 = self.issue()
        first = json.loads(p1.read_text())
        self.assertEqual(([m["id"] for m in first["materials"]], first["sequence"], first["carry_memory"], first["asset_policy"]), (["B1"], 1, {"gist": "", "notes": []}, "reference-jump"))
        self.assertNotIn("Unseen body only", p1.read_text())
        self.assertNotIn("Unseen figure caption", p1.read_text())
        self.assertNotIn("case-with-private-label", p1.read_text())
        _, raw_path = self.submit(p1, self.response(carry=memory("Counts.", [N1])))
        self.assertEqual((self.run / "records/000001/response.json").read_bytes(), raw_path.read_bytes())
        p2 = self.issue()
        second = json.loads(p2.read_text())
        self.assertEqual([m["id"] for m in second["materials"]], ["B2"])
        self.assertEqual((second["sequence"], second["carry_memory"]), (2, {"gist": "Counts.", "notes": [N1]}))
        self.assertNotIn("Opening material", p2.read_text())
        self.assertNotIn("A private full response", p2.read_text())
        result, _ = self.submit(p2, self.response(carry=memory("Counts.", [N1])))
        self.assertEqual((result["complete"], result["notes_added"]), (True, []))
        with self.assertRaisesRegex(reader.Invalid, "Run is complete"):
            self.issue()

    def test_records_cannot_skip_or_replay_packets(self):
        response = self.root / "response.json"
        response.write_bytes(reader.encoded(self.response()))
        with self.assertRaisesRegex(reader.Invalid, "No packet"):
            reader.record(self.run, self.root / "never-issued.json", response)
        p1 = self.issue()
        self.submit(p1)
        p2 = self.issue()
        with self.assertRaisesRegex(reader.Invalid, "out of order"):
            reader.record(self.run, p1, response)
        self.submit(p2)
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001", "000002"])

    def test_future_lookup_blocked_and_allowed_lookup_returns_to_window(self):
        p1 = self.issue()
        for target in ("B2", "F2"):
            with self.assertRaisesRegex(reader.Invalid, "unseen|not been referenced"):
                self.submit(p1, self.response("lookup", target))
        with self.assertRaisesRegex(reader.Invalid, "--lookup must match"):
            self.issue("F1")
        self.submit(p1, self.response("lookup", "F1"))
        with self.assertRaisesRegex(reader.Invalid, "--lookup must match"):
            self.issue("F2")
        lookup = self.issue("F1")
        data = json.loads(lookup.read_text())
        self.assertEqual((data["mode"], [m["id"] for m in data["materials"]]), ("lookup", ["F1"]))
        self.assertNotIn("Unseen figure caption", lookup.read_text())
        self.assertEqual(Path(data["materials"][0]["path"]).read_bytes(), self.asset.read_bytes())
        with self.assertRaisesRegex(reader.Invalid, "One lookup"):
            self.submit(lookup, self.response("lookup", "B1"))
        self.submit(lookup)
        returned = json.loads(self.issue().read_text())
        self.assertEqual([m["id"] for m in returned["materials"]], ["B1"])
        self.assertIn("new context", returned["instructions"])

    def test_memory_budget_in_words_with_legacy_alias(self):
        self.assertEqual(self.fresh_run({"task": "Read.", "memory_word_limit": 180}, "alias-run"), {"ok": True, "run": str(self.run.resolve()), "memory_limit": 180, "memory_unit": "words"})
        p = self.issue()
        notes = [note(f"N{i}", text, ["B1"], 1) for i, text in enumerate(("one", "two", "three?", "four"), 1)]
        carry = memory("word " * 176, notes)
        self.assertEqual(reader.memory_size(carry, {"memory_word_limit": 180}), 180)
        excessive = copy.deepcopy(carry)
        excessive["notes"][3]["text"] = "four extra"
        with self.assertRaisesRegex(reader.Invalid, "carry_memory has 181 words; limit is 180"):
            self.submit(p, self.response(carry=excessive))
        result, _ = self.submit(p, self.response(carry=carry))
        self.assertEqual((result["memory_size"], result["notes_added"]), (180, ["N1", "N2", "N3", "N4"]))

    def test_memory_budget_in_non_whitespace_characters(self):
        profile = {"task": "Read.", "memory_limit": 10, "memory_unit": "characters"}
        self.assertEqual(reader.memory_size({"gist": " a b\tc\n", "notes": [{"text": "dé  f"}]}, profile), 6)
        self.fresh_run(profile, "char-run")
        p = self.issue()
        with self.assertRaisesRegex(reader.Invalid, "carry_memory has 11 characters; limit is 10"):
            self.submit(p, self.response(carry=memory("ab cd", [note("N1", "ef gh ijk", ["B1"], 1)])))
        result, _ = self.submit(p, self.response(carry=memory("ab cd", [note("N1", "ef gh ij", ["B1"], 1)])))
        self.assertEqual(result["memory_size"], 10)

    def test_note_lineage_refusals(self):
        self.submit(self.issue(), self.response(carry=memory("Counts.", [N1])))
        p2 = self.issue()
        new = note("N2", "Figure F2 is named.", ["B2"], 2)
        cases = [
            (memory("x"), "Carried notes must be kept or explicitly dropped: N1"),
            (memory("x", [N1, {**new, "since": 1}]), "New note N2 must have since=2"),
            (memory("x", [{**N1, "since": 2}]), "Carried note N1 must keep since=1"),
            (memory("x", [N1, {**new, "sources": ["F1"]}]), "Note N2 cites a source the reader has not seen"),
            (memory("x", [N1], [{"id": "N1", "reason": "done"}]), "dropped must name a carried note that is not kept, once each"),
            (memory("x", [N1], [{"id": "N7", "reason": "done"}]), "dropped must name a carried note that is not kept, once each"),
            (memory("x", [{**N1, "kind": "certain"}]), "Note N1 kind must be one of established, tentative, open_question, qualification"),
            (memory("x", [N1, {**new, "id": "N1"}]), "Note ids must be nonempty and unique"),
            ({"gist": "x", "established": [], "open_questions": []}, "carry_memory requires exactly gist, notes, dropped"),
        ]
        for carry, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(reader.Invalid, message):
                self.submit(p2, self.response(carry=carry))
        self.assertEqual(sorted(p.name for p in (self.run / "records").iterdir()), ["000001"])
        result, _ = self.submit(p2, self.response(carry=memory("x", [N1, new])))
        self.assertEqual((result["notes_added"], result["notes_revised"]), (["N2"], []))

    def test_source_profile_snapshot_and_asset_changes_stop_run(self):
        for path in (self.case_path, self.profile_path, self.run / "case.snapshot.json", self.asset):
            with self.subTest(path=str(path)):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.assertRaisesRegex(reader.Invalid, "changed"):
                    self.issue()
                path.write_bytes(original)
        self.issue()

    def test_no_overwrite_and_saved_record_tamper_detection(self):
        with self.assertRaisesRegex(reader.Invalid, "already exists"):
            reader.start(self.case_path, self.profile_path, self.run)
        p = self.issue()
        packet_bytes = p.read_bytes()
        with self.assertRaisesRegex(reader.Invalid, "overwrite"):
            reader.packet(self.run, p)
        self.assertEqual(p.read_bytes(), packet_bytes)
        self.submit(p, self.response(carry=memory("Counts.", [N1])))
        saved = self.run / "records/000001/response.json"
        original = saved.read_bytes()
        with self.assertRaisesRegex(reader.Invalid, "No packet has been issued"):
            self.submit(p)
        self.assertEqual(saved.read_bytes(), original)
        receipt_path = self.run / "records/000001/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt_path.write_bytes(reader.encoded({**receipt, "notes_added": []}))
        with self.assertRaisesRegex(reader.Invalid, "Receipt memory record changed"):
            self.issue()
        receipt_path.write_bytes(reader.encoded(receipt))
        saved.write_bytes(original + b" ")
        with self.assertRaisesRegex(reader.Invalid, "Recorded input or response changed"):
            self.issue()

    def test_real_cli_reports_note_changes_across_three_events(self):
        script = str(Path(__file__).parents[1] / "scripts" / "masked_reader.py")
        cli_run = self.root / "cli-run"

        def call(*args):
            completed = subprocess.run([sys.executable, "-B", script, *map(str, args)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)
        call("start", "--case", self.case_path, "--profile", self.profile_path, "--out", cli_run)
        n2 = note("N2", "What do the counts show?", ["B1"], 1, "open_question")
        n3 = note("N3", "F1 plots the counts.", ["F1"], 2, "tentative")
        revised = {**N1, "text": "The article reports observed counts."}
        plans = [
            (self.response("lookup", "F1", memory("Counts are described.", [N1, n2])), {"memory_size": 12, "notes_added": ["N1", "N2"], "notes_dropped": [], "notes_revised": [], "complete": False, "next_mode": "lookup"}),
            (self.response(carry=memory("Counts are described.", [revised, n2, n3])), {"memory_size": 17, "notes_added": ["N3"], "notes_dropped": [], "notes_revised": ["N1"], "complete": False, "next_mode": "window"}),
            (self.response(carry=memory("Counts are described.", [revised, n3], [{"id": "N2", "reason": "F1 answers it."}])), {"memory_size": 12, "notes_added": [], "notes_dropped": ["N2"], "notes_revised": [], "complete": False, "next_mode": "window"}),
        ]
        for seq, (value, expected) in enumerate(plans, 1):
            packet = self.root / f"cli-packet-{seq}.json"
            call("packet", "--run", cli_run, "--out", packet)
            result = call("record", "--run", cli_run, "--packet", packet, "--response", self.write(value), "--agent-id", f"fixed-fixture-{seq}")
            self.assertEqual(result, {"ok": True, "sequence": seq, **expected})
            receipt = json.loads((cli_run / "records" / f"{seq:06d}" / "receipt.json").read_text())
            self.assertEqual({k: receipt[k] for k in ("memory_size", "notes_added", "notes_dropped", "notes_revised")}, {k: expected[k] for k in ("memory_size", "notes_added", "notes_dropped", "notes_revised")})
        question = self.write({"id": "Q1", "question": "What does F1 show?", "selected_with_full_text": False, "reason": "lookup"}, "question")
        issued = call("branch", "--run", cli_run, "--from-sequence", 2, "--question", question, "--out", self.root / "cli-branch.json")
        self.assertEqual((issued["branch_sequence"], issued["branched_from_sequence"]), (1, 2))
        answer = self.write({"answer": "Counts.", "support": "background", "anchors": [], "inference": "", "background_used": "figure reading", "undetermined": "", "source_recognition": {"recognized": False, "description": ""}})
        self.assertEqual(call("branch-record", "--run", cli_run, "--packet", self.root / "cli-branch.json", "--response", answer), {"ok": True, "branch_sequence": 1, "branched_from_sequence": 2, "support": "background"})

    def test_last_supplied_window_is_explicit_without_future_content(self):
        p1 = self.issue()
        self.assertFalse(json.loads(p1.read_text())["end_of_supplied_text"])
        self.submit(p1)
        last = json.loads(self.issue().read_text())
        self.assertTrue(last["end_of_supplied_text"])
        self.assertIn("last window of the supplied text", last["instructions"])
        self.assertIn("an unanswered question is not automatically a defect", last["instructions"])

    def test_new_run_format_and_rejection_of_earlier_manifests(self):
        manifest_path = self.run / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["format_version"], 5)
        manifest["format_version"] = 4
        manifest_path.write_bytes(reader.encoded(manifest))
        with self.assertRaisesRegex(reader.Invalid, "original controller for earlier run formats; this controller creates and reads version-5 runs"):
            self.issue()
        self.assertEqual(list((self.run / "packets").iterdir()), [])

    def test_case_without_a_known_asset_policy_is_refused(self):
        missing = {k: v for k, v in self.case.items() if k != "asset_policy"}
        for case in (missing, {**self.case, "asset_policy": "jump"}):
            with self.subTest(policy=case.get("asset_policy")):
                self.case_path.write_bytes(reader.encoded(case))
                with self.assertRaisesRegex(reader.Invalid, "^Case requires asset_policy: strict-prefix"):
                    self.fresh_run({"task": "Read.", "memory_limit": 180}, "no-policy-run")
                self.assertFalse((self.root / "no-policy-run").exists())

    def test_strict_prefix_opens_a_referenced_asset_only_at_its_layout_position(self):
        case = copy.deepcopy(self.case)
        case["asset_policy"] = "strict-prefix"
        case["assets"][0]["encountered_by"] = ["B2"]
        self.case_path.write_bytes(reader.encoded(case))
        self.fresh_run({"task": "Read.", "memory_limit": 180}, "strict-run")
        p1 = self.issue()
        first = json.loads(p1.read_text())
        self.assertEqual((first["asset_policy"], first["available_lookup_ids"]), ("strict-prefix", ["B1"]))
        with self.assertRaisesRegex(reader.Invalid, "Lookup target is unseen or has not been referenced"):
            self.submit(p1, self.response("lookup", "F1"))
        self.assertEqual(list((self.run / "records").iterdir()), [])
        self.submit(p1)
        p2 = self.issue()
        self.assertEqual(json.loads(p2.read_text())["available_lookup_ids"], ["B1", "B2", "F1"])
        self.submit(p2, self.response("lookup", "F1"))
        lookup = json.loads(self.issue("F1").read_text())
        self.assertEqual(([m["id"] for m in lookup["materials"]], lookup["asset_policy"]), (["F1"], "strict-prefix"))

    def test_understood_basis_accepts_background_and_refuses_other_kinds(self):
        p1 = self.issue()

        def claimed(basis):
            value = self.response()
            value["understood"] = [{"claim": "Numbered figures follow journal convention.", "basis": basis, "anchor": {"source_id": "B1", "quote": "Figure F1"}}]
            return value
        with self.assertRaisesRegex(reader.Invalid, "basis must be one of explicit, inferred, background"):
            self.submit(p1, claimed("stated"))
        self.assertEqual(list((self.run / "records").iterdir()), [])
        result, _ = self.submit(p1, claimed("background"))
        self.assertEqual(result, {"ok": True, "sequence": 1, "memory_size": 5, "notes_added": [], "notes_dropped": [], "notes_revised": [], "complete": False, "next_mode": "window"})
        saved = json.loads((self.run / "records/000001/response.json").read_text())
        self.assertEqual(saved["understood"], [{"claim": "Numbered figures follow journal convention.", "basis": "background", "anchor": {"source_id": "B1", "quote": "Figure F1"}}])
        self.assertEqual([m["id"] for m in json.loads(self.issue().read_text())["materials"]], ["B2"])

    def test_layout_encounter_opens_asset_only_when_its_position_is_seen(self):
        case = copy.deepcopy(self.case)
        case["assets"][1]["referenced_by"] = []
        case["assets"][1]["encountered_by"] = ["B2"]
        invalid = copy.deepcopy(case)
        invalid["assets"][1]["encountered_by"] = ["B99"]
        with self.assertRaisesRegex(reader.Invalid, "encountered_by"):
            reader.validate_case(invalid)
        self.case_path.write_bytes(reader.encoded(case))
        self.fresh_run({"task": "Read.", "memory_limit": 180}, "layout-run")
        p1 = self.issue()
        self.assertEqual(json.loads(p1.read_text())["available_lookup_ids"], ["B1", "F1"])
        self.submit(p1)
        p2 = self.issue()
        self.assertEqual(json.loads(p2.read_text())["available_lookup_ids"], ["B1", "B2", "F1", "F2"])
        self.submit(p2, self.response("lookup", "F2"))
        value = json.loads((lookup := self.issue()).read_text())
        self.assertEqual((value["materials"][0]["id"], value["end_of_supplied_text"]), ("F2", False))
        self.submit(lookup)
        returned = json.loads(self.issue().read_text())
        self.assertEqual(([m["id"] for m in returned["materials"]], returned["end_of_supplied_text"]), (["B2"], True))

    def test_anchor_quote_must_be_exact_text_of_a_seen_block(self):
        p1 = self.issue()

        def anchored(anchor):
            value = self.response("lookup", "F1")
            value["understood"] = [{"claim": "The text names a figure.", "basis": "explicit", "anchor": anchor}]
            return value
        with self.assertRaisesRegex(reader.Invalid, "not exact text of B1"):
            self.submit(p1, anchored({"source_id": "B1", "quote": "refers to Figure F9"}))
        with self.assertRaisesRegex(reader.Invalid, "exposed source_id"):
            self.submit(p1, anchored({"source_id": "F1", "quote": ""}))
        self.assertEqual(list((self.run / "records").iterdir()), [])
        self.submit(p1, anchored({"source_id": "B1", "quote": "refers to Figure F1"}))
        lookup = self.issue("F1")
        value = self.response()
        value["uncertain"] = [{"question": "Which bar is the control?", "needed_now": True, "anchor": {"source_id": "F1", "quote": "a label read from the image"}}]
        self.submit(lookup, value)
        saved = json.loads((self.run / "records/000002/response.json").read_text())
        self.assertEqual(saved["uncertain"][0]["anchor"], {"source_id": "F1", "quote": "a label read from the image"})

    def branch_answer(self, **change):
        return {"answer": "F1 shows the counts B1 mentions.", "support": "inferred", "anchors": [{"source_id": "B1", "quote": "refers to Figure F1"}], "inference": "B1 points to F1.", "background_used": "", "undetermined": "", "source_recognition": {"recognized": False, "description": ""}, **change}

    def test_branch_shows_one_recorded_event_and_never_alters_the_main_path(self):
        self.submit(self.issue(), self.response("lookup", "F1", memory("Counts.", [N1])))
        self.submit(self.issue("F1"), self.response(carry=memory("Counts.", [N1])))
        pending = self.issue()
        pending_bytes = pending.read_bytes()
        question = {"id": "Q1", "question": "What does F1 show?", "selected_with_full_text": True, "reason": "B2 names the final counts."}
        qpath = self.write(question, "question")
        for origin in (0, 3):
            with self.assertRaisesRegex(reader.Invalid, "A branch must start from a recorded display event"):
                reader.branch(self.run, origin, qpath, self.root / f"branch-{origin}.json")
        out = self.root / "branch.json"
        self.assertEqual(reader.branch(self.run, 2, qpath, out)["branch_sequence"], 1)
        value = json.loads(out.read_text())
        origin = json.loads((self.run / "records/000002/packet.json").read_text())
        self.assertEqual((value["mode"], value["branch_sequence"], value["branched_from_sequence"], value["question"]), ("branch", 1, 2, question))
        self.assertEqual((value["materials"], value["carry_memory"]), (origin["materials"], {"gist": "Counts.", "notes": [N1]}))
        self.assertEqual([m["id"] for m in value["materials"]], ["F1"])
        self.assertNotIn("Unseen body only", out.read_text())
        self.assertNotIn("final counts", out.read_text().replace(question["reason"], ""))
        self.assertEqual((self.run / "branch-packets/000001.json").read_bytes(), out.read_bytes())
        refusals = [(self.branch_answer(support="stated", anchors=[]), "support=stated requires at least one anchor"),
                    (self.branch_answer(inference=""), "support=inferred requires inference"),
                    (self.branch_answer(anchors=[{"source_id": "B1", "quote": "the final counts"}]), "Anchor quote is not exact text of B1"),
                    (self.branch_answer(anchors=[{"source_id": "B2", "quote": ""}]), "Anchor requires exposed source_id")]
        for answer, message in refusals:
            with self.subTest(message=message), self.assertRaisesRegex(reader.Invalid, message):
                reader.branch_record(self.run, out, self.write(answer))
        self.assertFalse((self.run / "branches").exists())
        result = reader.branch_record(self.run, out, self.write(self.branch_answer()), "branch-agent")
        self.assertEqual(result, {"ok": True, "branch_sequence": 1, "branched_from_sequence": 2, "support": "inferred"})
        receipt = json.loads((self.run / "branches/000001/receipt.json").read_text())
        origin_receipt = hashlib.sha256((self.run / "records/000002/receipt.json").read_bytes()).hexdigest()
        self.assertEqual((receipt["event_receipt_sha256"], receipt["created_after_sequence"], receipt["agent_id"]), (origin_receipt, 2, "branch-agent"))
        again = self.issue()
        self.assertEqual(again.read_bytes(), pending_bytes)
        saved = self.run / "branches/000001/response.json"
        saved.write_bytes(saved.read_bytes() + b" ")
        with self.assertRaisesRegex(reader.Invalid, "Branch record changed"):
            reader.branch(self.run, 1, qpath, self.root / "branch-after-tamper.json")
        self.assertEqual(reader.load_run(self.run)[5], 3)
        result, _ = self.submit(pending, self.response(carry=memory("Counts.", [N1])))
        self.assertEqual((result["sequence"], result["complete"], result["next_mode"]), (3, False, "window"))

    # ------------------------------------------------------------ recall events

    def recall_question(self, qid="Q1", after="B1", **change):
        return {"id": qid, "after_block": after, "question": "What does the opening report?", "options": ["counts", "weights", reader.RECALL_IDK], "answer": "counts", **change}

    def recall_answer(self, choice="counts", qid="Q1", note_ids=(), **change):
        return {"answers": [{"question_id": qid, "choice": choice, "note_ids": list(note_ids)}], "source_recognition": {"recognized": False, "description": ""}, **change}

    def test_validate_recall_questions_refusals(self):
        good = [self.recall_question()]
        reader.validate_recall_questions(good)
        reader.validate_recall_questions(good, {"B1", "B2"})
        cases = [
            ({"id": "Q1"}, "recall_questions must be a nonempty list of questions"),
            ([], "recall_questions must be a nonempty list of questions"),
            ([{k: v for k, v in self.recall_question().items() if k != "answer"}], "recall_questions\\[0\\] requires exactly id, after_block, question, options, answer"),
            ([self.recall_question(), self.recall_question(after="B2")], "recall_questions\\[1\\].id must be a nonempty string, unique in the list"),
            ([self.recall_question(options=["counts", reader.RECALL_IDK, "weights", reader.RECALL_IDK])], "recall_questions\\[0\\].options must be at least two distinct nonempty strings"),
            ([self.recall_question(options=["counts", "weights"])], f"recall_questions\\[0\\].options must include exactly one {reader.RECALL_IDK!r} option"),
            ([self.recall_question(answer=reader.RECALL_IDK)], f"recall_questions\\[0\\].answer must be one of the options other than {reader.RECALL_IDK!r}"),
            ([self.recall_question(answer="volumes")], "recall_questions\\[0\\].answer must be one of the options"),
            ([self.recall_question(after="B9")], "recall_questions\\[0\\].after_block names a block that is not in the case: B9"),
        ]
        for questions, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(reader.Invalid, message):
                reader.validate_recall_questions(questions, {"B1", "B2"})
        with self.assertRaisesRegex(reader.Invalid, "plan M1.recall_questions\\[0\\].question must be a nonempty string"):
            reader.validate_recall_questions([self.recall_question(question="")], None, "plan M1.recall_questions")

    def test_recall_results_outcomes(self):
        questions = [self.recall_question(), self.recall_question("Q2", "B2", answer="weights")]
        response = {"answers": [{"question_id": "Q1", "choice": "counts", "note_ids": []}, {"question_id": "Q2", "choice": "counts", "note_ids": []}], "source_recognition": {"recognized": False, "description": ""}}
        self.assertEqual(reader.recall_results(response, questions), [{"question_id": "Q1", "choice": "counts", "outcome": "correct"}, {"question_id": "Q2", "choice": "counts", "outcome": "wrong"}])
        idk = {"answers": [{"question_id": "Q2", "choice": reader.RECALL_IDK, "note_ids": []}], "source_recognition": {"recognized": False, "description": ""}}
        self.assertEqual(reader.recall_results(idk, questions), [{"question_id": "Q2", "choice": reader.RECALL_IDK, "outcome": "idk"}])
        self.assertEqual(reader.RECALL_OUTCOMES, ("correct", "wrong", "idk"))

    def test_recall_cannot_be_issued_before_any_display_event(self):
        with self.assertRaisesRegex(reader.Invalid, "A recall must follow a recorded display event"):
            reader.recall(self.run, [self.recall_question()], self.root / "recall-0.json")
        self.assertFalse((self.run / "recall-packets").exists())
        self.assertFalse((self.run / "recall-questions").exists())
        self.assertFalse((self.root / "recall-0.json").exists())

    def test_recall_value_and_response_validation(self):
        self.submit(self.issue(), self.response(carry=memory("Counts.", [N1])))
        questions = [self.recall_question(), self.recall_question("Q2", "B1", answer="weights")]
        out = self.root / "recall-1.json"
        issued = reader.recall(self.run, questions, out)
        self.assertEqual((issued["recall_sequence"], issued["after_sequence"]), (1, 1))
        packet = json.loads(out.read_text())
        self.assertEqual((packet["mode"], packet["materials"], packet["recall_sequence"], packet["after_sequence"]), ("recall", [], 1, 1))
        self.assertEqual(packet["carry_memory"], {"gist": "Counts.", "notes": [N1]})
        self.assertEqual(packet["questions"], [{"id": q["id"], "question": q["question"], "options": q["options"]} for q in questions])
        self.assertNotIn('"answer"', out.read_text())
        self.assertTrue(all("answer" not in q for q in packet["questions"]))
        self.assertNotIn("Opening material", out.read_text())
        self.assertEqual((self.run / "recall-packets/000001.json").read_bytes(), out.read_bytes())
        self.assertEqual(json.loads((self.run / "recall-questions/000001.json").read_text()), questions)
        # A second recall with other questions cannot be issued while this one is pending.
        with self.assertRaisesRegex(reader.Invalid, "A different recall is pending"):
            reader.recall(self.run, [self.recall_question("Q3")], self.root / "recall-other.json")
        refusals = [
            (self.recall_answer(), "Every question needs an answer; missing: Q2"),
            ({**self.recall_answer(), "answers": self.recall_answer()["answers"] + [{"question_id": "Q9", "choice": "counts", "note_ids": []}]}, "answers\\[1\\].question_id must name one packet question, once"),
            ({**self.recall_answer(), "answers": self.recall_answer()["answers"] + [{"question_id": "Q2", "choice": "weights", "note_ids": ["N7"]}]}, "answers\\[1\\].note_ids must name carried notes"),
            ({**self.recall_answer(), "answers": self.recall_answer()["answers"] + [{"question_id": "Q2", "choice": "volumes", "note_ids": []}]}, "answers\\[1\\].choice must be one of the question's options, copied verbatim"),
            ({**self.recall_answer(), "answers": self.recall_answer()["answers"] + [{"question_id": "Q2", "choice": "weights"}]}, "answers\\[1\\] requires exactly question_id, choice, note_ids"),
            ({"answers": []}, "Recall response requires exactly answers, source_recognition"),
        ]
        for response, message in refusals:
            with self.subTest(message=message), self.assertRaisesRegex(reader.Invalid, message):
                reader.recall_record(self.run, out, self.write(response, "recall-response"))
        self.assertFalse((self.run / "recalls").exists())
        full = {**self.recall_answer(note_ids=["N1"]), "answers": self.recall_answer(note_ids=["N1"])["answers"] + [{"question_id": "Q2", "choice": reader.RECALL_IDK, "note_ids": []}]}
        result = reader.recall_record(self.run, out, self.write(full, "recall-response"), "recall-agent")
        self.assertEqual(result, {"ok": True, "recall_sequence": 1, "after_sequence": 1, "results": [{"question_id": "Q1", "choice": "counts", "outcome": "correct"}, {"question_id": "Q2", "choice": reader.RECALL_IDK, "outcome": "idk"}], "wrong": [], "idk": ["Q2"]})
        receipt = json.loads((self.run / "recalls/000001/receipt.json").read_text())
        self.assertEqual((receipt["agent_id"], receipt["event_receipt_sha256"]), ("recall-agent", hashlib.sha256((self.run / "records/000001/receipt.json").read_bytes()).hexdigest()))
        self.assertEqual(json.loads((self.run / "recalls/000001/questions.json").read_text()), questions)
        run, manifest, _case, profile, state, _seq, _ = reader.load_run(self.run)
        recalls = reader.load_recalls(run, manifest, profile, state)
        self.assertEqual([(r["sequence"], r["after_sequence"]) for r in recalls], [(1, 1)])
        # The main path is untouched: the next window packet carries the same memory as before the recall.
        self.assertEqual(json.loads(self.issue().read_text())["carry_memory"], {"gist": "Counts.", "notes": [N1]})


if __name__ == "__main__":
    unittest.main()
