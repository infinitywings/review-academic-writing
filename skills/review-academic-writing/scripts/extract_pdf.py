#!/usr/bin/env python3
"""Turn a manuscript PDF into a review edition: MinerU extraction, Markdown cleanup, masked case.

Python standard library only. Three subcommands:

  extract      run the local MinerU parse (standard tier) on a PDF; never remote.
               With --fallback pdftotext|pdfplumber, fall back to a text-only,
               low-fidelity extraction when MinerU is unavailable; by default
               the command stops and prints the MinerU start commands instead.
  postprocess  clean a MinerU Markdown file for review_packets.py index and write a
               page sidecar plus extraction notes.
  case         build a masked_reader case.json from an index.json, the page sidecar,
               a word budget, and a user-supplied asset list.

The cleaned Markdown has exactly one blank line between paragraphs and none inside a
paragraph, so each paragraph becomes one review_packets block. The sidecar lists those
paragraphs in order with a page number and a text hash; `case` checks the hash against
the index so a stale sidecar is refused rather than silently misaligned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


SIDECAR_SCHEMA = 1
MINERU_TIER = "standard"
MINERU_START_COMMANDS = [
    "mineru-kit models download --tier standard",
    "mineru server start",
    "mineru config set parse_server.local.mode managed",
    "mineru server restart",
    "mineru server status --json",
]
FALLBACK_EXTRACTORS = ("pdftotext", "pdfplumber")
LOW_FIDELITY_NOTE = (
    "LOW-FIDELITY EXTRACTION with {extractor}; MinerU was not used ({reason}). "
    "Two-column reading order, subscripts and superscripts, formulas, tables, and figure captions "
    "may be lost or merged. Check every region used for a finding against the PDF, and say in the "
    "scope record that the edition is text-only."
)
EXTRACTION_MARKER = re.compile(r"^\s*<!--\s*extraction:\s*(.*?)\s*-->\s*$", re.I)
PAGE_MARKER = re.compile(r"^\s*<!--\s*page\s+(\d+)(?:\s+of\s+(\d+))?\s*-->\s*$", re.I)
HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+\S")
LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+")
ASSET_REFERENCE = re.compile(r"\b(Fig(?:ure|s)?\.?|Tables?)\s*~?\s*(\d{1,3})\b", re.I)
HEADER_WORD_LIMIT = 12
HEADER_REPEAT_PAGES = 3


class ExtractError(ValueError):
    """An actionable data or command-line error."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- Markdown segments


class Segment:
    """One block of the cleaned edition: a kind, its source lines, and a page."""

    __slots__ = ("kind", "lines", "page", "ordinal")

    def __init__(self, kind: str, lines: list[str], page: int | None) -> None:
        self.kind = kind
        self.lines = lines
        self.page = page
        self.ordinal = 0

    def text(self) -> str:
        if self.kind == "paragraph":
            return merge_bold(" ".join(line.strip() for line in self.lines))
        if self.kind == "heading":
            return merge_bold(self.lines[0].strip())
        if self.kind == "list":
            return "\n".join(merge_bold(line.rstrip()) for line in self.lines)
        # Verbatim blocks keep their lines, minus blank ones: the indexer splits a
        # block at a blank line, and the sidecar must map one paragraph to one block.
        return "\n".join(line.rstrip() for line in self.lines if line.strip())

    def blank_lines_inside(self) -> int:
        return sum(1 for line in self.lines if not line.strip()) if self.kind in ("table", "math", "code") else 0

    def raw(self) -> str:
        return "\n".join(self.lines)

    def words(self) -> int:
        return len(self.text().split())


