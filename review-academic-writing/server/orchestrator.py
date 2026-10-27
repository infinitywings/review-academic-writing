#!/usr/bin/env python3
"""Run the whole staged review on a server, with an OpenAI-compatible LLM as the editor.

Python standard library only. The three core helpers (review_packets, review_steps,
masked_reader) and dispatch_reader are imported as libraries and left unchanged; every
note they record is validated by them, never by this file. Order:

  index -> scope -> start -> reading (batched) -> checks (batched) -> record
  -> readers through dispatch_reader (prefix checkpoints from the plan, one masked run
     over the first windows, one branch)
  -> reconcile -> decisions -> repairs (every proposed wording audited; a failed audit is
     re-prompted once, then converted to an author question) -> ready
  -> review.json (check-review, check-final) -> report.md -> summary.json

The reconcile, decisions, and repairs prompts can only run after 02-checks.receipt.json
exists; the code asserts the file before dispatching any reader and before each of those
prompts. When a helper rejects a response, the same step is re-prompted with the helper's
exact message, at most three times; then the run stops with the journal intact and
prints what to fix. Before a response reaches the helper, top-level fields that a re-prompt
left out are carried from the previous attempt, and an anchor quote that differs from its
block only in typography or whitespace is replaced by the block's exact text; both are
logged. Every editor call keeps its prompt, raw response, parsed JSON, and
provenance under <out>/editor; reader and audit calls keep dispatch_reader's file sets
under <out>/readers and <out>/audits. The API key is read from the environment variable
the config names and is never written to any file.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Callable
import unicodedata

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parent.parent
SCRIPTS = PACKAGE / "scripts"
PROMPTS = PACKAGE / "server" / "prompts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch_reader as dr  # noqa: E402
import extract_pdf as ep  # noqa: E402
import llm  # noqa: E402
import masked_reader as mr  # noqa: E402
import review_packets as rp  # noqa: E402
import review_steps as rs  # noqa: E402
import schemas  # noqa: E402

STEPS = ("scope", "reading", "checks", "reconcile", "decisions", "repairs", "review", "report")
ATTEMPT_SUFFIXES = ("", "-b", "-c", "-d")  # one prompt plus at most three re-prompts
SECTION_INCLUDE = re.compile(r"\{\{section:([^|}]+)\|([^}]+)\}\}")
FILE_INCLUDE = re.compile(r"\{\{file:([^}]+)\}\}")
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
READER_PREFIX_MODE = "bounded-request"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")  # ids that may become file names
PREVIEW_CHARS = 200
TYPOGRAPHY = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
                            "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-"})
# Quoting a formula, models drop its markup ($S ( I )$ written S(I)) or garble its commands in JSON (\ldots left out,
# \therefore read as a tab), so quote matching ignores LaTeX markup: $ _ ^ { }, \commands, and a tab, backspace or form
# feed with the letters after it. Newlines stay whitespace: extracted blocks contain real ones.
LATEX_MARKUP = re.compile(r"[$_^{}]|\\[A-Za-z]+|[\x08\x09\x0c][A-Za-z]*")
EXECUTION_MODE = "bounded stateless chat-completions requests to an OpenAI-compatible endpoint; isolation by request content, not by sandbox"


class Rejected(Exception):
    """A response that the helper or the controller did not accept; re-prompted with this message."""


class StopRun(Exception):
    """The run cannot continue; the journal stays as it is."""

    def __init__(self, stage: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.fix = fix


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False)


# ---------------------------------------------------------------- templates


def section_of(document: str, heading: str) -> str:
    """The Markdown section that starts at `heading`, up to the next heading of the same or a higher level."""
    lines = document.split("\n")
    for number, line in enumerate(lines):
        match = HEADING.match(line)
        if match and line.strip() == heading.strip():
            level = len(match.group(1))
            end = len(lines)
            for later in range(number + 1, len(lines)):
                other = HEADING.match(lines[later])
                if other and len(other.group(1)) <= level:
                    end = later
                    break
            return "\n".join(lines[number:end]).rstrip("\n")
    raise KeyError(f"Heading not found: {heading}")


def render(step: str, values: dict[str, Any], package: Path = PACKAGE) -> str:
    """Fill a prompt template: section and file includes come verbatim from the package; {{input}} is JSON."""
    template = (PROMPTS / f"{step}.txt").read_text(encoding="utf-8")
    cache: dict[str, str] = {}

    def read(name: str) -> str:
        if name not in cache:
            cache[name] = (package / name).read_text(encoding="utf-8")
        return cache[name]

    text = SECTION_INCLUDE.sub(lambda m: section_of(read(m.group(1).strip()), m.group(2)), template)
    text = FILE_INCLUDE.sub(lambda m: read(m.group(1).strip()).rstrip("\n"), text)

    def fill(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Template {step}.txt needs a value for {{{{{key}}}}}")
        value = values[key]
        return dumps(value) if key == "input" else str(value)

    return PLACEHOLDER.sub(fill, text).rstrip("\n")


def with_correction(prompt: str, history: list[str]) -> str:
    """Insert the controller's correction note after the step line so the model sees it first."""
    first, rest = prompt.split("\n", 1)
    attempts = " ".join(f"Attempt {n}: {message}" for n, message in enumerate(history, 1))
    note = ("Correction note from the controller: the previous attempt(s) at this same step were not accepted. " + attempts
            + " Return the complete, corrected JSON object for this step, with no code fence and no commentary.")
    return first + "\n\n" + note + "\n" + rest


# ---------------------------------------------------------------- helpers


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Rejected(message)


def require_array(response: dict[str, Any], key: str) -> None:
    # Told only "must be an array", Flash-Next left the field out again on every re-prompt; naming the omission and
    # the empty answer got it filled in three replays of four.
    if key not in response:
        raise Rejected(f"{key} is missing; give it as an array, [] when there are none.")
    require(isinstance(response[key], list), f"{key} must be an array.")


def helper_message(exc: BaseException) -> str:
    return f"The helper rejected it: {exc}"


def anchor_offsets(block: dict[str, Any], quote: str) -> int:
    position = block["text"].find(quote)
    if position < 0:
        raise Rejected(f"checkpoint_plan anchor quote is not text of {block['block_id']}.")
    return block["start_offset"] + position + len(quote)


