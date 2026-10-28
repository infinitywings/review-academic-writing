#!/usr/bin/env python3
"""Build auditable, prefix-only review inputs and check exact text anchors.

Python standard library only. This program performs no semantic analysis, model
calls, networking, sentence splitting, or transport-isolation verification.
Offsets are zero-based Python Unicode character offsets; end offsets are
exclusive. Line numbers are one-based. Only CRLF is normalized to LF.

Index and packet hashes detect inconsistent/stale data, not a coordinated rewrite
of content and every hash. A packet's document snapshot is deliberately opaque:
without the complete index, this program cannot authenticate its source document.
Caller-supplied reader profiles are copied unchanged and must be independently
constructed without future manuscript information.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


SCHEMA_VERSION = 1
OFFSET_UNIT = "unicode_code_points"
NORMALIZATION = "CRLF-to-LF"
SEVERITIES = {"critical", "major", "minor", "optional"}
CONFIDENCES = {"high", "medium", "low"}
OBSERVATION_CATEGORIES = {
    "unavailable_prerequisite", "ambiguous_reference", "missing_relation",
    "retrieval_burden", "scope_ambiguity", "other",
}
REVIEW_CATEGORIES = {
    "late_prerequisite", "unavailable_prerequisite", "ambiguous_reference",
    "scattered_information", "retrieval_burden", "missing_relation",
    "paragraph_function", "terminology_scope_drift", "unsupported_claim", "other",
    "unnecessary_term", "word_choice", "sentence_structure",
}
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
# Hedges and conditions that a fidelity audit compares one by one (dispatch_reader.py audit
# lists them as hedges_in_original; review_steps.py requires each to be reported). Patterns are
# matched case-insensitively on the original text; extend the list here or per call with
# --hedge-list. The list is a floor, not a definition of hedging.
HEDGE_PATTERNS = (
    r"\bmay\b", r"\bmight\b", r"\bcould\b", r"\bwould\b",
    r"\blikely\b", r"\bunlikely\b", r"\bpossibl[ey]\b", r"\bprobabl[ey]\b", r"\bpotential(?:ly)?\b",
    r"\bperhaps\b", r"\bpresumably\b", r"\bapparently\b", r"\barguably\b", r"\bplausibl[ey]\b",
    r"\boften\b", r"\btypically\b", r"\bgenerally\b", r"\busually\b", r"\bmostly\b", r"\bsometimes\b", r"\boccasionally\b", r"\bsomewhat\b", r"\bin general\b",
    r"\bclose to\b", r"\bexcept (?:when|for|in)\b", r"\bgiven that\b", r"\brestricted to\b",
    r"\bsuggest(?:s|ed|ing)?\b", r"\bappear(?:s|ed)? to\b", r"\bseem(?:s|ed)?\b", r"\btend(?:s|ed)? to\b",
    r"\bhypothesi[sz]e(?:s|d)?\b", r"\bspeculate(?:s|d)?\b", r"\bexpect(?:s|ed)? (?:that|to)\b", r"\bassume(?:s|d)?\b",
    r"\bassociated with\b", r"\bcorrelat(?:ed|es|ion|ions) (?:with|between)\b", r"\brelated to\b", r"\blinked to\b",
    r"\bconsistent with\b", r"\bcompatible with\b",
    r"\bapproximately\b", r"\broughly\b", r"\bnearly\b", r"\balmost\b", r"\bat least\b", r"\bat most\b", r"\bup to\b",
    r"\bon average\b", r"\bin part\b", r"\bpartly\b", r"\bpartially\b", r"\bto some extent\b",
    r"\bin (?:some|most|many|several|a few|a subset of|a minority of|a majority of) \w+\b",
    r"\b(?:some|most|many|several|a subset) of\b", r"\b(?:in )?\d+ of (?:the )?\d+\b",
    r"\bunder (?:\w+ ){0,4}?(?:assumptions?|conditions?|settings?|regimes?|hypothes[ie]s|constraints?)\b",
    r"\b(?:in|within) (?:our|this|the present|these) (?:setting|settings|sample|samples|dataset|datasets|cohort|cohorts|experiments?|study|studies)\b",
    r"\bif\b", r"\bunless\b", r"\bassuming\b", r"\bprovided that\b", r"\bonly (?:when|if)\b", r"\bas long as\b",
    r"\bpreliminary\b", r"\btentative(?:ly)?\b", r"\bexploratory\b", r"\bnot necessarily\b", r"\bnot always\b",
    r"\bcannot (?:rule out|exclude)\b", r"\blimited to\b", r"\bwe did not (?:measure|test|examine|observe)\b",
)
HEDGE_REGEX = re.compile("|".join(HEDGE_PATTERNS), re.IGNORECASE)


class ReviewDataError(ValueError):
    """An actionable data or command-line error, suitable for JSON output."""

    def __init__(self, message: str, code: str = "validation_error") -> None:
        super().__init__(message)
        self.code = code


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n")


def hedge_key(term: str) -> str:
    return " ".join(term.lower().split())


def hedge_survives(kept: list[str], proposed_wording: str, extra_patterns: tuple[str, ...] | list[str] = ()) -> bool:
    """A kept hedge survives when the wording carries some listed hedge or the kept phrase itself."""
    if find_hedges(proposed_wording, extra_patterns):
        return True
    wording = hedge_key(proposed_wording)
    return any(re.search(r"(?<!\w)" + re.escape(hedge_key(term)) + r"(?!\w)", wording) for term in kept)


def find_hedges(text: str, extra_patterns: tuple[str, ...] | list[str] = ()) -> list[str]:
    """Hedge and condition phrases in `text`, in order of first occurrence, one entry per distinct phrase.

    A phrase that occurs twice is listed once; the auditor reports it kept only when every
    occurrence is kept. `extra_patterns` are regular expressions added to HEDGE_PATTERNS.
    """
    regex = HEDGE_REGEX if not extra_patterns else re.compile("|".join((*HEDGE_PATTERNS, *extra_patterns)), re.IGNORECASE)
    found: dict[str, str] = {}
    for match in regex.finditer(text):
        key = hedge_key(match.group(0))
        found.setdefault(key, " ".join(match.group(0).split()))
    return list(found.values())


def sha256_text(text: str) -> str:
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as exc:
        raise ReviewDataError("Text contains an invalid Unicode surrogate.") from exc


def _canonical_hash(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (ValueError, TypeError) as exc:
        raise ReviewDataError("Value is not a finite, serializable JSON value.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ReviewDataError(f"{label} must be a JSON object with string keys.")
    return value


def _keys(
    value: dict[str, Any], required: set[str], optional: set[str], label: str,
    *, allow_extra: bool = False,
) -> None:
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise ReviewDataError(f"{label} is missing fields: {', '.join(sorted(missing))}.")
    if extra and not allow_extra:
        raise ReviewDataError(f"{label} has unsupported fields: {', '.join(sorted(extra))}.")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewDataError(f"{label} must be a nonempty string.")
    sha256_text(value)  # Reject invalid Unicode, without changing the text.
    return value


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReviewDataError(f"{label} must be an integer.")
    return value


def _enum(value: Any, allowed: set[str], label: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise ReviewDataError(f"{label} must be one of: {', '.join(sorted(allowed))}.")


def _profile(value: Any) -> dict[str, Any]:
    profile = _object(value, "profile")
    if not profile:
        raise ReviewDataError("profile must be a nonempty JSON object of reader assumptions.")
    _canonical_hash(profile)
    # A deep JSON copy prevents later caller mutations of nested objects.
    return json.loads(json.dumps(profile, ensure_ascii=True, allow_nan=False))


def _classify_block(text: str) -> str:
    """Mechanical Markdown classification using only the supplied block text."""
    lines = text.split("\n")
    if len(lines) == 1 and re.match(r"^ {0,3}#{1,6}[ \t]+\S", lines[0]):
        return "heading"
    if len(lines) == 2 and lines[0].strip() and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", lines[1]):
        return "heading"
    if len(lines) >= 2 and "|" in lines[0]:
        delimiter = lines[1].strip().strip("|")
        cells = delimiter.split("|")
        if len(cells) >= 2 and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells):
            return "table"
    return "text"


def build_blocks(text: str) -> list[dict[str, Any]]:
    """Partition nonblank line runs; separator whitespace remains in full text."""
    blocks: list[dict[str, Any]] = []
    run_start: int | None = None
    run_start_line = 0
    run_end = 0
    run_end_line = 0
    offset = 0

    def finish() -> None:
        nonlocal run_start
        if run_start is not None:
            block_text = text[run_start:run_end]
            blocks.append({
                "block_id": f"B{len(blocks) + 1:04d}",
                "kind": _classify_block(block_text),
                "start_offset": run_start,
                "end_offset": run_end,
                "start_line": run_start_line,
                "end_line": run_end_line,
                "text": block_text,
            })
            run_start = None

    for line_number, line in enumerate(text.split("\n"), 1):
        line_end = offset + len(line)
        if not line.strip(" \t\r"):
            finish()
        else:
            if run_start is None:
                run_start = offset
                run_start_line = line_number
            run_end = line_end
            run_end_line = line_number
        offset = line_end + 1
    finish()
    return blocks


def build_index(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ReviewDataError("Input text must be a string.")
    text = normalize_text(text)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "academic_writing_index",
        "normalization": NORMALIZATION,
        "offset_unit": OFFSET_UNIT,
        "snapshot_id": "sha256:" + sha256_text(text),
        "text": text,
        "blocks": build_blocks(text),
    }
    result["index_sha256"] = _canonical_hash(result)
    return result


def validate_index(value: Any) -> dict[str, Any]:
    index = _object(value, "index")
    _keys(index, {
        "schema_version", "artifact_type", "normalization", "offset_unit",
        "snapshot_id", "text", "blocks", "index_sha256",
    }, set(), "index")
    if not isinstance(index["text"], str):
        raise ReviewDataError("index.text must be a string.")
    expected = build_index(index["text"])
    if index["text"] != expected["text"]:
        raise ReviewDataError("Index text is not CRLF-normalized; rebuild the index.")
    if index["snapshot_id"] != expected["snapshot_id"]:
        raise ReviewDataError("Index snapshot_id does not match its text; rebuild the index.")
    if _canonical_hash(index["blocks"]) != _canonical_hash(expected["blocks"]):
        raise ReviewDataError("Index blocks, IDs, classifications, or ranges do not match its text.")
    if _canonical_hash(index) != _canonical_hash(expected):
        raise ReviewDataError("Index schema or checksum mismatch; rebuild the index.")
    return index


def _checkpoint_id(snapshot_id: str, stop_offset: int, prefix_sha256: str) -> str:
    return "sha256:" + _canonical_hash({
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "stop_offset": stop_offset,
        "prefix_sha256": prefix_sha256,
    })


def _quote_offsets(text: str, quote: str) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        found = text.find(quote, start)
        if found < 0:
            return offsets
        offsets.append(found)
        start = found + 1  # Count overlapping occurrences too.


def build_packet(
    index: Any,
    profile: Any,
    *,
    through_block: str | None = None,
    stop_after_text: str | None = None,
    stop_offset: int | None = None,
    occurrence: int | None = None,
) -> dict[str, Any]:
    index = validate_index(index)
    profile = _profile(profile)
    selected = sum(option is not None for option in (through_block, stop_after_text, stop_offset))
    if selected != 1:
        raise ReviewDataError("Choose exactly one of through_block, stop_after_text, or stop_offset.")
    if occurrence is not None and stop_after_text is None:
        raise ReviewDataError("occurrence is only valid with stop_after_text.")
    text = index["text"]
    if through_block is not None:
        matches = [b for b in index["blocks"] if b["block_id"] == through_block]
        if not matches:
            raise ReviewDataError(f"Unknown through_block: {through_block}.")
        stop_offset = matches[0]["end_offset"]
    elif stop_after_text is not None:
        quote = _nonempty_string(stop_after_text, "stop_after_text")
        matches = _quote_offsets(text, quote)
        if not matches:
            raise ReviewDataError("stop_after_text has no exact occurrence in the normalized text.")
        if occurrence is None:
            if len(matches) != 1:
                raise ReviewDataError("stop_after_text is ambiguous; supply a one-based occurrence.")
            occurrence = 1
        occurrence = _integer(occurrence, "occurrence")
        if not 1 <= occurrence <= len(matches):
            raise ReviewDataError("occurrence is outside the available exact matches.")
        stop_offset = matches[occurrence - 1] + len(quote)
    stop_offset = _integer(stop_offset, "stop_offset")
    if not 0 <= stop_offset <= len(text):
        raise ReviewDataError("stop_offset is outside the normalized text.")
    prefix = text[:stop_offset]
    prefix_hash = sha256_text(prefix)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "academic_writing_packet",
        "offset_unit": OFFSET_UNIT,
        "snapshot_id": index["snapshot_id"],
        "checkpoint_id": _checkpoint_id(index["snapshot_id"], stop_offset, prefix_hash),
        "prefix_sha256": prefix_hash,
        "stop_offset": stop_offset,
        "profile": profile,
        "visible_text": prefix,
        # Recompute on the prefix: even kind/line metadata must not use the suffix.
        "blocks": build_blocks(prefix),
    }
    result["packet_sha256"] = _canonical_hash(result)
    return result


def validate_packet(value: Any) -> dict[str, Any]:
    packet = _object(value, "packet")
    _keys(packet, {
        "schema_version", "artifact_type", "offset_unit", "snapshot_id",
        "checkpoint_id", "prefix_sha256", "stop_offset", "profile",
        "visible_text", "blocks", "packet_sha256",
    }, set(), "packet")
    if (
        type(packet["schema_version"]) is not int
        or packet["schema_version"] != SCHEMA_VERSION
        or packet["artifact_type"] != "academic_writing_packet"
        or packet["offset_unit"] != OFFSET_UNIT
    ):
        raise ReviewDataError("Unsupported packet schema or offset unit.")
    if not isinstance(packet["snapshot_id"], str) or not SNAPSHOT_ID.fullmatch(packet["snapshot_id"]):
        raise ReviewDataError("packet.snapshot_id must be an opaque sha256:<hex> identifier.")
    prefix = packet["visible_text"]
    if not isinstance(prefix, str) or normalize_text(prefix) != prefix:
        raise ReviewDataError("packet.visible_text must be a CRLF-normalized string.")
    stop = _integer(packet["stop_offset"], "packet.stop_offset")
    if stop != len(prefix):
        raise ReviewDataError("Packet stop_offset does not match its visible prefix length.")
    if packet["prefix_sha256"] != sha256_text(prefix):
        raise ReviewDataError("Packet prefix_sha256 does not match visible_text.")
    expected_checkpoint = _checkpoint_id(packet["snapshot_id"], stop, packet["prefix_sha256"])
    if packet["checkpoint_id"] != expected_checkpoint:
        raise ReviewDataError("Packet checkpoint_id does not match its snapshot and prefix.")
    if _canonical_hash(packet["blocks"]) != _canonical_hash(build_blocks(prefix)):
        raise ReviewDataError("Packet blocks or ranges do not match its visible prefix.")
    _profile(packet["profile"])
    checksum = packet["packet_sha256"]
    if not isinstance(checksum, str) or not HEX_SHA256.fullmatch(checksum):
        raise ReviewDataError("packet.packet_sha256 must be a lowercase SHA-256 hex digest.")
    payload = {k: v for k, v in packet.items() if k != "packet_sha256"}
    if checksum != _canonical_hash(payload):
        raise ReviewDataError("Packet checksum mismatch; regenerate the packet.")
    return packet


def _anchor(value: Any, blocks: dict[str, dict[str, Any]], label: str) -> None:
    anchor = _object(value, label)
    _keys(anchor, {"block_id", "quote"}, set(), label)
    block_id = _nonempty_string(anchor["block_id"], label + ".block_id")
    quote = _nonempty_string(anchor["quote"], label + ".quote")
    if block_id not in blocks:
        raise ReviewDataError(f"{label}.block_id is not present in the supplied text.")
    if quote not in blocks[block_id]["text"]:
        raise ReviewDataError(f"{label}.quote is not an exact substring of its supplied block.")


def _evidence(value: Any, blocks: dict[str, dict[str, Any]], label: str) -> None:
    if not isinstance(value, list):
        raise ReviewDataError(f"{label} must be an array of exact text anchors.")
    for number, anchor in enumerate(value):
        _anchor(anchor, blocks, f"{label}[{number}]")


def _issues(value: Any, blocks: dict[str, dict[str, Any]], *, final: bool) -> int:
    if not isinstance(value, list):
        raise ReviewDataError("issues must be an array; use [] for no findings.")
    required = {"id", "category", "anchor", "severity", "confidence"}
    required |= {"diagnosis", "action", "scientific_fidelity"} if final else {"reader_question", "consequence"}
    optional = {"evidence"}
    seen: set[str] = set()
    for number, value_item in enumerate(value):
        label = f"issues[{number}]"
        issue = _object(value_item, label)
        _keys(issue, required, optional, label, allow_extra=final)
        issue_id = _nonempty_string(issue["id"], label + ".id")
        if issue_id in seen:
            raise ReviewDataError(f"Duplicate issue id: {issue_id}.")
        seen.add(issue_id)
        _enum(issue["category"], REVIEW_CATEGORIES if final else OBSERVATION_CATEGORIES, label + ".category")
        _enum(issue["severity"], SEVERITIES, label + ".severity")
        _enum(issue["confidence"], CONFIDENCES, label + ".confidence")
        _anchor(issue["anchor"], blocks, label + ".anchor")
        for field in required - {"id", "category", "anchor", "severity", "confidence"}:
            _nonempty_string(issue[field], label + "." + field)
        if "evidence" in issue:
            _evidence(issue["evidence"], blocks, label + ".evidence")
    return len(value)


def check_observation(packet: Any, observation: Any) -> dict[str, Any]:
    packet = validate_packet(packet)
    observation = _object(observation, "observation")
    _keys(observation, {
        "snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256", "understanding", "issues",
    }, set(), "observation")
    for field in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256"):
        if observation[field] != packet[field]:
            raise ReviewDataError(f"Observation {field} does not match the supplied packet; it may be stale.")
    _nonempty_string(observation["understanding"], "observation.understanding")
    blocks = {block["block_id"]: block for block in packet["blocks"]}
    count = _issues(observation["issues"], blocks, final=False)
    return {
        "ok": True,
        "command": "check-observation",
        "snapshot_id": packet["snapshot_id"],
        "checkpoint_id": packet["checkpoint_id"],
        "issue_count": count,
        "scope": {
            "verified": ["internal packet hashes and ranges", "matching observation identifiers and exact packet checksum (including profile)", "required fields and enums", "exact anchors within visible blocks"],
            "not_verified": ["source authenticity", "profile provenance", "runtime isolation or transport", "semantic correctness", "human comprehension"],
        },
    }


def _concept_ledger(value: Any, blocks: dict[str, dict[str, Any]]) -> None:
    if not isinstance(value, list):
        raise ReviewDataError("concept_ledger must be an array of objects.")
    seen: set[str] = set()
    for number, entry_value in enumerate(value):
        label = f"concept_ledger[{number}]"
        entry = _object(entry_value, label)
        if not entry:
            raise ReviewDataError(f"{label} must not be an empty object.")
        if "concept_id" in entry:
            concept_id = _nonempty_string(entry["concept_id"], label + ".concept_id")
            if concept_id in seen:
                raise ReviewDataError(f"Duplicate concept_id: {concept_id}.")
            seen.add(concept_id)
        for field in ("first_mention", "earliest_usable_explanation", "first_critical_use"):
            if field in entry and entry[field] is not None:
                _anchor(entry[field], blocks, label + "." + field)
        if "evidence" in entry:
            _evidence(entry["evidence"], blocks, label + ".evidence")


def check_review(index: Any, review: Any) -> dict[str, Any]:
    index = validate_index(index)
    review = _object(review, "review")
    _keys(review, {"snapshot_id", "summary", "issues"}, {"concept_ledger"}, "review", allow_extra=True)
    if review["snapshot_id"] != index["snapshot_id"]:
        raise ReviewDataError("Review snapshot_id does not match the supplied index; it may be stale.")
    _nonempty_string(review["summary"], "review.summary")
    blocks = {block["block_id"]: block for block in index["blocks"]}
    count = _issues(review["issues"], blocks, final=True)
    if "concept_ledger" in review:
        _concept_ledger(review["concept_ledger"], blocks)
    return {
        "ok": True,
        "command": "check-review",
        "snapshot_id": index["snapshot_id"],
        "issue_count": count,
        "scope": {
            "verified": ["internal index hashes and ranges", "matching review snapshot", "required fields and enums", "exact issue and conventional ledger anchors"],
            "not_verified": ["source authenticity", "runtime isolation or transport (including self-described execution_mode)", "diagnostic correctness", "scientific fidelity", "concept-ledger semantics", "additional editorial fields outside the core schema and conventional ledger anchors", "human comprehension"],
        },
    }


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewDataError(f"Duplicate JSON object key: {key}.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ReviewDataError(f"Nonfinite JSON number is not allowed: {value}.")


def load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return json.load(handle, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)


def write_json_atomic(path: str | Path, value: Any) -> None:
    """Publish one complete new file atomically; never replace an existing path."""
    path = Path(path)
    if os.path.lexists(path):
        raise ReviewDataError("Output path already exists; choose a new path.", "output_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Unlike replace()/rename(), hard-link publication fails atomically
            # when another process has already created the destination.
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ReviewDataError("Output path already exists; choose a new path.", "output_exists") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ReviewDataError(message, "cli_error")


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Index normalized UTF-8 Markdown or plain text.")
    index.add_argument("--input", required=True)
    index.add_argument("--out", required=True)
    packet = commands.add_parser("packet", help="Create a prefix-only reader packet.")
    packet.add_argument("--index", required=True)
    packet.add_argument("--profile", required=True)
    packet.add_argument("--out", required=True)
    selector = packet.add_mutually_exclusive_group(required=True)
    selector.add_argument("--through-block", metavar="B0001")
    selector.add_argument("--stop-after-text", metavar="EXACT_QUOTE")
    selector.add_argument("--stop-offset", type=int, metavar="N")
    packet.add_argument("--occurrence", type=int, metavar="N", help="One-based match for --stop-after-text; overlapping matches count.")
    observation = commands.add_parser("check-observation", help="Validate a frozen reader observation against its packet.")
    observation.add_argument("--packet", required=True)
    observation.add_argument("--observation", required=True)
    review = commands.add_parser("check-review", help="Validate a final review against its indexed snapshot.")
    review.add_argument("--index", required=True)
    review.add_argument("--review", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "index":
            source = Path(args.input)
            if source.suffix.lower() not in {".md", ".txt"}:
                raise ReviewDataError("--input must be a UTF-8 .md or .txt file.")
            with source.open("r", encoding="utf-8", newline="") as handle:
                result = build_index(handle.read())
            write_json_atomic(args.out, result)
            response = {"ok": True, "command": "index", "snapshot_id": result["snapshot_id"], "output": str(args.out)}
        elif args.command == "packet":
            result = build_packet(
                load_json(args.index), load_json(args.profile),
                through_block=args.through_block, stop_after_text=args.stop_after_text,
                stop_offset=args.stop_offset, occurrence=args.occurrence,
            )
            write_json_atomic(args.out, result)
            response = {"ok": True, "command": "packet", "checkpoint_id": result["checkpoint_id"], "prefix_sha256": result["prefix_sha256"], "output": str(args.out)}
        elif args.command == "check-observation":
            response = check_observation(load_json(args.packet), load_json(args.observation))
        else:
            response = check_review(load_json(args.index), load_json(args.review))
        print(json.dumps(response, ensure_ascii=True, allow_nan=False))
        return 0
    except ReviewDataError as exc:
        error = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        error = {"ok": False, "error": {"code": "input_output_error", "message": str(exc)}}
    print(json.dumps(error, ensure_ascii=True), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