def segment_markdown(text: str) -> tuple[list[Segment], int]:
    """Split MinerU Markdown into segments; tables, display math, and code stay whole."""
    segments: list[Segment] = []
    markers = 0
    page: int | None = None
    current: Segment | None = None
    closer: str | None = None  # the line pattern that ends a multi-line verbatim block

    def close() -> None:
        nonlocal current, closer
        if current is not None:
            segments.append(current)
        current, closer = None, None

    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if closer is not None:
            current.lines.append(line)
            if (closer == "</table>" and "</table>" in stripped.lower()) or (closer != "</table>" and stripped == closer) or (closer == "$$" and stripped.endswith("$$") and stripped != "$$" and len(current.lines) > 1):
                close()
            continue
        marker = PAGE_MARKER.match(line)
        if marker:
            close()
            page = int(marker.group(1))
            markers += 1
            continue
        if not stripped:
            close()
            continue
        lower = stripped.lower()
        if lower.startswith("<table") or lower.startswith("<html"):
            close()
            current, closer = Segment("table", [line], page), "</table>"
            if "</table>" in lower:
                close()
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            close()
            current, closer = Segment("code", [line], page), stripped[:3]
        elif stripped == "$$" or (stripped.startswith("$$") and not stripped.endswith("$$")):
            close()
            current, closer = Segment("math", [line], page), "$$"
        elif stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
            close()
            segments.append(Segment("math", [line], page))
        elif HEADING.match(line):
            close()
            segments.append(Segment("heading", [line], page))
        elif stripped.startswith("!["):
            close()
            segments.append(Segment("image", [line], page))
        elif stripped.startswith("|"):
            if current is None or current.kind != "table":
                close()
                current = Segment("table", [], page)
            current.lines.append(line)
        elif LIST_ITEM.match(line) or (current is not None and current.kind == "list" and line[:1].isspace()):
            if current is None or current.kind != "list":
                close()
                current = Segment("list", [], page)
            current.lines.append(line)
        else:
            if current is None or current.kind != "paragraph":
                close()
                current = Segment("paragraph", [], page)
            current.lines.append(line)
    close()
    return segments, markers


# ---------------------------------------------------------------- bold repair

BOLD_INNER_SPACE = re.compile(r"\*\*[ \t]+([^*\n]+?)[ \t]+\*\*")
BOLD_ADJACENT = re.compile(r"\*\*([^*\n]+?)\*\*[ \t]+\*\*([^*\n]+?)\*\*")
BOLD_GAP_WORDS = 6


