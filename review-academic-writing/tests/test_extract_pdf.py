import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
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


ex = load("extract_pdf")
rp = load("review_packets")

MINERU_MD = """<!-- page 1 of 3 -->

Running Head Journal 2026

# Admission Checking

**Abstract—We instantiate the method with** 14 **tools. It rejects** 0/42 **benign checks.**

## 1. Introduction

First paragraph line one
continues on line two.

Figure 1 shows the pipeline; Table 1 lists the parts.

1

<!-- page 2 of 3 -->

Running Head Journal 2026

<table><tr><td>Part</td><td>Trusted?</td></tr>
<tr><td>Twin</td><td>Yes</td></tr></table>

$$
x = y + 1
$$

Second page text with **bold** **adjacent** runs and the rate** 1/227 **kept.

Figure 1. The pipeline.

2

<!-- page 3 of 3 -->

Running Head Journal 2026

## 2. Method

Method text mentions Fig. 2 as well as Table 1 again.

| run | value |
| --- | --- |
| 1 | 2 |

3
"""


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BoldMergeTests(unittest.TestCase):
    def test_whole_paragraph_fragments_become_one_run(self):
        self.assertEqual(
            ex.merge_bold("**We instantiate the method with** 14 **tools. It rejects** 0/42 **benign checks.**"),
            "**We instantiate the method with 14 tools. It rejects 0/42 benign checks.**",
        )

    def test_inner_spaces_are_trimmed_and_words_stay_apart(self):
        self.assertEqual(ex.merge_bold("The gate rejects** 0/42 **benign checks."), "The gate rejects **0/42** benign checks.")

    def test_adjacent_runs_split_by_a_space_merge(self):
        self.assertEqual(ex.merge_bold("text with **bold** **adjacent** runs"), "text with **bold adjacent** runs")

    def test_long_gaps_are_left_alone(self):
        text = "**Term.** A sentence of ordinary length that is clearly not bold at all. **Other.**"
        self.assertEqual(ex.merge_bold(text), text)