def dedupe_anchors(anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for anchor in anchors:
        key = rp._canonical_hash(anchor)
        if key not in seen:
            seen.add(key)
            result.append(anchor)
    return result


def fold(text: str) -> tuple[str, list[tuple[int, int]]]:
    """`text` with typographic quotes and dashes folded to ASCII, NFKC per character, and whitespace and LaTeX markup dropped;
    spans[i] is the (start, end) range in `text` of folded character i."""
    folded: list[str] = []
    spans: list[tuple[int, int]] = []

    def plain(start: int, end: int) -> None:
        for at in range(start, end):
            for piece in unicodedata.normalize("NFKC", text[at]).translate(TYPOGRAPHY):
                if not piece.isspace():
                    folded.append(piece)
                    spans.append((at, at + 1))

    offset = 0
    for match in LATEX_MARKUP.finditer(text):
        plain(offset, match.start())
        offset = match.end()
    plain(offset, len(text))
    return "".join(folded), spans


def block_span(text: str, quote: str) -> str | None:
    """The text of the one span of `text` that equals `quote` after folding both, widened over adjacent `$` and `}` so a
    formula stays whole; None when there is no such span or more than one."""
    target, _ = fold(quote)
    folded, spans = fold(text)
    start = folded.find(target)
    if not target or start < 0 or folded.find(target, start + 1) >= 0:
        return None
    begin, end = spans[start][0], spans[start + len(target) - 1][1]
    while begin > 0 and text[begin - 1] == "$":
        begin -= 1
    while end < len(text) and text[end] in "$}":
        end += 1
    return text[begin:end].strip()


def canonical_anchors(value: Any, blocks: dict[str, dict[str, Any]]) -> tuple[Any, list[dict[str, str]]]:
    """A copy of a parsed response in which every inexact anchor quote that names one span of its block is that span's exact text, and the rewrites made.

    An anchor is any object with string block_id and quote. Models write straight quotes for curly ones and repair
    extraction spacing ("34- layer"), while the helpers demand exact substrings. A quote that is already exact, or that
    folds to no span or to several, is left for the helper to judge.
    """
    rewrites: list[dict[str, str]] = []

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        copy = {key: walk(item) for key, item in node.items()}
        block_id, quote = node.get("block_id"), node.get("quote")
        if isinstance(block_id, str) and isinstance(quote, str) and block_id in blocks and quote not in blocks[block_id]["text"]:
            exact = block_span(blocks[block_id]["text"], quote)
            if exact is not None:
                copy["quote"] = exact
                rewrites.append({"block_id": block_id, "quote": quote, "replacement": exact})
        return copy

    return walk(value), rewrites


class Run:
    """One orchestrated review: directories, options, log, and the editor client."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out = Path(args.out).resolve()
        if self.out.exists():
            raise dr.DispatchError(f"--out {self.out} already exists; use a new run directory.")
        self.manuscript = Path(args.manuscript).resolve()
        self.config = Path(args.config).resolve()
        self.reader_config = Path(args.reader_config).resolve() if args.reader_config else self.config
        self.journal = self.out / "journal"
        self.readers = self.out / "readers"
        self.audits = self.out / "audits"
        self.masked = self.out / "masked"
        runtime = llm.EditorRuntime(self.config, None, None)  # validates the config and the key variable before anything is written
        self.out.mkdir(parents=True)
        self.log_path = self.out / "log.jsonl"
        self.started = time.monotonic()
        self.client = llm.Client(self.config, self.out / "editor", runtime=runtime)
        self.stages: dict[str, dict[str, Any]] = {}
        self.reader_calls: list[dict[str, Any]] = []
        self.audit_rows: list[dict[str, Any]] = []
        self.retries = 0
        self.anchors_canonicalized = 0
        self.index: dict[str, Any] = {}
        self.blocks: dict[str, dict[str, Any]] = {}
        self.profile: dict[str, Any] = {}
        self.included: list[str] = []
        self.notes: dict[str, Any] = {}
        self.case: dict[str, Any] | None = None
        self.branch_question: dict[str, Any] | None = None
        self.prefix_results: list[dict[str, Any]] = []
        self.failed_readers: list[dict[str, Any]] = []
        self.masked_summary: dict[str, Any] | None = None
        self.branch_summary: dict[str, Any] | None = None
        self.converted: list[str] = []

    # ------------------------------------------------------------ bookkeeping

    def log(self, event: str, **detail: Any) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": utc_now(), "event": event, **detail}, ensure_ascii=False) + "\n")

    def say(self, text: str) -> None:
        print(text, flush=True)

    def receipt_path(self, stage: str) -> Path:
        return rs.stage_paths(self.journal, stage)[1]

    def assert_checks_frozen(self, before: str) -> None:
        receipt = self.receipt_path("checks")
        if not receipt.is_file():
            raise StopRun(before, f"{receipt.name} does not exist; {before} must not run before the checks stage is recorded.", "Record the checks stage first.")
        rs.verify_receipt(rs.parse(rs.read_raw(receipt)), "checks receipt")

    def write_json(self, path: Path, value: Any) -> Path:
        rp.write_json_atomic(path, value)
        return path

    # ------------------------------------------------------------ editor calls with corrections

    def effective_response(self, step: str, call: llm.Call, previous: dict[str, Any] | None) -> Any:
        """The response the helpers judge, which also becomes call.parsed; <label>.response.json keeps the model's own.

        A re-prompted model may return only the top-level fields it changed, so the others are carried from the
        previous attempt's effective response (the newest value of each field wins). Then anchors are canonicalized.
        """
        response = call.parsed
        if isinstance(response, dict) and previous is not None:
            carried = [key for key in previous if key not in response]
            if carried:
                response = {**previous, **response}
                self.log("fields_carried", step=step, label=call.label, fields=carried)
        response, rewrites = canonical_anchors(response, self.blocks)
        for rewrite in rewrites:
            self.log("anchor_canonicalized", step=step, label=call.label, **rewrite)
        self.anchors_canonicalized += len(rewrites)
        call.parsed = response
        return response

    def ask(self, step: str, label: str, values: dict[str, Any], accept: Callable[[Any, llm.Call], Any], schema: dict[str, Any] | None = None) -> Any:
        history: list[str] = []
        previous: dict[str, Any] | None = None
        for number, suffix in enumerate(ATTEMPT_SUFFIXES):
            prompt = render(step, values)
            if history:
                prompt = with_correction(prompt, history)
            call = self.client.call(label + suffix, prompt, schema)
            if call.parsed is None:
                message = f"The response was not one JSON object ({call.parse_error})."
            else:
                response = self.effective_response(step, call, previous)
                if isinstance(response, dict):
                    previous = response
                try:
                    value = accept(response, call)
                    call.status = "ok"
                    self.log("editor_call", step=step, label=call.label, status="ok", attempt=number + 1)
                    return value
                except Rejected as exc:
                    message = str(exc)
            call.status = "rejected"
            self.log("editor_call", step=step, label=call.label, status="rejected", attempt=number + 1, message=message)
            history.append(message)
            if number < len(ATTEMPT_SUFFIXES) - 1:
                self.retries += 1
                self.say(f"  {call.label} not accepted; re-prompting with the message.")
        raise StopRun(step, f"{label}: {len(history)} attempts were not accepted. Last message: {history[-1]}",
                      f"Inspect {self.client.path(label + ATTEMPT_SUFFIXES[-1], 'response.raw')} and the prompt beside it; fix the prompt template, the model settings, or the manuscript edition, then start a new run.")

    # ------------------------------------------------------------ 1. index, scope, start

    def step_index(self) -> None:
        text = self.manuscript.read_text(encoding="utf-8")
        self.index = rp.build_index(text)
        self.blocks = {b["block_id"]: b for b in self.index["blocks"]}
        self.write_json(self.out / "index.json", self.index)
        profile_path = Path(self.args.profile) if self.args.profile else PACKAGE / "assets" / "reader-profile.json"
        self.profile = rp._profile(rp.load_json(profile_path))
        self.write_json(self.out / "reader-profile.json", self.profile)
        self.log("index", snapshot_id=self.index["snapshot_id"], blocks=len(self.index["blocks"]))
        self.say(f"index: {len(self.index['blocks'])} blocks, {self.index['snapshot_id']}")

    def extraction_notes(self) -> str:
        if self.args.extraction_notes:
            return Path(self.args.extraction_notes).read_text(encoding="utf-8").strip() or "The extraction notes file is empty."
        return "No extraction step: the manuscript was supplied as UTF-8 Markdown or plain text."

    def step_scope(self) -> None:
        blocks = [{"block_id": b["block_id"], "kind": b["kind"], "words": len(b["text"].split()),
                   "preview": b["text"][:PREVIEW_CHARS] + ("..." if len(b["text"]) > PREVIEW_CHARS else "")} for b in self.index["blocks"]]
        values = {"input": {"task": self.args.task, "snapshot_id": self.index["snapshot_id"], "reader_profile": self.profile,
                            "extraction_notes": self.extraction_notes(), "blocks": blocks}}

        def accept(parsed: Any, call: llm.Call) -> dict[str, Any]:
            require(isinstance(parsed, dict) and parsed.get("task") == self.args.task, f"scope.task must be {self.args.task!r}.")
            try:
                return rs.validate_scope(parsed, self.index)
            except rp.ReviewDataError as exc:
                raise Rejected(helper_message(exc)) from exc

        scope = self.ask("scope", "00-scope", values, accept, schemas.scope(self.args.task, list(self.blocks)))
        self.write_json(self.out / "scope.json", scope)
        try:
            rs.start_run(self.index, self.profile, scope, self.journal)
        except rp.ReviewDataError as exc:
            raise StopRun("scope", helper_message(exc), "The scope passed validation but start refused it; inspect scope.json.") from exc
        order = {b["block_id"]: n for n, b in enumerate(self.index["blocks"])}
        self.included = sorted(scope["included_block_ids"], key=order.get)
        self.stages["scope"] = {"status": "recorded", "included_blocks": len(self.included), "omitted_blocks": len(scope["omissions"])}
        self.log("stage_recorded", stage="scope", included=len(self.included))
        self.say(f"scope: {len(self.included)} included, {len(scope['omissions'])} omitted; journal started")

    # ------------------------------------------------------------ 2. reading and checks, batched

    def start_and_notes(self) -> tuple[dict[str, Any], dict[str, Any]]:
        start, notes, _ = rs.load_run(self.journal, self.index)
        return start, notes

    def block_rows(self, ids: list[str]) -> list[dict[str, Any]]:
        return [{"block_id": i, "kind": self.blocks[i]["kind"], "text": self.blocks[i]["text"]} for i in ids]

    def outline(self) -> list[dict[str, Any]]:
        return [{"block_id": i, "kind": self.blocks[i]["kind"], "preview": self.blocks[i]["text"][:80]} for i in self.included]

    def placeholder_paragraph(self, block_id: str) -> dict[str, Any]:
        return {"anchor": {"block_id": block_id, "quote": self.blocks[block_id]["text"][:20]}, "paraphrase": "pending batch", "role": "pending batch",
                "required_context": [], "new_information": [], "open_questions": []}

    def record(self, stage: str, notes: dict[str, Any], path: Path) -> None:
        raw = (json.dumps(notes, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        path.write_bytes(raw)
        rs.record_stage(self.journal, self.index, stage, raw)
        self.notes[stage] = notes
        self.log("stage_recorded", stage=stage, receipt=str(self.receipt_path(stage)))

    def step_reading(self) -> None:
        start, _ = self.start_and_notes()
        batches = chunks(self.included, self.args.batch_blocks)
        accepted: dict[str, dict[str, Any]] = {}
        summaries: list[str] = []
        for number, batch in enumerate(batches, 1):
            values = {"batch": number, "batches": len(batches), "block_range": f"{batch[0]} to {batch[-1]}",
                      "input": {"snapshot_id": self.index["snapshot_id"], "reader_profile": self.profile, "batch_block_ids": batch,
                                "blocks": self.block_rows(batch), "outline_of_all_included_blocks": self.outline()}}

            def accept(parsed: Any, call: llm.Call, batch: list[str] = batch) -> list[dict[str, Any]]:
                require(isinstance(parsed, dict) and isinstance(parsed.get("paragraphs"), list), "The response must be an object with a paragraphs array.")
                require(isinstance(parsed.get("summary"), str) and parsed["summary"].strip(), "summary must be a nonempty string.")
                rows = {}
                for row in parsed["paragraphs"]:
                    block_id = row.get("anchor", {}).get("block_id") if isinstance(row, dict) and isinstance(row.get("anchor"), dict) else None
                    require(isinstance(block_id, str), "Each paragraph needs anchor.block_id.")
                    rows[block_id] = row
                provisional = {"snapshot_id": self.index["snapshot_id"], "summary": "provisional",
                               "paragraphs": [accepted.get(i) or rows.get(i) or self.placeholder_paragraph(i) for i in self.included if i not in batch]
                               + list(parsed["paragraphs"])}
                try:
                    rs.validate_notes("reading", provisional, start, self.index, {})
                except rp.ReviewDataError as exc:
                    raise Rejected(helper_message(exc)) from exc
                require(set(rows) == set(batch), f"This batch must cover exactly blocks {', '.join(batch)}.")
                return [rows[i] for i in batch]

            label = "01-reading" + (f"-batch{number:02d}" if len(batches) > 1 else "")
            for row in self.ask("reading", label, values, accept, schemas.reading(batch)):
                accepted[row["anchor"]["block_id"]] = row
            summaries.append(call_summary(self.client.calls[-1]))
            self.say(f"reading: batch {number}/{len(batches)} accepted")
        notes = {"snapshot_id": self.index["snapshot_id"], "summary": " ".join(summaries), "paragraphs": [accepted[i] for i in self.included]}
        try:
            self.record("reading", notes, self.out / "reading.json")
        except rp.ReviewDataError as exc:
            raise StopRun("reading", helper_message(exc), "Each batch passed validation but the merged notes did not; inspect reading.json.") from exc
        self.stages["reading"] = {"status": "recorded", "batches": len(batches)}
        self.say("reading: recorded")

    def masked_case(self) -> dict[str, Any] | None:
        if self.args.masked_windows <= 0:
            return None
        blocks = [{"id": i, "text": self.blocks[i]["text"], "page": None, "kind": self.blocks[i]["kind"]} for i in self.included]
        windows = ep.build_windows(blocks, self.args.masked_budget)[:self.args.masked_windows]
        ids = [i for window in windows for i in window]
        return {"id": self.index["snapshot_id"], "asset_policy": "strict-prefix", "blocks": [b for b in blocks if b["id"] in ids], "windows": windows, "assets": []}

    def step_checks(self) -> None:
        start, notes = self.start_and_notes()
        paraphrases = [{"block_id": p["anchor"]["block_id"], "paraphrase": p["paraphrase"]} for p in notes["reading"]["paragraphs"]]
        self.case = self.masked_case()
        masked_ids = [i for window in self.case["windows"] for i in window] if self.case else []
        batches = chunks(self.included, self.args.batch_blocks)
        merged: dict[str, Any] = {"concepts": [], "passages": {}, "checkpoint_plan": [], "candidates": [], "reasons": []}
        summaries: list[str] = []

        def placeholder_passage(block_id: str) -> dict[str, Any]:
            return {"block_id": block_id, "structure_check": "pending batch", "wording_check": "pending batch", "evidence": [{"block_id": block_id, "quote": self.blocks[block_id]["text"][:20]}]}

        for number, batch in enumerate(batches, 1):
            remaining = self.args.prefix_checkpoints - len(merged["checkpoint_plan"])
            values = {"batch": number, "batches": len(batches), "block_range": f"{batch[0]} to {batch[-1]}",
                      "input": {"snapshot_id": self.index["snapshot_id"], "reader_profile": self.profile, "batch_block_ids": batch,
                                "blocks": self.block_rows(batch), "paraphrases_of_all_included_blocks": paraphrases,
                                "already_recorded": {"concepts": [{"concept_id": c["concept_id"], "term": c["term"]} for c in merged["concepts"]],
                                                     "checkpoint_plan": merged["checkpoint_plan"], "candidates": [{"id": c["id"], "anchor": c["anchor"], "question": c["question"]} for c in merged["candidates"]]},
                                "max_prefix_checkpoints": self.args.prefix_checkpoints, "remaining_prefix_checkpoints": remaining,
                                "masked_run_first_windows": masked_ids, "branch_question_taken": self.branch_question is not None}}

            def accept(parsed: Any, call: llm.Call, batch: list[str] = batch, remaining: int = remaining) -> dict[str, Any]:
                require(isinstance(parsed, dict), "The response must be one JSON object.")
                for key in ("concepts", "passages", "checkpoint_plan", "candidates"):
                    require_array(parsed, key)
                require(isinstance(parsed.get("summary"), str) and parsed["summary"].strip(), "summary must be a nonempty string.")
                require(len(parsed["checkpoint_plan"]) <= remaining, f"checkpoint_plan may hold at most {remaining} more entries in this batch.")
                for row in parsed["checkpoint_plan"]:
                    require(isinstance(row, dict) and row.get("mode") == READER_PREFIX_MODE, f"Every checkpoint_plan mode must be {READER_PREFIX_MODE!r} on this server.")
                concepts, plan, candidates = [], [], []
                for n, row in enumerate(parsed["concepts"]):
                    require(isinstance(row, dict), "Each concept must be an object.")
                    concepts.append({**row, "concept_id": f"K{len(merged['concepts']) + n + 1:02d}"})
                for n, row in enumerate(parsed["checkpoint_plan"]):
                    plan.append({**row, "id": f"P{len(merged['checkpoint_plan']) + n + 1:02d}"})
                for n, row in enumerate(parsed["candidates"]):
                    require(isinstance(row, dict), "Each candidate must be an object.")
                    candidates.append({**row, "id": f"C{len(merged['candidates']) + n + 1:02d}"})
                passages = {}
                for row in parsed["passages"]:
                    require(isinstance(row, dict) and isinstance(row.get("block_id"), str), "Each passage needs block_id.")
                    passages[row["block_id"]] = row
                all_concepts = merged["concepts"] + concepts
                provisional = {"snapshot_id": self.index["snapshot_id"], "summary": "provisional", "concepts": all_concepts,
                               "passages": [merged["passages"].get(i) or passages.get(i) or placeholder_passage(i) for i in self.included if i not in batch] + list(parsed["passages"]),
                               "checkpoint_plan": merged["checkpoint_plan"] + plan, "candidates": merged["candidates"] + candidates}
                if not all_concepts:
                    reason = parsed.get("no_concepts_reason")
                    provisional["no_concepts_reason"] = reason if isinstance(reason, str) and reason.strip() else "No concept record was relevant in this batch."
                for key in rs.READER_EVIDENCE_KEYS:
                    if key in parsed:
                        provisional[key] = parsed[key]
                try:
                    rs.validate_notes("checks", provisional, start, self.index, {})
                except rp.ReviewDataError as exc:
                    raise Rejected(helper_message(exc)) from exc
                require(set(passages) == set(batch), f"This batch must check exactly blocks {', '.join(batch)}.")
                question = parsed.get("branch_question")
                if question is not None:
                    require(isinstance(question, dict) and all(isinstance(question.get(k), str) and question[k].strip() for k in ("question", "reason", "after_block_id")),
                            "branch_question must be null or an object with nonempty question, reason, and after_block_id.")
                return {"concepts": concepts, "plan": plan, "candidates": candidates, "passages": [passages[i] for i in batch],
                        "reason": provisional.get("no_concepts_reason"), "branch_question": question}

            label = "02-checks" + (f"-batch{number:02d}" if len(batches) > 1 else "")
            result = self.ask("checks", label, values, accept, schemas.checks(batch, self.included, list(self.blocks), remaining))
            merged["concepts"] += result["concepts"]
            merged["checkpoint_plan"] += result["plan"]
            merged["candidates"] += result["candidates"]
            for row in result["passages"]:
                merged["passages"][row["block_id"]] = row
            if result["reason"]:
                merged["reasons"].append(result["reason"])
            if result["branch_question"] and self.branch_question is None and masked_ids:
                self.branch_question = result["branch_question"]
            summaries.append(call_summary(self.client.calls[-1]))
            self.say(f"checks: batch {number}/{len(batches)} accepted")
        checks = {"snapshot_id": self.index["snapshot_id"], "summary": " ".join(summaries), "concepts": merged["concepts"]}
        if not merged["concepts"]:
            checks["no_concepts_reason"] = " ".join(merged["reasons"]) or "No concept record was relevant."
        checks.update({"passages": [merged["passages"][i] for i in self.included], "checkpoint_plan": merged["checkpoint_plan"], "candidates": merged["candidates"]})
        try:
            self.record("checks", checks, self.out / "checks.json")
        except rp.ReviewDataError as exc:
            raise StopRun("checks", helper_message(exc), "Each batch passed validation but the merged notes did not; inspect checks.json.") from exc
        if self.branch_question:
            self.write_json(self.out / "branch-question.json", {"id": "Q01", "question": self.branch_question["question"], "selected_with_full_text": True, "reason": self.branch_question["reason"]})
        self.stages["checks"] = {"status": "recorded", "batches": len(batches), "concepts": len(merged["concepts"]), "candidates": len(merged["candidates"]),
                                 "checkpoint_plan": len(merged["checkpoint_plan"]), "branch_question": self.branch_question is not None}
        self.say(f"checks: recorded ({len(merged['candidates'])} candidates, {len(merged['checkpoint_plan'])} planned checkpoints)")

    # ------------------------------------------------------------ 3. readers through dispatch_reader

    def dispatch(self, kind: str, argv: list[str]) -> dict[str, Any]:
        """Run one dispatch_reader subcommand in-process with the reader config; keep its summary."""
        full = [kind, "--runtime", "http", "--config", str(self.reader_config), *argv]
        self.log("reader_dispatch", kind=kind, argv=full)
        args = dr._parser().parse_args(full)
        events = dr.Events(Path(args.events), dr.PROMPTS_DIR)
        handler = {"prefix": dr.run_prefix, "masked": dr.run_masked, "branch": dr.run_branch, "audit": dr.run_audit}[kind]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                summary = handler(args, events, dr.build_runtime(args))
        except (dr.DispatchError, mr.Invalid, rp.ReviewDataError, OSError, ValueError, KeyError, TypeError) as exc:
            summary = events.finish(kind, f"{type(exc).__name__}: {exc}", False)
        for row in summary["calls"]:
            self.reader_calls.append({"kind": kind, "events_dir": summary["events_dir"], **row})
        self.log("reader_done", kind=kind, ok=summary["ok"], outcome=summary["outcome"], calls=summary["totals"]["calls"])
        return summary

    def step_readers(self) -> None:
        self.assert_checks_frozen("reader dispatch")
        self.log("checks_frozen", receipt=str(self.receipt_path("checks")))
        plan = self.notes["checks"]["checkpoint_plan"]
        for entry in plan:
            block = self.blocks[entry["anchor"]["block_id"]]
            stop = anchor_offsets(block, entry["anchor"]["quote"])
            packet = rp.build_packet(self.index, self.profile, stop_offset=stop)
            events = self.readers / entry["id"]
            packet_path = self.write_json(events / f"{entry['id']}.packet.json", packet)
            summary = self.dispatch("prefix", ["--events", str(events), "--packet", str(packet_path), "--label", entry["id"]])
            if summary["ok"]:
                label = summary["label"]
                self.prefix_results.append({"plan": entry, "packet_path": str(packet_path), "observation_path": str(events / f"{label}.observation.json"),
                                            "observation": rp.load_json(events / f"{label}.observation.json"), "label": label})
                self.say(f"reader {entry['id']}: {summary['outcome']}")
            else:
                self.failed_readers.append({"id": entry["id"], "kind": "prefix", "outcome": summary["outcome"]})
                self.say(f"reader {entry['id']} failed: {summary['outcome']}")
        if self.case:
            case_path = self.write_json(self.masked / "case.json", self.case)
            profile_path = Path(self.args.masked_profile) if self.args.masked_profile else PACKAGE / "assets" / "masked-reader-profile.json"
            masked_profile = self.masked / "profile.json"
            self.masked.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(profile_path, masked_profile)
            summary = self.dispatch("masked", ["--events", str(self.masked / "events"), "--run", str(self.masked / "reader"), "--case", str(case_path), "--profile", str(masked_profile), "--max-events", str(self.args.max_events)])
            self.masked_summary = summary
            self.say(f"masked run: {summary['outcome']}")
            if not summary["ok"]:
                self.failed_readers.append({"id": "masked", "kind": "masked", "outcome": summary["outcome"]})
            if self.branch_question and (self.masked / "reader" / "records").is_dir() and any((self.masked / "reader" / "records").iterdir()):
                sequence = self.branch_sequence(self.branch_question["after_block_id"])
                summary = self.dispatch("branch", ["--events", str(self.masked / "branch-events"), "--run", str(self.masked / "reader"), "--from-sequence", str(sequence), "--question", str(self.out / "branch-question.json")])
                self.branch_summary = summary
                self.say(f"branch: {summary['outcome']}")
                if not summary["ok"]:
                    self.failed_readers.append({"id": "branch", "kind": "branch", "outcome": summary["outcome"]})
        self.stages["readers"] = {"status": "done", "prefix_ok": len(self.prefix_results), "failed": [f["id"] for f in self.failed_readers],
                                  "masked": bool(self.masked_summary and self.masked_summary["ok"]), "branch": bool(self.branch_summary and self.branch_summary["ok"])}

    def masked_events(self) -> list[dict[str, Any]]:
        run = self.masked / "reader"
        _run, _manifest, _case, _profile, state, _next, _previous = mr.load_run(run)
        rows = []
        for event in state["events"]:
            seq = event["sequence"]
            response = json.loads((run / "records" / f"{seq:06d}" / "response.json").read_text(encoding="utf-8"))
            rows.append({"sequence": seq, "mode": event["packet"]["mode"], "displayed_block_ids": [m["id"] for m in event["packet"]["materials"]],
                         "seen_block_ids_by_this_event": sorted(event["exposed"]), "response": {k: response.get(k) for k in ("understanding", "understood", "uncertain", "resolved", "next_action", "carry_memory")}})
        return rows

    def branch_sequence(self, after_block_id: str) -> int:
        events = self.masked_events()
        for row in events:
            if row["mode"] == "window" and after_block_id in row["displayed_block_ids"]:
                return row["sequence"]
        return events[-1]["sequence"]

    # ------------------------------------------------------------ 4. reconcile, decisions, repairs

    def step_reconcile(self) -> None:
        self.assert_checks_frozen("reconcile")
        start, notes = self.start_and_notes()
        checks = notes["checks"]
        prefix_rows = []
        for n, result in enumerate(self.prefix_results, 1):
            plan = result["plan"]
            prefix_rows.append({"id": f"K{n:02d}", "plan_id": plan["id"], "anchor": plan["anchor"], "mode": plan["mode"], "figure_policy": "text-only",
                                "packet_path": result["packet_path"], "observation_path": result["observation_path"], "observation": result["observation"]})
        masked = None
        if self.masked_summary and (self.masked / "reader" / "records").is_dir():
            try:
                masked = {"run_path": str(self.masked / "reader"), "asset_policy": self.case["asset_policy"] if self.case else None,
                          "complete": bool(self.masked_summary["ok"]), "outcome": self.masked_summary["outcome"], "events": self.masked_events()}
            except (mr.Invalid, KeyError, TypeError, OSError) as exc:
                self.failed_readers.append({"id": "masked", "kind": "masked", "outcome": f"run does not replay: {exc}"})
        branch = None
        if self.branch_summary and self.branch_summary["ok"]:
            record = self.masked / "reader" / "branches" / f"{self.branch_summary['branch_sequence']:06d}"
            branch = {"id": "R01", "run_path": str(self.masked / "reader"), "branch_sequence": self.branch_summary["branch_sequence"],
                      "question": json.loads((self.out / "branch-question.json").read_text(encoding="utf-8")),
                      "packet": {k: v for k, v in json.loads((record / "packet.json").read_text(encoding="utf-8")).items() if k in ("branched_from_sequence", "materials", "carry_memory")},
                      "answer": json.loads((record / "response.json").read_text(encoding="utf-8"))}
        next_candidate = len(checks["candidates"]) + 1
        values = {"input": {"snapshot_id": self.index["snapshot_id"], "execution_mode": EXECUTION_MODE, "reader_endpoint": llm.load_config(self.reader_config).get("base_url"),
                            "checks_notes": checks, "blocks": self.block_rows(self.included), "prefix_checkpoints": prefix_rows,
                            "failed_reader_calls": self.failed_readers, "masked_run": masked, "branch": branch, "next_candidate_id": f"C{next_candidate:02d}"}}

        def accept(parsed: Any, call: llm.Call) -> dict[str, Any]:
            require(isinstance(parsed, dict), "The response must be one JSON object.")
            for key in ("checkpoints", "branches", "observations", "candidates"):
                require_array(parsed, key)
            require(isinstance(parsed.get("summary"), str) and parsed["summary"].strip(), "summary must be a nonempty string.")
            returned = {}
            for row in parsed["checkpoints"]:
                require(isinstance(row, dict) and isinstance(row.get("id"), str), "Each checkpoint needs an id.")
                returned[row["id"]] = row
            checkpoints = []
            for bound in prefix_rows:
                row = returned.pop(bound["id"], None)
                require(row is not None, f"checkpoints must include {bound['id']} with result and limitation.")
                checkpoints.append({"id": bound["id"], "plan_id": bound["plan_id"], "anchor": bound["anchor"], "mode": bound["mode"], "result": row.get("result"),
                                    "limitation": row.get("limitation"), "packet_path": bound["packet_path"], "observation_path": bound["observation_path"], "figure_policy": "text-only"})
            for row in returned.values():
                require(masked is not None, f"checkpoint {row['id']} is neither a listed prefix checkpoint nor a masked event (no masked run exists).")
                checkpoints.append({"id": row["id"], "plan_id": None, "anchor": row.get("anchor"), "mode": "masked-sequential", "result": row.get("result"), "limitation": row.get("limitation"),
                                    "run_path": masked["run_path"], "record_sequence": row.get("record_sequence"), "figure_policy": masked["asset_policy"]})
            branches = []
            for row in parsed["branches"]:
                require(isinstance(row, dict) and branch is not None and row.get("id") == branch["id"], "branches may only name the listed branch id.")
                branches.append({"id": branch["id"], "run_path": branch["run_path"], "branch_sequence": branch["branch_sequence"], "result": row.get("result"), "limitation": row.get("limitation")})
            for row in parsed["candidates"]:
                require(isinstance(row, dict) and isinstance(row.get("id"), str) and SAFE_ID.fullmatch(row["id"]) is not None,
                        "Each new candidate id must be letters, digits, '_' or '-' only (for example C07); it becomes a file name later.")
            reconcile = {"snapshot_id": self.index["snapshot_id"], "summary": parsed["summary"], "checkpoints": checkpoints}
            if not checkpoints:
                reason = parsed.get("no_checkpoints_reason")
                reconcile["no_checkpoints_reason"] = reason if isinstance(reason, str) and reason.strip() else "No reader checkpoint completed on this run."
            reconcile.update({"branches": branches, "observations": parsed["observations"], "candidates": parsed["candidates"]})
            try:
                self.record("reconcile", reconcile, self.out / "reconcile.json")
            except rp.ReviewDataError as exc:
                raise Rejected(helper_message(exc)) from exc
            return reconcile

        reconcile = self.ask("reconcile", "03-reconcile", values, accept, schemas.reconcile(self.included, list(self.blocks), [branch["id"]] if branch else []))
        self.stages["reconcile"] = {"status": "recorded", "checkpoints": len(reconcile["checkpoints"]), "branches": len(reconcile["branches"]),
                                    "observations": len(reconcile["observations"]), "candidates_added": len(reconcile["candidates"])}
        self.say(f"reconcile: recorded ({len(reconcile['observations'])} observations, {len(reconcile['candidates'])} candidates added)")

    def step_decisions(self) -> None:
        self.assert_checks_frozen("decisions")
        _start, notes = self.start_and_notes()
        candidates = [{**c, "stage": "checks"} for c in notes["checks"]["candidates"]] + [{**c, "stage": "reconcile"} for c in notes["reconcile"]["candidates"]]
        values = {"input": {"snapshot_id": self.index["snapshot_id"], "candidates": candidates, "observations": notes["reconcile"]["observations"],
                            "paraphrases": [{"block_id": p["anchor"]["block_id"], "paraphrase": p["paraphrase"]} for p in notes["reading"]["paragraphs"]],
                            "blocks": self.block_rows(self.included)}}

        def accept(parsed: Any, call: llm.Call) -> dict[str, Any]:
            require(isinstance(parsed, dict) and isinstance(parsed.get("decisions"), list), "The response must be an object with a decisions array.")
            require(isinstance(parsed.get("summary"), str) and parsed["summary"].strip(), "summary must be a nonempty string.")
            decisions = {"snapshot_id": self.index["snapshot_id"], "summary": parsed["summary"], "decisions": parsed["decisions"]}
            try:
                self.record("decisions", decisions, self.out / "decisions.json")
            except rp.ReviewDataError as exc:
                raise Rejected(helper_message(exc)) from exc
            return decisions

        decisions = self.ask("decisions", "04-decisions", values, accept, schemas.decisions([c["id"] for c in candidates], list(self.blocks)))
        counts = {status: sum(1 for d in decisions["decisions"] if d["status"] == status) for status in ("accepted", "needs_author", "rejected", "merged")}
        self.stages["decisions"] = {"status": "recorded", **counts}
        self.say("decisions: recorded (" + ", ".join(f"{v} {k}" for k, v in counts.items()) + ")")

    def retained(self) -> list[dict[str, Any]]:
        _start, notes = self.start_and_notes()
        candidates = rs.all_candidates(notes)
        rows = []
        for decision in notes["decisions"]["decisions"]:
            if decision["status"] in rs.FINAL_STATUSES:
                rows.append({"decision": decision, "candidate": candidates[decision["candidate_id"]]})
        return rows

    def validate_repairs(self, parsed: Any, wanted: list[str], start: dict[str, Any], notes: dict[str, Any], others: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        """Check a repairs response for the candidates in `wanted`; `others` holds already accepted rows for the rest."""
        require(isinstance(parsed, dict) and isinstance(parsed.get("repairs"), list), "The response must be an object with a repairs array.")
        require(isinstance(parsed.get("summary"), str) and parsed["summary"].strip(), "summary must be a nonempty string.")
        rows: dict[str, dict[str, Any]] = {}
        for row in parsed["repairs"]:
            require(isinstance(row, dict) and isinstance(row.get("candidate_id"), str), "Each repair needs candidate_id.")
            rows[row["candidate_id"]] = row
        require(set(rows) == set(wanted), f"repairs must cover exactly {', '.join(wanted)}.")
        decisions = rs.by_id(notes["decisions"]["decisions"], "candidate_id", "decisions")
        provisional = [{k: v for k, v in row.items() if k not in ("original", "basis")} | {"proposed_wording": None, "audit_path": None} for key, row in (others or {}).items() if key not in wanted]
        for key in wanted:
            row = rows[key]
            wording = row.get("proposed_wording")
            if decisions[key]["status"] == "needs_author":
                require(row.get("kind") == "author_question" and wording is None, f"Repair {key}: needs_author requires an author question, not a definitive replacement.")
            if wording is not None:
                require(isinstance(wording, str) and wording.strip(), f"Repair {key}: proposed_wording must be null or a nonempty string.")
                original, basis = row.get("original"), row.get("basis")
                require(isinstance(original, list) and original, f"Repair {key}: original must quote the exact source text the wording replaces.")
                require(isinstance(basis, list), f"Repair {key}: basis must be an array of facts with locations.")
                affected = row.get("affected_block_ids")
                require(isinstance(affected, list), f"Repair {key}: affected_block_ids must be an array.")
                for n, anchor in enumerate(original):
                    try:
                        rp._anchor(anchor, self.blocks, f"repair {key} original[{n}]")
                    except rp.ReviewDataError as exc:
                        raise Rejected(helper_message(exc)) from exc
                    require(anchor["block_id"] in affected, f"Repair {key}: original[{n}] quotes a block outside affected_block_ids.")
            provisional.append({k: v for k, v in row.items() if k not in ("original", "basis")} | {"proposed_wording": None, "audit_path": None})
        try:
            rs.validate_notes("repairs", {"snapshot_id": self.index["snapshot_id"], "summary": parsed["summary"], "repairs": provisional}, start, self.index, notes)
        except rp.ReviewDataError as exc:
            raise Rejected(helper_message(exc)) from exc
        return rows

    def audit(self, key: str, row: dict[str, Any], suffix: str) -> dict[str, Any]:
        """Run the independent fidelity audit for one wording; returns verdict, changed properties, notes, path."""
        label = key + suffix
        if SAFE_ID.fullmatch(label) is None or not (self.audits / label).resolve().is_relative_to(self.out):
            raise StopRun("repairs", f"Candidate id {key!r} cannot be used as an audit file name.", "Inspect the recorded candidate ids; they must be letters, digits, '_' or '-' only.")
        request = {"candidate_id": key, "original": row["original"], "proposed_wording": row["proposed_wording"], "basis": row["basis"]}
        input_path = self.write_json(self.audits / f"{label}.input.json", request)
        out_path = self.audits / f"{label}.audit.json"
        summary = self.dispatch("audit", ["--events", str(self.audits / label), "--input", str(input_path), "--out", str(out_path), "--label", label])
        result = {"candidate_id": key, "label": label, "proposed_wording": row["proposed_wording"], "audit_path": str(out_path), "verdict": None, "changed": [], "notes": summary["outcome"], "accepted": False}
        if summary["ok"] and out_path.is_file():
            audit = rp.load_json(out_path)
            result.update(verdict=audit["verdict"], changed=[k for k in rs.AUDIT_CHECKS if audit["checks"][k] == "changed"], notes=audit["notes"])
            result["accepted"] = audit["verdict"] == "preserved" and not result["changed"]
        self.audit_rows.append(result)
        self.log("audit", candidate_id=key, label=label, verdict=result["verdict"], changed=result["changed"], accepted=result["accepted"])
        self.say(f"audit {label}: verdict {result['verdict']}" + (f" (changed: {', '.join(result['changed'])})" if result["changed"] else ""))
        return result

    def step_repairs(self) -> None:
        self.assert_checks_frozen("repairs")
        start, notes = self.start_and_notes()
        retained = self.retained()
        wanted = [r["decision"]["candidate_id"] for r in retained]
        base_input = {"snapshot_id": self.index["snapshot_id"], "retained": retained, "blocks": self.block_rows(self.included)}
        rows = self.ask("repairs", "05-repairs", {"input": base_input}, lambda parsed, call: self.validate_repairs(parsed, wanted, start, notes),
                        schemas.repairs(wanted, list(self.blocks)))
        summary = self.client.calls[-1].parsed["summary"]
        audits: dict[str, dict[str, Any]] = {}
        for key in wanted:
            if rows[key].get("proposed_wording") is not None:
                audits[key] = self.audit(key, rows[key], "")
        failed = [key for key, result in audits.items() if not result["accepted"]]
        if failed:
            feedback = [{"candidate_id": key, "audited_wording": audits[key]["proposed_wording"], "verdict": audits[key]["verdict"], "changed": audits[key]["changed"], "auditor_notes": audits[key]["notes"]} for key in failed]
            retry_input = {**base_input, "retained": [r for r in retained if r["decision"]["candidate_id"] in failed], "audit_feedback": feedback}
            revised = self.ask("repairs", "05-repairs-audit", {"input": retry_input}, lambda parsed, call: self.validate_repairs(parsed, failed, start, notes, rows),
                               schemas.repairs(failed, list(self.blocks)))
            for key in failed:
                rows[key] = revised[key]
                if rows[key].get("proposed_wording") is not None:
                    audits[key] = self.audit(key, rows[key], "-2")
                else:
                    audits.pop(key)
            for key in failed:
                if key in audits and not audits[key]["accepted"]:
                    result = audits.pop(key)
                    row = rows[key]
                    question = ("An independent fidelity audit found that the proposed replacement would change its scientific meaning"
                                + (f" ({', '.join(result['changed'])})" if result["changed"] else "") + f": {result['notes']} "
                                + f"Audited wording: {result['proposed_wording']!r}. Which wording keeps the intended meaning: the original text, or a revision that preserves these properties?")
                    rows[key] = {**row, "kind": "author_question", "proposed_wording": None, "original": [], "basis": [], "author_question": question}
                    self.converted.append(key)
                    self.log("audit_converted", candidate_id=key)
                    self.say(f"repair {key}: audit still not preserved; converted to an author question")
        repairs = []
        for key in wanted:
            row = {k: v for k, v in rows[key].items() if k not in ("original", "basis")}
            row["audit_path"] = audits[key]["audit_path"] if key in audits else None
            repairs.append(row)
        try:
            self.record("repairs", {"snapshot_id": self.index["snapshot_id"], "summary": summary, "repairs": repairs}, self.out / "repairs.json")
        except rp.ReviewDataError as exc:
            raise StopRun("repairs", helper_message(exc), "The repairs passed the controller's checks but the helper refused the merged notes; inspect repairs.json and the audits directory.") from exc
        self.stages["repairs"] = {"status": "recorded", "repairs": len(repairs), "with_wording": sum(1 for r in repairs if r["proposed_wording"] is not None),
                                  "author_questions": sum(1 for r in repairs if r["kind"] == "author_question"), "audits": len(self.audit_rows), "converted_by_audit": list(self.converted)}
        self.say(f"repairs: recorded ({self.stages['repairs']['with_wording']} with wording, {len(self.converted)} converted by audit)")

    # ------------------------------------------------------------ 5. ready, review, report

    def step_ready(self) -> None:
        try:
            rs.ready_run(self.journal, self.index)
        except rp.ReviewDataError as exc:
            raise StopRun("ready", helper_message(exc), "Inspect the journal; a recorded stage no longer validates.") from exc
        self.stages["ready"] = {"status": "ok"}
        self.log("ready", receipt=str(self.journal / rs.READY))
        self.say("ready: receipt written")

    def step_review(self) -> None:
        _start, notes = self.start_and_notes()
        candidates = rs.all_candidates(notes)
        decisions = rs.by_id(notes["decisions"]["decisions"], "candidate_id", "decisions")
        repairs = rs.by_id(notes["repairs"]["repairs"], "candidate_id", "repairs")
        retained = [{"candidate_id": key, "anchor": candidates[key]["anchor"], "question": candidates[key]["question"], **{k: decisions[key][k] for k in ("status", "category", "diagnosis", "severity", "confidence")},
                     **{k: repairs[key][k] for k in ("kind", "action", "proposed_wording", "author_question")}} for key in repairs]
        values = {"input": {"snapshot_id": self.index["snapshot_id"], "scope": rp.load_json(self.out / "scope.json"), "execution_mode": EXECUTION_MODE,
                            "readers": self.stages.get("readers"), "retained_candidates": retained, "rejected_or_merged": [d["candidate_id"] for d in notes["decisions"]["decisions"] if d["status"] not in rs.FINAL_STATUSES]}}

        def accept(parsed: Any, call: llm.Call) -> dict[str, Any]:
            require(isinstance(parsed, dict) and isinstance(parsed.get("issue_order"), list), "The response must be an object with issue_order.")
            require(isinstance(parsed.get("summary"), str) and parsed["summary"].strip(), "summary must be a nonempty string.")
            require(sorted(parsed["issue_order"]) == sorted(repairs) and len(parsed["issue_order"]) == len(repairs), f"issue_order must list exactly {', '.join(repairs)} once each.")
            issues = []
            for n, key in enumerate(parsed["issue_order"], 1):
                decision, repair, candidate = decisions[key], repairs[key], candidates[key]
                issue = {"id": f"W{n:02d}", "candidate_id": key, "decision_status": decision["status"], "category": decision["category"], "anchor": candidate["anchor"],
                         "diagnosis": decision["diagnosis"], "action": repair["action"], "proposed_wording": repair["proposed_wording"], "scientific_fidelity": repair["scientific_fidelity"],
                         "severity": decision["severity"], "confidence": decision["confidence"], "evidence": dedupe_anchors(decision["evidence"] + candidate["evidence"]),
                         "initial_reader_question": candidate["question"], "affected_locations": repair["affected_block_ids"]}
                if repair["author_question"] is not None:
                    issue["author_question"] = repair["author_question"]
                issues.append(issue)
            review = {"snapshot_id": self.index["snapshot_id"], "summary": parsed["summary"], "issues": issues, "concept_ledger": notes["checks"]["concepts"],
                      "execution_mode": EXECUTION_MODE, "coverage": {"included_block_ids": self.included, "prefix_checkpoints": len(self.prefix_results), "masked_run": bool(self.masked_summary and self.masked_summary["ok"])}}
            try:
                rp.check_review(self.index, review)
                rs.check_final(self.journal, self.index, review)
            except rp.ReviewDataError as exc:
                raise Rejected(helper_message(exc)) from exc
            return review

        review = self.ask("review", "06-review", values, accept, schemas.review(list(repairs)))
        self.write_json(self.out / "review.json", review)
        self.stages["review"] = {"status": "checked", "issues": len(review["issues"])}
        self.log("review_checked", issues=len(review["issues"]))
        self.say(f"review: {len(review['issues'])} issues passed check-review and check-final")
        return review

    def step_report(self, review: dict[str, Any]) -> None:
        _start, notes = self.start_and_notes()
        values = {"input": {"report_language": self.args.language, "review": review, "scope": rp.load_json(self.out / "scope.json"), "reader_profile": self.profile,
                            "execution_mode": EXECUTION_MODE, "concepts": notes["checks"]["concepts"], "reader_observations": notes["reconcile"]["observations"],
                            "checkpoints": [{k: v for k, v in c.items() if k not in ("packet_path", "observation_path", "run_path")} for c in notes["reconcile"]["checkpoints"]],
                            "repairs": notes["repairs"]["repairs"], "audits": self.audit_rows, "rejected_candidates": [d for d in notes["decisions"]["decisions"] if d["status"] == "rejected"],
                            "run_statistics": self.statistics()}}

        def accept(parsed: Any, call: llm.Call) -> str:
            require(isinstance(parsed, dict) and isinstance(parsed.get("report_markdown"), str) and parsed["report_markdown"].strip(), "report_markdown must be a nonempty string.")
            missing = [issue["id"] for issue in review["issues"] if issue["id"] not in parsed["report_markdown"]]
            require(not missing, f"report_markdown must present every issue of the review; it does not mention {', '.join(missing)}. "
                                 "If the report stopped early, a straight double quotation mark probably ended the string: write “ ” or ‘ ’ inside it.")
            return parsed["report_markdown"]

        report = self.ask("report", "07-report", values, accept, schemas.report())
        (self.out / "report.md").write_text(report.rstrip("\n") + "\n", encoding="utf-8")
        self.stages["report"] = {"status": "written", "path": str(self.out / "report.md")}
        self.say(f"report: {self.out / 'report.md'}")

    # ------------------------------------------------------------ summary

    def statistics(self) -> dict[str, Any]:
        editor = self.client.totals()
        readers = {"calls": len(self.reader_calls), "seconds": round(sum(r["seconds"] or 0 for r in self.reader_calls), 1), "cost_usd": round(sum(r["cost_usd"] or 0 for r in self.reader_calls), 5),
                   "by_kind": {kind: sum(1 for r in self.reader_calls if r["kind"] == kind) for kind in ("prefix", "masked", "branch", "audit")}}
        return {"editor": editor, "readers": readers, "calls": editor["calls"] + readers["calls"], "seconds": round(time.monotonic() - self.started, 1), "retries": self.retries,
                "anchors_canonicalized": self.anchors_canonicalized}

    def write_summary(self, ok: bool, outcome: str, failure: dict[str, Any] | None = None) -> dict[str, Any]:
        editor_config, reader_config = llm.load_config(self.config), llm.load_config(self.reader_config)
        summary = {"ok": ok, "outcome": outcome, "manuscript": str(self.manuscript), "snapshot_id": self.index.get("snapshot_id"), "out": str(self.out),
                   "editor": {"base_url": editor_config.get("base_url"), "model": editor_config.get("model")}, "readers_config": {"base_url": reader_config.get("base_url"), "model": reader_config.get("model")},
                   "options": {"batch_blocks": self.args.batch_blocks, "prefix_checkpoints": self.args.prefix_checkpoints, "masked_windows": self.args.masked_windows, "masked_budget": self.args.masked_budget, "language": self.args.language},
                   "stages": self.stages, "editor_calls": [c.row() for c in self.client.calls], "reader_calls": self.reader_calls, "audits": self.audit_rows,
                   "totals": self.statistics(), "failure": failure, "recorded_at": utc_now()}
        self.write_json(self.out / "summary.json", summary)
        return summary

    def run(self) -> int:
        try:
            self.step_index()
            self.step_scope()
            self.step_reading()
            self.step_checks()
            self.step_readers()
            self.step_reconcile()
            self.step_decisions()
            self.step_repairs()
            self.step_ready()
            review = self.step_review()
            self.step_report(review)
        except StopRun as exc:
            self.log("stopped", stage=exc.stage, message=str(exc))
            self.write_summary(False, f"Stopped at {exc.stage}: {exc}", {"stage": exc.stage, "message": str(exc), "fix": exc.fix})
            self.say(f"STOPPED at {exc.stage}: {exc}\nThe journal under {self.journal} is intact. What to fix: {exc.fix}")
            return 1
        summary = self.write_summary(True, f"Review complete: {self.stages['review']['issues']} issues; report at {self.out / 'report.md'}.")
        totals = summary["totals"]
        self.say(f"done: {totals['calls']} calls ({totals['editor']['calls']} editor, {totals['readers']['calls']} reader), {totals['seconds']} s, {totals['retries']} re-prompts; summary at {self.out / 'summary.json'}")
        return 0


def call_summary(call: llm.Call) -> str:
    value = call.parsed.get("summary") if isinstance(call.parsed, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else "(no summary)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the whole staged review with an OpenAI-compatible LLM as the editor.")
    run.add_argument("--manuscript", required=True, help="UTF-8 Markdown or plain-text edition of the manuscript.")
    run.add_argument("--out", required=True, help="New run directory (journal, editor calls, readers, audits, review.json, report.md, summary.json).")
    run.add_argument("--config", required=True, help="JSON config for the editor endpoint (same format as dispatch_reader.py --runtime http --config).")
    run.add_argument("--reader-config", help="JSON config for the reader and auditor calls (default: --config).")
    run.add_argument("--task", default="review", choices=("review", "quick_review"), help="Scope task (default review).")
    run.add_argument("--profile", help="Prefix reader profile JSON (default assets/reader-profile.json).")
    run.add_argument("--masked-profile", help="Masked reader profile JSON (default assets/masked-reader-profile.json).")
    run.add_argument("--extraction-notes", help="Text file with extraction notes for the scope record.")
    run.add_argument("--language", default="English", help="Language of the user-facing report (default English).")
    run.add_argument("--masked-windows", type=int, default=4, help="Windows of the masked run over the first sections; 0 disables it (default 4).")
    run.add_argument("--masked-budget", type=int, default=250, help="Approximate words per masked window (default 250).")
    run.add_argument("--max-events", type=int, default=40, help="Maximum masked reader calls, corrections included (default 40).")
    run.add_argument("--prefix-checkpoints", type=int, default=3, help="Maximum prefix checkpoints the checks plan may request (default 3).")
    run.add_argument("--batch-blocks", type=int, default=20, help="Blocks per reading and checks prompt (default 20).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    for name in ("masked_windows", "masked_budget", "max_events", "prefix_checkpoints", "batch_blocks"):
        if getattr(args, name) < 0 or (name in ("masked_budget", "max_events", "batch_blocks") and getattr(args, name) <= 0):
            print(json.dumps({"ok": False, "error": {"code": "cli_error", "message": f"--{name.replace('_', '-')} must be a positive integer."}}), file=sys.stderr)
            return 2
    try:
        run = Run(args)
    except (dr.DispatchError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "dispatch_error", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        return run.run()
    except (rp.ReviewDataError, mr.Invalid, dr.DispatchError) as exc:
        run.log("error", message=str(exc))
        run.write_summary(False, f"Error: {exc}", {"stage": "unknown", "message": str(exc), "fix": "Inspect log.jsonl; the journal is intact."})
        print(json.dumps({"ok": False, "error": {"code": "helper_error", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