def merge_bold(text: str) -> str:
    """Repair MinerU bold fragments without changing the words.

    1. A paragraph that opens and closes with ** and is broken by short unbolded gaps
       (`**...with** 14 **MCP tools...**`) becomes one bold run.
    2. `** 0/42 **` becomes `**0/42**`, with a space kept on each side so words stay apart.
    3. `**a** **b**` becomes `**a b**`.
    """
    stripped = text.strip()
    if stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") >= 4:
        inner = stripped[2:-2]
        parts = inner.split("**")
        gaps = parts[1::2]
        if all(len(gap.split()) <= BOLD_GAP_WORDS for gap in gaps):
            text = "**" + " ".join("".join(parts).split()) + "**"
    def tighten(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if len(inner.split()) > BOLD_GAP_WORDS:
            return match.group(0)
        return " **" + inner + "** "
    text = BOLD_INNER_SPACE.sub(tighten, text)
    previous = None
    while previous != text:
        previous = text
        text = BOLD_ADJACENT.sub(lambda m: "**" + m.group(1) + " " + m.group(2) + "**", text)
    return " ".join(text.split()) if "\n" not in text else text


# ---------------------------------------------------------------- headers and footers


def header_key(segment: Segment) -> str | None:
    if segment.kind not in ("paragraph", "heading"):
        return None
    words = segment.text().split()
    if not words or len(words) > HEADER_WORD_LIMIT:
        return None
    return re.sub(r"\d+", "#", " ".join(words)).lower()


def drop_repeated_headers(segments: list[Segment]) -> list[tuple[Segment, str]]:
    """Remove short lines that recur at the top or bottom of three or more pages.

    Returns the dropped segments with a reason. Needs page numbers; without them nothing
    is dropped. The caller removes the returned segments from its list.
    """
    pages: dict[int, list[Segment]] = {}
    for segment in segments:
        if segment.page is not None:
            pages.setdefault(segment.page, []).append(segment)
    if len(pages) < HEADER_REPEAT_PAGES:
        return []
    edge: dict[str, dict[int, list[Segment]]] = {}
    for page, items in pages.items():
        for segment in items[:2] + items[-2:]:
            key = header_key(segment)
            if key is not None:
                edge.setdefault(key, {}).setdefault(page, []).append(segment)
    dropped: list[tuple[Segment, str]] = []
    for key, by_page in edge.items():
        bare_number = key == "#"
        if len(by_page) >= HEADER_REPEAT_PAGES or (bare_number and len(by_page) >= 2):
            for page in sorted(by_page):
                for segment in by_page[page]:
                    dropped.append((segment, f"repeats on pages {', '.join(str(p) for p in sorted(by_page))}"))
    return dropped


GUTTER_MIN_NUMBERS = 5


def drop_line_number_gutters(segments: list[Segment]) -> list[tuple[Segment, str]]:
    """Remove lines that are only a run of consecutive line numbers.

    Submission templates (ACL, NeurIPS) print a line-number gutter down the margin;
    layout parsers capture it as a paragraph of 3- or 4-digit integers rising by one.
    Such a line is dropped when it holds at least GUTTER_MIN_NUMBERS integers and every
    integer is the previous one plus one. The caller removes the returned segments.
    """
    dropped: list[tuple[Segment, str]] = []
    for segment in segments:
        if segment.kind not in ("paragraph", "heading", "list"):
            continue
        tokens = segment.text().split()
        if len(tokens) < GUTTER_MIN_NUMBERS or not all(re.fullmatch(r"\d{3,4}", t) for t in tokens):
            continue
        numbers = [int(t) for t in tokens]
        if all(b == a + 1 for a, b in zip(numbers, numbers[1:])):
            dropped.append((segment, f"line-number gutter {numbers[0]}-{numbers[-1]}"))
    return dropped


# ---------------------------------------------------------------- layout page lookup


def normalize_for_match(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[^0-9a-z一-鿿]+", "", text.lower())


def _strings(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for item in value:
            _strings(item, out)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in ("content", "text", "table_body", "table_caption", "img_caption", "img_footnote", "table_footnote", "text_level", "html"):
                _strings(item, out)
            elif isinstance(item, (list, dict)):
                _strings(item, out)


def layout_pages(layout: Any) -> tuple[dict[int, str], list[str]]:
    """Return {page_number: normalized text} and any header/footer texts the layout labels.

    Accepts MinerU's middle.json (``pages[].blocks[]``) and content_list.json (a flat list
    of ``{page_idx, ...}``). Page numbers are one-based.
    """
    blobs: dict[int, list[str]] = {}
    labeled: list[str] = []
    if isinstance(layout, dict) and isinstance(layout.get("pages"), list):
        items = [(page.get("page_idx"), block) for page in layout["pages"] if isinstance(page, dict) for block in page.get("blocks", [])]
    elif isinstance(layout, list):
        items = [(item.get("page_idx"), item) for item in layout if isinstance(item, dict)]
    else:
        raise ExtractError("--layout must be MinerU middle.json (object with pages) or content_list.json (array).")
    for page_idx, block in items:
        if not isinstance(page_idx, int) or isinstance(page_idx, bool):
            continue
        texts: list[str] = []
        _strings(block, texts)
        blobs.setdefault(page_idx + 1, []).extend(texts)
        if isinstance(block, dict) and str(block.get("type", "")).lower() in ("header", "footer", "page_number", "page_footer", "page_header"):
            labeled.append(f"page {page_idx + 1}: " + " ".join(" ".join(texts).split())[:80])
    return {page: normalize_for_match(" ".join(texts)) for page, texts in blobs.items()}, labeled


def assign_pages_from_layout(segments: list[Segment], pages: dict[int, str]) -> int:
    """Set segment.page by locating the segment's opening text on a page; returns unmatched count."""
    unmatched = 0
    current = 1
    ordered = sorted(pages)
    for segment in segments:
        text = segment.text()
        candidates = [normalize_for_match(re.sub(r"[#*_`$\\{}]", " ", text))]
        if segment.kind != "math":
            candidates.append(normalize_for_match(re.sub(r"[#*_`\\{}]", " ", re.sub(r"\$[^$\n]*\$", " ", text))))
        found = None
        for length in (40, 24, 14):
            for full in candidates:
                probe = full[:length]
                if len(probe) < min(length, 6):
                    continue
                for page in [p for p in ordered if p >= current] + [p for p in ordered if p < current]:
                    if probe in pages[page]:
                        found = page
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            unmatched += 1
            segment.page = None
        else:
            segment.page = found
            current = found
    return unmatched


# ---------------------------------------------------------------- postprocess


def postprocess(markdown: str, layout: Any | None, source_name: str) -> dict[str, Any]:
    """Clean MinerU Markdown; return the edition, the sidecar, and the notes text.

    A `<!-- extraction: ... -->` comment written by a fallback extractor is removed
    from the edition and copied to the top of the notes, so the scope record carries
    the low-fidelity warning.
    """
    extraction_warnings: list[str] = []
    kept_lines: list[str] = []
    for line in markdown.split("\n"):
        marker = EXTRACTION_MARKER.match(line)
        if marker:
            extraction_warnings.append(marker.group(1))
        else:
            kept_lines.append(line)
    markdown = "\n".join(kept_lines)
    segments, markers = segment_markdown(markdown)
    page_source = "markers" if markers else "none"
    layout_labels: list[str] = []
    unmatched = 0
    if layout is not None:
        pages, layout_labels = layout_pages(layout)
        if not markers:
            unmatched = assign_pages_from_layout(segments, pages)
            page_source = "layout"
    dropped = drop_repeated_headers(segments)
    gutters = drop_line_number_gutters(segments)
    dropped = dropped + [g for g in gutters if id(g[0]) not in {id(d[0]) for d in dropped}]
    dropped_ids = {id(segment) for segment, _ in dropped}
    kept = [segment for segment in segments if id(segment) not in dropped_ids]
    joined = sum(1 for s in kept if s.kind == "paragraph" and len(s.lines) > 1)
    bold_fixed = [s for s in kept if s.kind in ("paragraph", "heading", "list") and s.text() != " ".join(s.raw().split()) and "**" in s.raw()]
    rendered: list[str] = []
    paragraphs: list[dict[str, Any]] = []
    for ordinal, segment in enumerate(kept, 1):
        segment.ordinal = ordinal
        text = segment.text()
        rendered.append(text)
        paragraphs.append({"ordinal": ordinal, "kind": segment.kind, "page": segment.page, "sha256": sha256_text(text)})
    edition = "\n\n".join(rendered) + "\n"
    sidecar = {
        "schema_version": SIDECAR_SCHEMA,
        "artifact_type": "page_sidecar",
        "source_markdown": source_name,
        "page_source": page_source,
        "paragraphs": paragraphs,
    }
    origin = "text-only fallback extraction" if extraction_warnings else "MinerU Markdown"
    notes = [f"Extraction notes for {source_name} ({origin} cleaned by extract_pdf.py postprocess).", ""]
    notes.extend(extraction_warnings)
    if extraction_warnings:
        notes.append("")
    notes.append(f"Paragraphs in the cleaned edition: {len(kept)}. Page numbers from: {page_source}.")
    notes.append(f"Dropped {markers} page marker comment(s); page numbers live in the sidecar, not in the text.")
    if dropped:
        notes.append("Dropped repeated page headers/footers:")
        notes.extend(f"  - page {s.page}: {s.text()[:80]!r} ({reason})" for s, reason in dropped)
    else:
        notes.append("No repeated page header or footer lines were found" + ("." if page_source != "none" else "; header removal needs page numbers and was skipped."))
    if gutters:
        notes.append(f"Dropped {len(gutters)} line-number gutter line(s) (submission line numbers captured as text): " + "; ".join(reason for _, reason in gutters[:12]) + ("; ..." if len(gutters) > 12 else "") + ".")
    notes.append(f"Joined {joined} multi-line paragraph(s) into single lines; words were not changed.")
    if bold_fixed:
        notes.append(f"Repaired bold markers in {len(bold_fixed)} paragraph(s) (ordinals {', '.join(str(s.ordinal) for s in bold_fixed)}); emphasis boundaries changed, words did not. Uncertain: whether every merged run was meant to be bold.")
    kinds = {k: [s.ordinal for s in kept if s.kind == k] for k in ("table", "math", "code", "image")}
    if kinds["table"]:
        notes.append(f"Kept {len(kinds['table'])} table block(s) whole (ordinals {', '.join(map(str, kinds['table']))}); HTML tables are single blocks.")
    if kinds["math"]:
        notes.append(f"Kept {len(kinds['math'])} display-math block(s) untouched (ordinals {', '.join(map(str, kinds['math']))}).")
    squeezed = [s for s in kept if s.blank_lines_inside()]
    if squeezed:
        notes.append(f"Removed blank lines inside {len(squeezed)} verbatim block(s) (ordinals {', '.join(str(s.ordinal) for s in squeezed)}) so each stays one index block; code or table spacing changed, text did not.")
    if kinds["image"]:
        notes.append(f"Image links kept as text at ordinals {', '.join(map(str, kinds['image']))}; the image files themselves were not exported. Supply figures through the case asset list.")
    missing = [s.ordinal for s in kept if s.page is None]
    if missing:
        notes.append(f"Uncertain: {len(missing)} paragraph(s) have no page number (ordinals {', '.join(map(str, missing[:40]))}{', ...' if len(missing) > 40 else ''}).")
    if unmatched:
        notes.append(f"Uncertain: {unmatched} paragraph(s) could not be located in the layout file.")
    if layout_labels:
        notes.append("Layout labels these blocks as header/footer; they were kept unless they repeated across pages:")
        notes.extend("  - " + label for label in layout_labels[:40])
    notes.append("Not checked here: reading order across columns, hyphenation at line ends, subscripts inside inline LaTeX, and figure content.")
    return {
        "markdown": edition,
        "sidecar": sidecar,
        "notes": "\n".join(notes) + "\n",
        "dropped": len(dropped),
        "markers": markers,
        "fidelity": "low" if extraction_warnings else "standard",
    }


# ---------------------------------------------------------------- case builder


def canonical_asset_id(kind: str, number: str) -> str:
    return ("Table" if kind.lower().startswith("t") else "Figure") + str(int(number))


def find_asset_references(blocks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map Figure N / Table N to the ids of blocks that mention them, in source order."""
    references: dict[str, list[str]] = {}
    for block in blocks:
        for match in ASSET_REFERENCE.finditer(block["text"]):
            asset_id = canonical_asset_id(match.group(1), match.group(2))
            mentions = references.setdefault(asset_id, [])
            if block["id"] not in mentions:
                mentions.append(block["id"])
    return references


def build_windows(blocks: list[dict[str, Any]], budget: int) -> list[list[str]]:
    """Group consecutive blocks into windows of about `budget` words; a heading always opens a window."""
    if budget <= 0:
        raise ExtractError("--budget must be a positive number of words.")
    windows: list[list[str]] = []
    current: list[str] = []
    words = 0
    for block in blocks:
        count = len(block["text"].split())
        starts_new = block["kind"] == "heading" or (current and words + count > budget and words >= budget / 2)
        if starts_new and current:
            windows.append(current)
            current, words = [], 0
        current.append(block["id"])
        words += count
    if current:
        windows.append(current)
    return windows


def build_case(index: dict[str, Any], sidecar: dict[str, Any], budget: int, asset_policy: str, assets: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    blocks_in = index.get("blocks")
    if not isinstance(blocks_in, list) or not blocks_in:
        raise ExtractError("index.json has no blocks.")
    paragraphs = sidecar.get("paragraphs")
    if sidecar.get("artifact_type") != "page_sidecar" or not isinstance(paragraphs, list):
        raise ExtractError("--pages must be the page sidecar written by postprocess.")
    if len(paragraphs) != len(blocks_in):
        raise ExtractError(f"Sidecar lists {len(paragraphs)} paragraphs but the index has {len(blocks_in)} blocks; re-run postprocess and index on the same edition.")
    blocks: list[dict[str, Any]] = []
    for block, para in zip(blocks_in, paragraphs):
        if sha256_text(block["text"]) != para.get("sha256"):
            raise ExtractError(f"Sidecar paragraph {para.get('ordinal')} does not match index block {block['block_id']}; re-run postprocess and index on the same edition.")
        blocks.append({"id": block["block_id"], "text": block["text"], "page": para.get("page"), "kind": block["kind"]})
    references = find_asset_references(blocks)
    first_block_on_page: dict[int, str] = {}
    for block in blocks:
        if isinstance(block["page"], int) and block["page"] not in first_block_on_page:
            first_block_on_page[block["page"]] = block["id"]
    case_assets: list[dict[str, Any]] = []
    listed: set[str] = set()
    for number, entry in enumerate(assets):
        label = f"assets[{number}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("path"), str):
            raise ExtractError(f"{label} needs string id and path.")
        match = re.fullmatch(r"(Figure|Table)\s*(\d+)", entry["id"].strip(), re.I)
        if not match:
            raise ExtractError(f"{label}.id must look like Figure3 or Table1.")
        asset_id = canonical_asset_id(match.group(1), match.group(2))
        if asset_id in listed:
            raise ExtractError(f"Duplicate asset id {asset_id}.")
        listed.add(asset_id)
        page = entry.get("page")
        if not isinstance(page, int) or isinstance(page, bool):
            raise ExtractError(f"{label}.page must be an integer page number.")
        encountered = [first_block_on_page[page]] if page in first_block_on_page else []
        case_assets.append({
            "id": asset_id,
            "path": entry["path"],
            "caption": entry.get("caption", "") if isinstance(entry.get("caption"), str) else "",
            "page": page,
            "referenced_by": references.get(asset_id, []),
            "encountered_by": encountered,
        })
    case = {
        "id": case_id,
        "asset_policy": asset_policy,
        "blocks": blocks,
        "windows": build_windows(blocks, budget),
        "assets": case_assets,
    }
    report = {
        "unlisted_references": sorted(a for a in references if a not in listed),
        "assets_without_references": sorted(a["id"] for a in case_assets if not a["referenced_by"]),
        "assets_off_page": sorted(a["id"] for a in case_assets if not a["encountered_by"]),
    }
    return {"case": case, "report": report}


# ---------------------------------------------------------------- MinerU extract


MINERU_INSTALL_HINT = 'pass --mineru, set MINERU, or run scripts/setup_mineru.sh next to this script (it installs MinerU with `uv tool install "mineru==4.0.12" --python 3.12`, downloads the models and starts the managed parse server; see README)'
MINERU_NOT_FOUND = "MinerU executable not found: " + MINERU_INSTALL_HINT


def mineru_executable(explicit: str | None) -> str | None:
    return explicit or os.environ.get("MINERU") or shutil.which("mineru")


def mineru_unavailable(mineru: str | None, timeout: float) -> tuple[str | None, str]:
    """Return (reason, status_text); reason is None when the local parse server is ready."""
    if not mineru:
        return MINERU_NOT_FOUND, ""
    try:
        status = subprocess.run([mineru, "server", "status", "--json"], capture_output=True, text=True, timeout=timeout)
    except OSError as exc:
        return f"MinerU executable not found or not runnable ({mineru}: {exc.strerror or exc}): " + MINERU_INSTALL_HINT, ""
    status_text = (status.stdout + status.stderr).strip()
    if server_ready(status.stdout, status.returncode):
        return None, status.stdout
    return "MinerU parse server is not ready", status_text


def server_ready(status_output: str, exit_code: int) -> bool:
    """True when `mineru server status --json` reports a healthy local parse server.

    The JSON form is authoritative. The text form is a fallback for MinerU builds
    without --json; it is width-sensitive, so it is only trusted when JSON is absent.
    """
    if exit_code != 0:
        return False
    try:
        status = json.loads(status_output)
    except ValueError:
        status = None
    if isinstance(status, dict):
        return local_parse_server_ready(status)
    text = status_output.lower()
    if re.search(r"not ready|preparing|stopped|not running|starting", text):
        return False
    return re.search(r"parse[^\n]*\bready\b|\bready\b[^\n]*parse|\brunning\b", text) is not None


def local_parse_server_ready(status: dict[str, Any]) -> bool:
    if not status.get("running"):
        return False
    local = (status.get("parse_server") or {}).get("local") or {}
    if local.get("mode") in (None, "off", "disabled"):
        return False
    if local.get("starting") or not local.get("healthy"):
        return False
    if local.get("mode") == "managed" and local.get("managed_running") is False:
        return False
    return True


def status_summary(status_output: str) -> str:
    """Keep the record readable: the local parse-server block when JSON, else the raw text."""
    try:
        status = json.loads(status_output)
    except ValueError:
        return status_output.strip()
    if not isinstance(status, dict):
        return status_output.strip()
    local = (status.get("parse_server") or {}).get("local") or {}
    keep = {k: local.get(k) for k in ("mode", "healthy", "starting", "managed_tier", "managed_running", "url", "error_code", "error_msg")}
    keep["server_running"] = status.get("running")
    keep["version"] = status.get("version")
    return json.dumps(keep, ensure_ascii=False)


def run_extract(pdf: Path, out_dir: Path, mineru: str | None, pages: str, timeout: float, fallback: str | None = None, pdftotext: str | None = None) -> dict[str, Any]:
    if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
        raise ExtractError("--pdf must be an existing .pdf file.")
    reason, status_text = mineru_unavailable(mineru, timeout)
    if reason is not None:
        if fallback is None:
            raise ExtractError(
                reason + (". Status output:\n" + status_text if status_text else "") + "\n\n"
                "Start it with scripts/setup_mineru.sh (next to this script; idempotent, --check only reports), or by hand:\n  " + "\n  ".join(MINERU_START_COMMANDS)
                + "\nWait until `mineru server status` reports the parse server ready, then retry. Never use --remote for an unpublished manuscript.\n"
                "For a text-only, low-fidelity edition instead (two-column order, formulas, and tables may be lost), rerun with --fallback pdftotext or --fallback pdfplumber."
            )
        return run_fallback(pdf, out_dir, fallback, reason, status_text, pdftotext, timeout)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (pdf.stem + ".md")
    if target.exists():
        raise ExtractError(f"Output already exists: {target}; choose a new --out-dir.")
    command = [mineru, "parse", str(pdf), "--pages", pages, "--tier", MINERU_TIER, "--wait", str(int(timeout)), "-o", str(target)]
    before = {p.name for p in out_dir.iterdir()}
    run = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    produced = sorted(p.name for p in out_dir.iterdir() if p.name not in before)
    record = {
        "ok": run.returncode == 0 and target.exists(),
        "extractor": "mineru",
        "fidelity": "standard",
        "command": command,
        "exit_code": run.returncode,
        "stdout": run.stdout[-4000:],
        "stderr": run.stderr[-4000:],
        "server_status": status_summary(status_text),
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "produced": produced,
        "markdown": str(target) if target.exists() else None,
        "tier": MINERU_TIER,
        "remote": False,
    }
    with (out_dir / "extract.json").open("w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if not record["ok"]:
        raise ExtractError(f"MinerU parse failed (exit {run.returncode}); see {out_dir / 'extract.json'}.")
    return record


# ---------------------------------------------------------------- low-fidelity fallback


def pdftotext_pages(pdf: Path, executable: str | None, timeout: float) -> tuple[list[str], list[str], str]:
    """Return (pages, command, stderr) from poppler's pdftotext; pages are split on form feeds."""
    exe = executable or shutil.which("pdftotext")
    if not exe:
        raise ExtractError("pdftotext not found: install poppler (brew install poppler) or pass --pdftotext; or use --fallback pdfplumber.")
    command = [exe, "-enc", "UTF-8", str(pdf), "-"]
    run = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0:
        raise ExtractError(f"pdftotext failed (exit {run.returncode}): {run.stderr.strip()[:500]}")
    pages = run.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages, command, run.stderr


def pdfplumber_pages(pdf: Path) -> tuple[list[str], list[str], str]:
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ExtractError("pdfplumber is not installed (pip install pdfplumber), or use --fallback pdftotext.") from exc
    with pdfplumber.open(str(pdf)) as document:
        pages = [page.extract_text() or "" for page in document.pages]
    return pages, ["pdfplumber", str(pdf)], ""


def fallback_markdown(pages: list[str], note: str) -> str:
    """Page-marked plain text so postprocess can build the page sidecar and carry the warning."""
    parts = [f"<!-- extraction: {' '.join(note.split())} -->", ""]
    total = len(pages)
    for number, text in enumerate(pages, 1):
        parts.append(f"<!-- page {number} of {total} -->")
        parts.append("")
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.splitlines())).strip()
        if body:
            parts.append(body)
            parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def run_fallback(pdf: Path, out_dir: Path, extractor: str, reason: str, status_text: str, pdftotext: str | None, timeout: float) -> dict[str, Any]:
    if extractor not in FALLBACK_EXTRACTORS:
        raise ExtractError(f"--fallback must be one of {', '.join(FALLBACK_EXTRACTORS)}.")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (pdf.stem + ".md")
    if target.exists():
        raise ExtractError(f"Output already exists: {target}; choose a new --out-dir.")
    if extractor == "pdftotext":
        pages, command, stderr = pdftotext_pages(pdf, pdftotext, timeout)
    else:
        pages, command, stderr = pdfplumber_pages(pdf)
    if not any(page.strip() for page in pages):
        raise ExtractError(f"{extractor} found no text in {pdf.name} (scanned pages?); the fallback has no OCR, so MinerU is required.")
    note = LOW_FIDELITY_NOTE.format(extractor=extractor, reason=reason.rstrip("."))
    write_new(target, fallback_markdown(pages, note))
    record = {
        "ok": True,
        "extractor": extractor,
        "fidelity": "low",
        "fidelity_note": note,
        "mineru_unavailable": reason,
        "command": command,
        "exit_code": 0,
        "stdout": "",
        "stderr": stderr[-4000:],
        "server_status": status_text[-4000:],
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "pages": len(pages),
        "produced": [target.name],
        "markdown": str(target),
        "tier": None,
        "remote": False,
    }
    with (out_dir / "extract.json").open("w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return record


# ---------------------------------------------------------------- CLI


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ExtractError(f"Refusing to overwrite: {path}") from exc


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract", help="Run the local MinerU standard-tier parse on a PDF.")
    extract.add_argument("--pdf", required=True)
    extract.add_argument("--out-dir", required=True)
    extract.add_argument("--mineru", help="MinerU executable; default $MINERU, then `mineru` on PATH.")
    extract.add_argument("--pages", default="all", help="Page selection passed to MinerU (default all).")
    extract.add_argument("--timeout", type=float, default=3600.0, help="Seconds allowed per MinerU command.")
    extract.add_argument("--remote", action="store_true", help="Refused: unpublished manuscripts are parsed locally only.")
    extract.add_argument("--fallback", choices=FALLBACK_EXTRACTORS, help="When MinerU is missing or its parse server is not ready, write a text-only low-fidelity edition with this tool instead of stopping (default: stop).")
    extract.add_argument("--pdftotext", help="pdftotext executable for --fallback pdftotext (default: pdftotext on PATH).")
    post = commands.add_parser("postprocess", help="Clean MinerU Markdown; write edition, page sidecar, and notes.")
    post.add_argument("--input", required=True, help="MinerU Markdown file.")
    post.add_argument("--layout", help="MinerU middle.json or content_list.json for page numbers when the Markdown has no page markers.")
    post.add_argument("--out", required=True, help="Cleaned Markdown path (new file).")
    post.add_argument("--pages-out", help="Page sidecar path (default: <out>.pages.json).")
    post.add_argument("--notes-out", help="Extraction notes path (default: <out>.extraction-notes.txt).")
    case = commands.add_parser("case", help="Build a masked_reader case.json from an index and the page sidecar.")
    case.add_argument("--index", required=True)
    case.add_argument("--pages", required=True, help="Page sidecar from postprocess.")
    case.add_argument("--budget", required=True, type=int, help="Approximate words per window.")
    case.add_argument("--asset-policy", required=True, choices=["strict-prefix", "reference-jump"])
    case.add_argument("--assets", help="JSON list of {id: Figure1|Table2, page, path, caption}.")
    case.add_argument("--id", help="Case id (default: the index snapshot id).")
    case.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "extract":
            if args.remote:
                raise ExtractError("--remote is refused: parse unpublished manuscripts with the local MinerU server only.")
            record = run_extract(Path(args.pdf), Path(args.out_dir), mineru_executable(args.mineru), args.pages, args.timeout, args.fallback, args.pdftotext)
            response = {"ok": True, "command": "extract", "extractor": record["extractor"], "fidelity": record["fidelity"], "markdown": record["markdown"], "produced": record["produced"], "record": str(Path(args.out_dir) / "extract.json")}
            if record["fidelity"] == "low":
                response["warning"] = record["fidelity_note"]
        elif args.command == "postprocess":
            source = Path(args.input)
            with source.open("r", encoding="utf-8") as handle:
                markdown = handle.read()
            layout = load_json(Path(args.layout)) if args.layout else None
            result = postprocess(markdown, layout, source.name)
            out = Path(args.out)
            pages_out = Path(args.pages_out) if args.pages_out else out.with_name(out.name + ".pages.json")
            notes_out = Path(args.notes_out) if args.notes_out else out.with_name(out.name + ".extraction-notes.txt")
            write_new(out, result["markdown"])
            write_new(pages_out, json.dumps(result["sidecar"], ensure_ascii=False, indent=2) + "\n")
            write_new(notes_out, result["notes"])
            response = {"ok": True, "command": "postprocess", "output": str(out), "pages": str(pages_out), "notes": str(notes_out), "paragraphs": len(result["sidecar"]["paragraphs"]), "page_source": result["sidecar"]["page_source"], "dropped_headers": result["dropped"], "fidelity": result["fidelity"]}
        else:
            index = load_json(Path(args.index))
            sidecar = load_json(Path(args.pages))
            assets = load_json(Path(args.assets)) if args.assets else []
            if not isinstance(assets, list):
                raise ExtractError("--assets must be a JSON array.")
            built = build_case(index, sidecar, args.budget, args.asset_policy, assets, args.id or index.get("snapshot_id", "case"))
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import masked_reader  # noqa: E402  (same directory; validates the case shape)
            masked_reader.validate_case(built["case"])
            write_new(Path(args.out), json.dumps(built["case"], ensure_ascii=False, indent=2) + "\n")
            response = {"ok": True, "command": "case", "output": str(args.out), "blocks": len(built["case"]["blocks"]), "windows": len(built["case"]["windows"]), "assets": len(built["case"]["assets"]), **built["report"]}
        print(json.dumps(response, ensure_ascii=False))
        return 0
    except ExtractError as exc:
        if "\n" in str(exc):
            print(str(exc), file=sys.stderr)
        error = {"ok": False, "error": {"code": "extract_error", "message": str(exc)}}
    except (OSError, UnicodeError, json.JSONDecodeError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError) as exc:
        error = {"ok": False, "error": {"code": "input_output_error", "message": f"{type(exc).__name__}: {exc}"}}
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