class PostprocessTests(unittest.TestCase):
    def setUp(self):
        self.result = ex.postprocess(MINERU_MD, None, "fixture.md")
        self.markdown = self.result["markdown"]
        self.paragraphs = [p for p in self.markdown.split("\n\n")]

    def test_one_blank_line_between_paragraphs_and_none_inside(self):
        self.assertNotIn("\n\n\n", self.markdown)
        self.assertTrue(self.markdown.endswith("\n") and not self.markdown.endswith("\n\n"))
        self.assertIn("\nFirst paragraph line one continues on line two.\n", self.markdown)

    def test_page_markers_are_dropped(self):
        self.assertNotIn("<!-- page", self.markdown)
        self.assertEqual(self.result["markers"], 3)

    def test_repeated_headers_and_page_numbers_are_dropped(self):
        self.assertNotIn("Running Head Journal 2026", self.markdown)
        for number in ("\n1\n", "\n2\n", "\n3\n"):
            self.assertNotIn(number, self.markdown)
        self.assertIn("Running Head Journal 2026", self.result["notes"])
        self.assertEqual(self.result["dropped"], 6)

    def test_html_table_stays_one_block_and_latex_is_untouched(self):
        index = rp.build_index(self.markdown)
        tables = [b for b in index["blocks"] if "<table>" in b["text"]]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["text"], "<table><tr><td>Part</td><td>Trusted?</td></tr>\n<tr><td>Twin</td><td>Yes</td></tr></table>")
        self.assertIn("$$\nx = y + 1\n$$", self.markdown)
        pipe = [b for b in index["blocks"] if b["kind"] == "table"]
        self.assertEqual(pipe[0]["text"], "| run | value |\n| --- | --- |\n| 1 | 2 |")

    def test_bold_fixes_applied_inside_the_edition(self):
        self.assertIn("**Abstract—We instantiate the method with 14 tools. It rejects 0/42 benign checks.**", self.markdown)
        self.assertIn("with **bold adjacent** runs and the rate **1/227** kept.", self.markdown)

    def test_page_sidecar_maps_each_index_block_to_a_page(self):
        index = rp.build_index(self.markdown)
        sidecar = self.result["sidecar"]
        self.assertEqual(sidecar["page_source"], "markers")
        self.assertEqual(len(sidecar["paragraphs"]), len(index["blocks"]))
        for block, para in zip(index["blocks"], sidecar["paragraphs"]):
            self.assertEqual(para["sha256"], sha(block["text"]))
        by_text = {b["text"]: p for b, p in zip(index["blocks"], sidecar["paragraphs"])}
        self.assertEqual(by_text["# Admission Checking"]["page"], 1)
        self.assertEqual(by_text["$$\nx = y + 1\n$$"]["page"], 2)
        self.assertEqual(by_text["## 2. Method"]["page"], 3)
        self.assertEqual([p["ordinal"] for p in sidecar["paragraphs"]], list(range(1, len(index["blocks"]) + 1)))

    def test_layout_assigns_pages_when_markers_are_absent(self):
        without_markers = "\n".join(l for l in MINERU_MD.split("\n") if not l.startswith("<!-- page"))
        layout = [
            {"type": "text", "page_idx": 0, "text": "Admission Checking"},
            {"type": "text", "page_idx": 0, "text": "First paragraph line one continues on line two."},
            {"type": "table", "page_idx": 1, "table_body": "<table><tr><td>Part</td><td>Trusted?</td></tr></table>"},
            {"type": "text", "page_idx": 1, "text": "Second page text with bold adjacent runs and the rate 1/227 kept."},
            {"type": "text", "page_idx": 2, "text": "2. Method"},
            {"type": "text", "page_idx": 2, "text": "Method text mentions Fig. 2 as well as Table 1 again."},
        ]
        result = ex.postprocess(without_markers, layout, "fixture.md")
        sidecar = result["sidecar"]
        self.assertEqual(sidecar["page_source"], "layout")
        index = rp.build_index(result["markdown"])
        pages = {b["text"]: p["page"] for b, p in zip(index["blocks"], sidecar["paragraphs"])}
        self.assertEqual(pages["# Admission Checking"], 1)
        self.assertEqual(pages["## 2. Method"], 3)
        self.assertEqual(pages["Method text mentions Fig. 2 as well as Table 1 again."], 3)
        self.assertIn("Uncertain", result["notes"])


