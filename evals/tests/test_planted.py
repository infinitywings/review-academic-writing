import contextlib
import importlib.util
import io
import json
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


pl = load("planted")
rp = pl.rp
SPEC = EVALS / "defects.json"


class PlantedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.spec = pl.load_spec(SPEC)
        self.clean = rp.normalize_text((EVALS / self.spec["manuscript"]).read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pl.main(argv)
        return code, out.getvalue(), err.getvalue()

    def planted(self, wanted="all", name="p"):
        code, out, err = self.main(["plant", "--defects", wanted, "--out", str(self.root / name)])
        self.assertEqual(code, 0, err)
        return json.loads((self.root / name / "key.json").read_text()), (self.root / name / "manuscript.md").read_text()

    def test_every_defect_plants_alone_and_all_plant_together(self):
        ids = [d["id"] for d in self.spec["defects"]]
        self.assertEqual(len(ids), 8)
        for n, defect in enumerate(ids):
            key, text = self.planted(defect, f"one-{n}")
            self.assertEqual(key["planted"], [defect])
            self.assertNotEqual(text, self.clean)
        key, text = self.planted()
        self.assertEqual(key["planted"], ids)
        again = pl.apply(self.clean, self.spec["defects"])
        self.assertEqual(text, again, "planting is deterministic")
        self.assertEqual(key["snapshot_id"], rp.build_index(text)["snapshot_id"])

    def test_each_defect_has_its_own_blocks_and_controls_are_untouched(self):
        key, text = self.planted()
        blocks = {b["block_id"]: b["text"] for b in rp.build_index(text)["blocks"]}
        owner = {}
        for defect in key["defects"]:
            for anchor in defect["expect"]:
                self.assertIn(anchor["quote"], blocks[anchor["block_id"]])
                self.assertEqual(owner.setdefault(anchor["block_id"], defect["id"]), defect["id"], "one block answers for one defect")
        controls = {c["block_id"] for c in key["controls"]}
        self.assertEqual(len(controls), 4)
        self.assertFalse(controls & set(owner))
        clean_blocks = {b["text"] for b in rp.build_index(self.clean)["blocks"]}
        for block in controls:
            self.assertIn(blocks[block], clean_blocks, "a control passage is the same in the clean and the planted text")

    def test_expected_quotes_exist_only_after_planting(self):
        # A moved definition leaves the sentence that uses the term unchanged, so its expected quotes are in the clean text too.
        for defect in self.spec["defects"]:
            if defect["type"] == "definition_after_use":
                continue
            for quote in defect["expect"]:
                self.assertNotIn(quote, self.clean, defect["id"])

    def test_score_counts_found_missed_false_alarms_and_elsewhere(self):
        key, text = self.planted()
        (self.root / "p" / "manuscript.md").write_text(text)
        by = {d["id"]: d["expect"][0] for d in key["defects"]}
        findings = self.root / "findings.json"
        findings.write_text(json.dumps({"candidates": [
            {"id": "C01", "anchor": by["D02"]},
            {"id": "C02", "anchor": {"block_id": by["D02"]["block_id"], "quote": "IRB"}},
            {"id": "C03", "anchor": by["D08"]},
            {"id": "C04", "anchor": key["controls"][0]},
            {"id": "C05", "anchor": {"block_id": "B0001", "quote": "Counting repeat visits"}},
        ]}))
        more = self.root / "more.json"
        more.write_text(json.dumps([{"block_id": by["D05"]["block_id"]}]))
        code, out, err = self.main(["score", "--key", str(self.root / "p" / "key.json"), "--manuscript", str(self.root / "p" / "manuscript.md"),
                                    "--findings", str(findings), "--findings", str(more), "--out", str(self.root / "score.json")])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["planted"], result["found"], result["findings"]), (8, 3, 6))
        self.assertEqual(result["missed"], ["D01", "D03", "D04", "D06", "D07"])
        self.assertEqual({d["id"]: d["by"] for d in result["defects"] if d["found"]}, {"D02": ["C01", "C02"], "D05": ["more.json#1"], "D08": ["C03"]})
        self.assertEqual((result["false_alarms"], result["elsewhere"], result["control_blocks"]), (["C04"], 1, 4))
        self.assertEqual(json.loads((self.root / "score.json").read_text()), result)

    def test_score_refuses_findings_made_on_another_text(self):
        key, text = self.planted("D02", "two")
        base = ["score", "--key", str(self.root / "two" / "key.json")]
        clean = self.root / "clean.md"
        clean.write_text(self.clean)
        ok = self.root / "ok.json"
        ok.write_text(json.dumps([key["defects"][0]["expect"][0]]))
        cases = (
            (str(clean), ok, [key["defects"][0]["expect"][0]], "The manuscript is not the planted copy this key was written for."),
            (str(self.root / "two" / "manuscript.md"), self.root / "ghost.json", [{"block_id": "B0999", "quote": "x"}], "points at B0999, which is not a block of the planted manuscript."),
            (str(self.root / "two" / "manuscript.md"), self.root / "stale.json", [{"block_id": "B0009", "quote": "an infrared break-beam (IRB) sensor"}], "quotes text that is not in B0009; it was made on a different text."),
            (str(self.root / "two" / "manuscript.md"), self.root / "bare.json", [{"quote": "no block"}], "finding 1 has no anchor with a block_id."),
        )
        for manuscript, path, value, message in cases:
            with self.subTest(message=message):
                path.write_text(json.dumps(value))
                code, out, err = self.main([*base, "--manuscript", manuscript, "--findings", str(path)])
                self.assertEqual(code, 2)
                self.assertIn(message, json.loads(err)["error"]["message"])

    def test_plant_refuses_bad_requests(self):
        for wanted, message in (("D99", "unknown: D99."), ("D01,D01", "comma-separated list of distinct ids"), ("", "comma-separated list of distinct ids")):
            code, out, err = self.main(["plant", "--defects", wanted, "--out", str(self.root / "bad")])
            self.assertEqual(code, 2)
            self.assertIn(message, json.loads(err)["error"]["message"])
            self.assertFalse((self.root / "bad").exists())
        self.planted("D01", "once")
        code, out, err = self.main(["plant", "--defects", "D01", "--out", str(self.root / "once")])
        self.assertEqual(code, 2, "an existing output directory is refused")
        # A replacement that does not match exactly once stops the planting.
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "manuscript.md").write_text("The cat sat. The cat sat.\n")
        (broken / "defects.json").write_text(json.dumps({"manuscript": "manuscript.md", "controls": [], "defects": [
            {"id": "X1", "type": "t", "description": "d", "replace": [{"find": "The cat sat.", "with": "It sat."}], "expect": ["It sat."]}]}))
        code, out, err = self.main(["plant", "--defects-file", str(broken / "defects.json"), "--out", str(self.root / "broken-out")])
        self.assertEqual(code, 2)
        self.assertIn("Defect X1: the text to replace occurs 2 times, not once", json.loads(err)["error"]["message"])

    def test_list_names_every_defect(self):
        code, out, err = self.main(["list"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual([d["id"] for d in result["defects"]], [f"D0{n}" for n in range(1, 9)])
        self.assertEqual(result["controls"], 4)


if __name__ == "__main__":
    unittest.main()