class CaseBuilderTests(unittest.TestCase):
    def setUp(self):
        self.result = ex.postprocess(MINERU_MD, None, "fixture.md")
        self.index = rp.build_index(self.result["markdown"])
        self.sidecar = self.result["sidecar"]
        self.ids = [b["block_id"] for b in self.index["blocks"]]

    def test_windows_partition_blocks_once_and_never_cross_a_heading(self):
        built = ex.build_case(self.index, self.sidecar, 12, "strict-prefix", [], "case-1")
        windows = built["case"]["windows"]
        self.assertEqual([bid for w in windows for bid in w], self.ids)
        kinds = {b["block_id"]: b["kind"] for b in self.index["blocks"]}
        for window in windows:
            self.assertFalse(any(kinds[bid] == "heading" for bid in window[1:]), window)
        self.assertEqual(windows[0], ["B0001", "B0002"])
        self.assertEqual(windows[1][0], "B0003")

    def test_window_builder_respects_the_budget(self):
        blocks = [{"id": f"X{i}", "text": " ".join(["w"] * n), "kind": "text"} for i, n in enumerate([50, 50, 50, 50, 50, 30], 1)]
        self.assertEqual(ex.build_windows(blocks, 100), [["X1", "X2"], ["X3", "X4"], ["X5", "X6"]])
        self.assertEqual(ex.build_windows(blocks, 500), [["X1", "X2", "X3", "X4", "X5", "X6"]])
        with self.assertRaises(ex.ExtractError):
            ex.build_windows(blocks, 0)

    def test_asset_references_are_detected_from_mentions(self):
        blocks = [{"id": b["block_id"], "text": b["text"]} for b in self.index["blocks"]]
        references = ex.find_asset_references(blocks)
        text = {b["block_id"]: b["text"] for b in self.index["blocks"]}
        self.assertEqual(sorted(references), ["Figure1", "Figure2", "Table1"])
        self.assertEqual([text[bid][:20] for bid in references["Figure1"]], ["Figure 1 shows the p", "Figure 1. The pipeli"])
        self.assertEqual(len(references["Table1"]), 2)
        self.assertTrue(text[references["Figure2"][0]].startswith("Method text mentions Fig. 2"))

    def test_assets_get_referenced_by_and_encountered_by(self):
        assets = [{"id": "Figure1", "page": 2, "path": "figures/fig1.png", "caption": "The pipeline."},
                  {"id": "Table 1", "page": 3, "path": "tables/t1.png"}]
        built = ex.build_case(self.index, self.sidecar, 40, "reference-jump", assets, "case-2")
        case = built["case"]
        self.assertEqual(case["asset_policy"], "reference-jump")
        first_on_page = {}
        for block in case["blocks"]:
            first_on_page.setdefault(block["page"], block["id"])
        figure, table = case["assets"]
        self.assertEqual(figure["id"], "Figure1")
        self.assertEqual(figure["encountered_by"], [first_on_page[2]])
        self.assertEqual(len(figure["referenced_by"]), 2)
        self.assertEqual(table["id"], "Table1")
        self.assertEqual(table["caption"], "")
        self.assertEqual(table["encountered_by"], [first_on_page[3]])
        self.assertEqual(built["report"]["unlisted_references"], ["Figure2"])
        mr = load("masked_reader")
        mr.validate_case(case)

    def test_stale_sidecar_is_refused(self):
        other = rp.build_index(self.result["markdown"] + "\nAn extra paragraph.\n")
        with self.assertRaises(ex.ExtractError):
            ex.build_case(other, self.sidecar, 40, "strict-prefix", [], "case-3")


FAKE_MINERU = """#!/bin/sh
if [ "$1" = "server" ]; then
  printf '%s\\n' '{status}'
  exit 0
fi
if [ "$1" = "parse" ]; then
  while [ $# -gt 0 ]; do
    if [ "$1" = "-o" ]; then shift; printf '# Parsed\\n\\nBody.\\n' > "$1"; fi
    shift
  done
  exit 0
fi
exit 1
"""


STATUS_READY = {
    "running": True,
    "version": "4.0.12",
    "parse_server": {
        "local": {"mode": "managed", "healthy": True, "starting": False, "managed_tier": "standard", "managed_running": True, "url": "http://127.0.0.1:16580", "error_code": None, "error_msg": None},
        "remote": {"healthy": True},
    },
    "error_summary": {"parse_errors": [{"code": "engine_unavailable", "count": 1}]},
}
STATUS_STARTING = {
    "running": True,
    "parse_server": {"local": {"mode": "managed", "healthy": False, "starting": True, "managed_tier": "standard", "managed_running": True}},
}


class ServerReadyTests(unittest.TestCase):
    def test_json_status_is_authoritative_and_ignores_old_error_counts(self):
        self.assertTrue(ex.server_ready(json.dumps(STATUS_READY), 0))
        self.assertFalse(ex.server_ready(json.dumps(STATUS_STARTING), 0))
        self.assertFalse(ex.server_ready(json.dumps(STATUS_READY), 1))

    def test_json_status_requires_a_local_parse_server(self):
        off = {"running": True, "parse_server": {"local": {"mode": "off", "healthy": False}, "remote": {"healthy": True}}}
        self.assertFalse(ex.server_ready(json.dumps(off), 0))
        down = {"running": False, "parse_server": {"local": {"mode": "managed", "healthy": True}}}
        self.assertFalse(ex.server_ready(json.dumps(down), 0))
        unmanaged = dict(STATUS_READY, parse_server={"local": {"mode": "managed", "healthy": True, "starting": False, "managed_running": False}})
        self.assertFalse(ex.server_ready(json.dumps(unmanaged), 0))

    def test_text_status_is_only_a_fallback(self):
        self.assertTrue(ex.server_ready("doclib: running\nparse server (standard): ready", 0))
        self.assertFalse(ex.server_ready("doclib: running\nparse server (standard): not ready, preparing startup", 0))

    def test_status_summary_keeps_the_local_block(self):
        summary = json.loads(ex.status_summary(json.dumps(STATUS_READY)))
        self.assertEqual(summary["mode"], "managed")
        self.assertEqual(summary["server_running"], True)
        self.assertEqual(ex.status_summary("plain text\n"), "plain text")


class ExtractCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, status):
        script = self.root / "mineru"
        script.write_text(FAKE_MINERU.replace("{status}", status))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return str(script)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ex.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_not_ready_server_exits_2_with_start_commands(self):
        mineru = self.fake(json.dumps(STATUS_STARTING))
        code, out, err = self.run_main(["extract", "--pdf", str(self.pdf), "--out-dir", str(self.root / "out"), "--mineru", mineru])
        self.assertEqual(code, 2)
        for command in ex.MINERU_START_COMMANDS:
            self.assertIn(command, err)
        self.assertFalse((self.root / "out" / "extract.json").exists())

    def test_remote_is_refused_before_anything_runs(self):
        code, out, err = self.run_main(["extract", "--pdf", str(self.pdf), "--out-dir", str(self.root / "out"), "--mineru", "/nonexistent/mineru", "--remote"])
        self.assertEqual(code, 2)
        self.assertIn("--remote is refused", err)

    def test_ready_server_runs_the_local_standard_parse(self):
        mineru = self.fake(json.dumps(STATUS_READY))
        code, out, err = self.run_main(["extract", "--pdf", str(self.pdf), "--out-dir", str(self.root / "out"), "--mineru", mineru])
        self.assertEqual(code, 0, err)
        record = json.loads((self.root / "out" / "extract.json").read_text())
        self.assertEqual(record["command"][1:4], ["parse", str(self.pdf), "--pages"])
        summary = json.loads(record["server_status"])
        self.assertEqual(summary["managed_tier"], "standard")
        self.assertTrue(summary["healthy"])
        self.assertEqual(record["command"][1:], ["parse", str(self.pdf), "--pages", "all", "--tier", "standard", "-o", str(self.root / "out" / "paper.md")])
        self.assertEqual((self.root / "out" / "paper.md").read_text(), "# Parsed\n\nBody.\n")
        self.assertFalse(record["remote"])

    def test_postprocess_and_case_through_the_cli(self):
        source = self.root / "mineru.md"
        source.write_text(MINERU_MD)
        clean = self.root / "clean.md"
        code, out, err = self.run_main(["postprocess", "--input", str(source), "--out", str(clean)])
        self.assertEqual(code, 0, err)
        response = json.loads(out)
        self.assertEqual(response["dropped_headers"], 6)
        index_path = self.root / "index.json"
        rp.write_json_atomic(index_path, rp.build_index(clean.read_text()))
        assets = self.root / "assets.json"
        assets.write_text(json.dumps([{"id": "Figure1", "page": 2, "path": "fig1.png", "caption": "c"}]))
        case_path = self.root / "case.json"
        code, out, err = self.run_main(["case", "--index", str(index_path), "--pages", response["pages"], "--budget", "30", "--asset-policy", "strict-prefix", "--assets", str(assets), "--out", str(case_path)])
        self.assertEqual(code, 0, err)
        case = json.loads(case_path.read_text())
        self.assertEqual(case["assets"][0]["id"], "Figure1")
        self.assertEqual([b for w in case["windows"] for b in w], [b["id"] for b in case["blocks"]])
        self.assertEqual(json.loads(out)["unlisted_references"], ["Figure2", "Table1"])

    def test_help_runs_isolated_from_another_directory(self):
        result = subprocess.run([sys.executable, "-B", "-I", str(SCRIPTS / "extract_pdf.py"), "--help"], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("postprocess", result.stdout)


if __name__ == "__main__":
    unittest.main()
