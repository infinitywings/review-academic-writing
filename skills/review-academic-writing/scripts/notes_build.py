#!/usr/bin/env python3
"""Build the six review records from fragments, with the checks a hand-written builder forgets, and render the report.

Standard library only. Every run of the staged review used to need its own builder script
that assembled reading, checks, reconcile, decisions, repairs, and review JSON from
dictionaries, verified every quotation against index.json, and picked the latest audit.
This program is that builder, once:

  reading    paragraphs keyed by block id -> reading notes, every included block covered
  checks     concepts, passages keyed by block id, checkpoint plan, candidates, no_candidate
  reconcile  checkpoints (prefix entries expanded from their events directory), branches,
             observations, candidates
  decisions  one decision per candidate from checks and reconcile
  repairs    one repair per accepted or needs_author candidate; for an edit, the latest
             audit of the candidate decides whether its wording is recorded. With
             --no-wording (a quick review) every edit records its action and no wording,
             and no audit is read
  review     the final review from the five recorded stages, a summary, and a scope note;
             the priority list, each issue's recorded support, and the depth (quick,
             standard, deep: the fragment's, or the one the records support) are filled in
  report     report.md, rendered from a review.json that passes check-final (--review,
             no fragment); minor items go to a separate appendix file
  section-ends
             a checks fragment whose checkpoint_plan has one prefix reader at the end of
             every section (--journal, --index, --out; no fragment). Pass it to checks as
             one more --source

Fragments come from --source paths: a JSON file holding one object, a Python module that
defines FRAGMENT (a dict) or build(ctx) returning one, or a directory of such files read
in name order. Fragments are merged: a mapping keyed by block id or candidate id merges by
key, a list extends, a scalar is replaced by the later fragment. Inside a module, ctx.q(block_id,
quote) returns an anchor and records a quote that is not exact text; ctx.text maps block ids
to text; ctx.opening(block_id, n) is an anchor of the block's first words.

Built-in checks, each reported before the file is written: every anchor-shaped object
({block_id, quote}) quotes exact text of its block; every included block has a reading
paragraph or a passage check; the field vocabularies of staged-review.md (through the same
validator review_steps.py record uses, so a record that builds here records there);
needs_author candidates carry a question that ends with a question mark; an edit's wording
comes from its latest audit and only when that audit found the meaning preserved.

The program judges shape and bookkeeping, not whether the notes are right.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

sys.dont_write_bytecode = True
# The helpers import each other by module name; isolated mode and symlinked installs leave this directory off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_packets as rp  # noqa: E402
import review_steps as rs  # noqa: E402

STAGES = ("reading", "checks", "reconcile", "decisions", "repairs", "review")
KEYED_FIELDS = {"paragraphs": "block_id", "passages": "block_id", "decisions": "candidate_id", "repairs": "candidate_id"}
LIST_FIELDS = {"concepts", "checkpoint_plan", "candidates", "no_candidate", "checkpoints", "branches", "observations", "issues"}
READING_FIELDS = ("paraphrase", "role", "required_context", "new_information", "open_questions")
PASSAGE_FIELDS = ("structure_check", "wording_check", "claim_check")
REPAIR_FIELDS = ("kind", "action", "scientific_fidelity", "new_terms_check", "affected_block_ids")
AUDIT_FILE = re.compile(r"^(?P<cid>[^./]+?)(?:-(?P<round>\d+))?\.audit\.json$")
QUICK_WITHHELD_REASON = "Not written: this is a quick review. A standard review adds the wording and its fidelity audit."
Error = rp.ReviewDataError


class BuildError(Error):
    """A fragment or source problem; nothing was written."""


# ---------------------------------------------------------------- fragments


class Context:
    """What a Python fragment may use: the index text, the scope, and anchor helpers."""

    def __init__(self, index: dict, included: list[str]) -> None:
        self.text = {b["block_id"]: b["text"] for b in index["blocks"]}
        self.blocks = [b["block_id"] for b in index["blocks"]]
        self.included = list(included)
        self.failures: list[tuple[str, str]] = []

    def q(self, block_id: str, quote: str) -> dict:
        if block_id not in self.text or not isinstance(quote, str) or quote not in self.text[block_id]:
            self.failures.append((block_id, str(quote)[:80]))
        return {"block_id": block_id, "quote": quote}

    def opening(self, block_id: str, n: int = 70) -> dict:
        text = self.text[block_id]
        head = text[:n]
        if len(text) > n and " " in head:
            head = head[: head.rfind(" ")]
        return {"block_id": block_id, "quote": head}


def load_module(path: Path, ctx: Context) -> dict:
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise BuildError(f"Cannot import {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "build") and callable(module.build):
        value = module.build(ctx)
    elif hasattr(module, "FRAGMENT"):
        value = module.FRAGMENT
    else:
        raise BuildError(f"{path} defines neither FRAGMENT nor build(ctx).")
    if not isinstance(value, dict):
        raise BuildError(f"{path}: the fragment must be a JSON-like object, not {type(value).__name__}.")
    return value


def load_fragments(sources: list[str], ctx: Context) -> list[tuple[Path, dict]]:
    fragments: list[tuple[Path, dict]] = []
    for source in sources:
        path = Path(source)
        if path.is_dir():
            files = sorted(p for p in path.iterdir() if p.suffix in (".py", ".json") and not p.name.startswith("."))
        elif path.is_file():
            files = [path]
        else:
            raise BuildError(f"--source {source} is not a file or directory.")
        for file in files:
            if file.suffix == ".py":
                fragments.append((file, load_module(file, ctx)))
            else:
                value = rp.load_json(file)
                if not isinstance(value, dict):
                    raise BuildError(f"{file} must hold one JSON object.")
                fragments.append((file, value))
    if not fragments:
        raise BuildError("No fragment was found under --source.")
    return fragments


def merge(fragments: list[tuple[Path, dict]]) -> dict:
    result: dict = {}
    for path, fragment in fragments:
        for key, value in fragment.items():
            if key in KEYED_FIELDS:
                mapping = as_mapping(value, KEYED_FIELDS[key], f"{path.name}:{key}")
                target = result.setdefault(key, {})
                clash = sorted(set(target) & set(mapping))
                if clash:
                    raise BuildError(f"{path.name}: {key} repeats {', '.join(clash[:5])} already given by an earlier fragment; each id once.")
                target.update(mapping)
            elif key in LIST_FIELDS:
                if not isinstance(value, list):
                    raise BuildError(f"{path.name}: {key} must be a list.")
                result.setdefault(key, []).extend(value)
            else:
                result[key] = value
    return result


def as_mapping(value: Any, id_field: str, label: str) -> dict:
    """A {id: row} mapping from a dict keyed by id or a list of rows that carry the id field."""
    if isinstance(value, dict):
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in value.items()}
    if isinstance(value, list):
        mapping: dict = {}
        for n, row in enumerate(value):
            if not isinstance(row, dict) or not isinstance(row.get(id_field), str):
                raise BuildError(f"{label}[{n}] must be an object with {id_field}.")
            if row[id_field] in mapping:
                raise BuildError(f"{label} lists {row[id_field]} twice.")
            mapping[row[id_field]] = dict(row)
        return mapping
    raise BuildError(f"{label} must be a mapping keyed by {id_field} or a list of rows.")


# ---------------------------------------------------------------- journal


class Journal:
    def __init__(self, journal: Path, index: dict) -> None:
        self.path = journal
        self.index = index
        self.start, self.notes, self.last_hash = rs.load_run(journal, index)
        self.included = list(self.start["scope"]["included_block_ids"])
        self.blocks = {b["block_id"]: b for b in index["blocks"]}

    def require(self, stage: str) -> None:
        """The stages before `stage` must be recorded; the stage itself must not be."""
        if stage == "review":
            missing = [s for s in rs.STAGES if s not in self.notes]
            if missing:
                raise BuildError("The review needs every stage recorded; missing: " + ", ".join(missing) + ".")
            return
        needed = rs.STAGES[: rs.STAGES.index(stage)]
        missing = [s for s in needed if s not in self.notes]
        if missing:
            raise BuildError(f"Building {stage} needs the earlier stages recorded in the journal; missing: {', '.join(missing)}.")
        if stage in self.notes:
            raise BuildError(f"The journal already records {stage}; build the next stage, or start a corrected run.")

    def candidates(self) -> dict:
        return rs.all_candidates(self.notes)

    def decisions(self) -> dict:
        return rs.by_id(self.notes["decisions"]["decisions"], "candidate_id", "decisions")


# ---------------------------------------------------------------- quotation check


def anchor_failures(value: Any, blocks: dict, path: str = "$") -> list[str]:
    """Every {block_id, quote} object anywhere in `value` whose quote is not exact text of its block."""
    failures: list[str] = []
    if isinstance(value, dict):
        if set(value) == {"block_id", "quote"}:
            block, quote = value["block_id"], value["quote"]
            if not isinstance(block, str) or block not in blocks:
                failures.append(f"{path}: block {block!r} is not in the index")
            elif not isinstance(quote, str) or not quote.strip():
                failures.append(f"{path}: empty quote for {block}")
            elif quote not in blocks[block]["text"]:
                failures.append(f"{path}: not exact text of {block}: {quote[:60]!r}")
            return failures
        for key, item in value.items():
            failures.extend(anchor_failures(item, blocks, f"{path}.{key}"))
    elif isinstance(value, list):
        for n, item in enumerate(value):
            failures.extend(anchor_failures(item, blocks, f"{path}[{n}]"))
    return failures


# ---------------------------------------------------------------- stage builders


def rows_in_order(mapping: dict, order: list[str], label: str, id_field: str) -> list[dict]:
    missing = [i for i in order if i not in mapping]
    extra = [i for i in mapping if i not in order]
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing {len(missing)}: " + ", ".join(missing[:12]) + (", ..." if len(missing) > 12 else ""))
        if extra:
            parts.append(f"not in scope: " + ", ".join(extra[:12]) + (", ..." if len(extra) > 12 else ""))
        raise BuildError(f"{label} must cover every included block exactly once; " + "; ".join(parts) + ".")
    rows = []
    for key in order:
        row = dict(mapping[key])
        row.setdefault(id_field, key)
        rows.append(row)
    return rows


def build_reading(fragment: dict, journal: Journal, ctx: Context) -> dict:
    paragraphs = fragment.get("paragraphs")
    if not isinstance(paragraphs, dict):
        raise BuildError("reading fragments need paragraphs keyed by block id.")
    rows = []
    for row in rows_in_order(paragraphs, journal.included, "Reading paragraphs", "block_id"):
        block = row.pop("block_id")
        anchor = row.pop("anchor", None)
        if anchor is None:
            anchor = ctx.opening(block)
        elif isinstance(anchor, str):
            anchor = {"block_id": block, "quote": anchor}
        missing = [f for f in READING_FIELDS if f not in row]
        if missing:
            raise BuildError(f"Reading paragraph {block} lacks {', '.join(missing)}.")
        for field in ("required_context", "new_information", "open_questions"):
            if isinstance(row[field], str):
                row[field] = [row[field]] if row[field] else []
        rows.append({"anchor": anchor, **{f: row[f] for f in READING_FIELDS}})
    return {"snapshot_id": journal.start["snapshot_id"], "summary": summary_of(fragment, "reading"), "paragraphs": rows}


def summary_of(fragment: dict, stage: str) -> str:
    summary = fragment.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise BuildError(f"{stage} fragments need a nonempty summary.")
    return summary


def with_ids(rows: list, prefix: str, id_field: str = "id") -> list[dict]:
    """Assign prefixNN ids to rows that have none, using the lowest numbers not already given."""
    used = {row.get(id_field) for row in rows if isinstance(row, dict) and isinstance(row.get(id_field), str)}
    counter = 0
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise BuildError(f"{prefix} rows must be objects.")
        row = dict(row)
        if not isinstance(row.get(id_field), str):
            counter += 1
            while f"{prefix}{counter:02d}" in used:
                counter += 1
            row[id_field] = f"{prefix}{counter:02d}"
            used.add(row[id_field])
        result.append(row)
    return result


def build_checks(fragment: dict, journal: Journal, ctx: Context) -> dict:
    passages = fragment.get("passages")
    if not isinstance(passages, dict):
        raise BuildError("checks fragments need passages keyed by block id.")
    rows = []
    for row in rows_in_order(passages, journal.included, "Passage checks", "block_id"):
        block = row["block_id"]
        missing = [f for f in PASSAGE_FIELDS if f not in row]
        if missing:
            raise BuildError(f"Passage check {block} lacks {', '.join(missing)}.")
        evidence = row.get("evidence") or []
        if isinstance(evidence, dict):
            evidence = [evidence]
        if not any(isinstance(a, dict) and a.get("block_id") == block for a in evidence):
            evidence = [ctx.opening(block, 40), *evidence]
        rows.append({"block_id": block, **{f: row[f] for f in PASSAGE_FIELDS}, "evidence": evidence})
    concepts = with_ids(fragment.get("concepts", []), "K", "concept_id")
    for concept in concepts:
        for field in ("first_mention", "earliest_usable_explanation", "first_critical_use"):
            concept.setdefault(field, None)
    no_concepts_reason = fragment.get("no_concepts_reason", "")
    if not concepts and not no_concepts_reason:
        raise BuildError("checks with no concepts need no_concepts_reason.")
    plan = with_ids(fragment.get("checkpoint_plan", []), "P")
    candidates = with_ids(fragment.get("candidates", []), "C")
    for candidate in candidates:
        candidate.setdefault("evidence", [])
    return {"snapshot_id": journal.start["snapshot_id"], "summary": summary_of(fragment, "checks"), "concepts": concepts,
            "no_concepts_reason": no_concepts_reason, "passages": rows, "checkpoint_plan": plan, "candidates": candidates,
            "no_candidate": list(fragment.get("no_candidate", []))}


def prefix_files(point: dict, plan_id: str) -> tuple[Path, Path]:
    """The packet and the accepted observation of a prefix checkpoint, under its events directory.

    Without a label, a prefix-batch.json in the directory decides: its row for the plan id names
    the files of the call that passed, which a rerun stores under S05r1 rather than S05. Otherwise
    the packet is <label>.packet.json, and the observation carries the label of the attempt that
    was accepted: <label>, or <label>b or <label>c after a correction.
    """
    events = Path(point["events_dir"])
    batch = events / rp.PREFIX_BATCH_FILE
    if point.get("label") is None and point.get("packet_path") is None and batch.is_file():
        value = rp.load_json(batch)
        for row in value.get("entries", []) if isinstance(value, dict) else []:
            if isinstance(row, dict) and row.get("plan_id") == plan_id and row.get("status") in ("ok", "kept"):
                return events / Path(row["packet_path"]).name, events / Path(row["observation_path"]).name
    label = point.get("label") or point.get("plan_id") or point["id"]
    observations = [events / f"{label}{suffix}.observation.json" for suffix in rp.ATTEMPT_SUFFIXES]
    return events / f"{label}.packet.json", next((path for path in observations if path.is_file()), observations[0])


def expand_prefix_checkpoint(point: dict, plan: dict) -> dict:
    """A reconcile checkpoint given as {id, plan_id?, events_dir, label?, limitation, result?} -> the full entry."""
    events = Path(point["events_dir"])
    packet_path, observation_path = prefix_files(point, point.get("plan_id", point["id"]))
    if not packet_path.is_file() or not observation_path.is_file():
        raise BuildError(f"Checkpoint {point['id']}: {packet_path.name} or {observation_path.name} is missing under {events}.")
    plan_id = point.get("plan_id", point["id"])
    planned = plan.get(plan_id)
    if planned is None:
        raise BuildError(f"Checkpoint {point['id']}: plan_id {plan_id} is not in the recorded checkpoint_plan.")
    observation = rp.load_json(observation_path)
    issues = observation.get("issues", []) if isinstance(observation, dict) else []
    understanding = observation.get("understanding", "") if isinstance(observation, dict) else ""
    unplaced = observation.get("cannot_place", []) if isinstance(observation, dict) else []
    result = point.get("result") or (f"{len(issues)} issue(s)" + (f", {len(unplaced)} cannot_place entr{'y' if len(unplaced) == 1 else 'ies'}" if unplaced else "") + f"; understanding: {str(understanding)[:400]}")
    entry = {"id": point["id"], "plan_id": plan_id, "anchor": planned["anchor"], "mode": point.get("mode", planned["mode"]),
             "result": result, "limitation": point.get("limitation"), "packet_path": str(packet_path), "observation_path": str(observation_path),
             "figure_policy": point.get("figure_policy", "text-only")}
    if not isinstance(entry["limitation"], str) or not entry["limitation"].strip():
        raise BuildError(f"Checkpoint {point['id']} needs a limitation sentence (what the dispatch could not verify).")
    return entry


def build_reconcile(fragment: dict, journal: Journal, ctx: Context) -> dict:
    plan = rs.by_id(journal.notes["checks"]["checkpoint_plan"], "id", "checkpoint_plan")
    checkpoints = []
    for point in with_ids(fragment.get("checkpoints", []), "P"):
        checkpoints.append(expand_prefix_checkpoint(point, plan) if "events_dir" in point else point)
    observations = with_ids(fragment.get("observations", []), "O")
    for observation in observations:
        observation.setdefault("candidate_id", None)
        observation.setdefault("resolved_by", None)
        observation.setdefault("background", None)
        observation.setdefault("evidence", [])
    candidates = with_ids(fragment.get("candidates", []), "C")
    earlier = {c["id"] for c in journal.notes["checks"]["candidates"]}
    clash = [c["id"] for c in candidates if c["id"] in earlier]
    if clash:
        raise BuildError("Reconcile candidates reuse checks ids: " + ", ".join(clash) + "; give new ids (the checks stage used up to " + max(earlier) + ").")
    for candidate in candidates:
        candidate.setdefault("evidence", [])
    value = {"snapshot_id": journal.start["snapshot_id"], "summary": summary_of(fragment, "reconcile"), "checkpoints": checkpoints,
             "branches": with_ids(fragment.get("branches", []), "BR"), "observations": observations, "candidates": candidates}
    if not checkpoints:
        value["no_checkpoints_reason"] = fragment.get("no_checkpoints_reason", "")
        if not value["no_checkpoints_reason"]:
            raise BuildError("reconcile with no checkpoints needs no_checkpoints_reason.")
    return value


def challenge_lookup(paths: list[str]) -> dict[str, str]:
    """Candidate id -> the challenge file that holds its challenge; a candidate challenged in two files is refused."""
    found: dict[str, str] = {}
    for text in paths:
        record = rp.load_json(text)
        entries = record.get("challenges") if isinstance(record, dict) else None
        if not isinstance(entries, list):
            raise BuildError(f"--challenge {text} is not a challenge file written by dispatch_reader.py challenge.")
        for entry in entries:
            key = entry.get("candidate_id") if isinstance(entry, dict) else None
            if key in found:
                raise BuildError(f"Candidate {key} is challenged in both {found[key]} and {text}; give one challenge per candidate.")
            found[key] = str(Path(text).resolve())
    return found


def build_decisions(fragment: dict, journal: Journal, ctx: Context, challenges: list[str] | None = None) -> dict:
    decisions = fragment.get("decisions")
    if not isinstance(decisions, dict):
        raise BuildError("decisions fragments need decisions keyed by candidate id.")
    candidates = journal.candidates()
    missing = [c for c in candidates if c not in decisions]
    extra = [c for c in decisions if c not in candidates]
    if missing or extra:
        raise BuildError("Decisions must account for every candidate exactly once; "
                         + (f"missing: {', '.join(missing)}" if missing else "") + ("; " if missing and extra else "")
                         + (f"unknown candidates: {', '.join(extra)}" if extra else "") + ".")
    lookup = challenge_lookup(challenges or [])
    rows = []
    for key in candidates:
        row = dict(decisions[key])
        row["candidate_id"] = key
        row.setdefault("evidence", [])
        if row.get("challenge_path") is None and key in lookup:
            row["challenge_path"] = lookup[key]
        if row.get("status") in rs.FINAL_STATUSES:
            needed = ("category", "diagnosis", "severity", "confidence") + (("close_criterion",) if journal.start["schema_version"] >= 7 else ())
            lacking = [f for f in needed if not row.get(f)]
            if lacking:
                raise BuildError(f"Decision {key} ({row['status']}) lacks {', '.join(lacking)}.")
        rows.append(row)
    built = {"snapshot_id": journal.start["snapshot_id"], "summary": summary_of(fragment, "decisions"), "decisions": rows}
    if fragment.get("no_challenge_reason") is not None:
        built["no_challenge_reason"] = fragment["no_challenge_reason"]
    return built


def audits_by_candidate(directory: Path | None) -> dict[str, list[tuple[int, Path, dict]]]:
    """Every *.audit.json under the directory, grouped by candidate_id and ordered by round (then file name)."""
    found: dict[str, list[tuple[int, Path, dict]]] = {}
    if directory is None:
        return found
    if not directory.is_dir():
        raise BuildError(f"--audits {directory} is not a directory.")
    for path in sorted(directory.iterdir()):
        match = AUDIT_FILE.match(path.name)
        if not match:
            continue
        audit = rp.load_json(path)
        if not isinstance(audit, dict) or not isinstance(audit.get("candidate_id"), str):
            raise BuildError(f"{path} is not a fidelity audit (no candidate_id).")
        round_number = audit.get("audit_round")
        if not isinstance(round_number, int) or isinstance(round_number, bool):
            round_number = int(match.group("round") or 1)
        found.setdefault(audit["candidate_id"], []).append((round_number, path, audit))
    for rows in found.values():
        rows.sort(key=lambda item: (item[0], item[1].name))
    return found


def latest_audit(rows: list[tuple[int, Path, dict]]) -> tuple[int, Path, dict]:
    return rows[-1]


def audit_preserved(audit: dict) -> bool:
    checks = audit.get("checks") if isinstance(audit.get("checks"), dict) else {}
    return audit.get("verdict") == "preserved" and not any(v == "changed" for k, v in checks.items() if k != "hedges")


def build_repairs(fragment: dict, journal: Journal, ctx: Context, audits_dir: Path | None, compare_dir: Path | None, no_wording: bool = False) -> dict:
    repairs = fragment.get("repairs")
    if not isinstance(repairs, dict):
        raise BuildError("repairs fragments need repairs keyed by candidate id.")
    decisions = journal.decisions()
    wanted = [key for key, row in decisions.items() if row["status"] in rs.FINAL_STATUSES]
    missing = [c for c in wanted if c not in repairs]
    extra = [c for c in repairs if c not in wanted]
    if missing or extra:
        raise BuildError("Repairs must correspond exactly to accepted and needs_author candidates; "
                         + (f"missing: {', '.join(missing)}" if missing else "") + ("; " if missing and extra else "")
                         + (f"not accepted or needs_author: {', '.join(extra)}" if extra else "") + ".")
    audits = {} if no_wording else audits_by_candidate(audits_dir)
    rows = []
    selection: list[dict] = []
    for key in wanted:
        row = dict(repairs[key])
        row["candidate_id"] = key
        status = decisions[key]["status"]
        if status == "needs_author":
            row.setdefault("kind", "author_question")
            if row["kind"] != "author_question":
                raise BuildError(f"Repair {key}: the decision is needs_author, so kind must be author_question.")
            question = row.get("author_question")
            if not isinstance(question, str) or not rs.is_question(question):
                raise BuildError(f"Repair {key}: needs_author requires author_question ending with a question mark; got {question!r}.")
            row.setdefault("proposed_wording", None)
        lacking = [f for f in REPAIR_FIELDS if f not in row]
        if lacking:
            raise BuildError(f"Repair {key} lacks {', '.join(lacking)}.")
        row.setdefault("proposed_wording", None)
        row.setdefault("author_question", None)
        row.setdefault("audit_path", None)
        row.setdefault("compare_path", None)
        if no_wording:
            if row["proposed_wording"] is not None:
                raise BuildError(f"Repair {key} gives proposed_wording, but --no-wording builds the repairs of a quick review, which records the action only. "
                                 "Remove the wording, or build without --no-wording for a standard review.")
            if row["kind"] == "edit" and row.get("wording_withheld_reason") is None:
                row["wording_withheld_reason"] = QUICK_WITHHELD_REASON
        if row["kind"] == "edit" and row.get("proposed_wording") is None and row.get("wording_withheld_reason") is None:
            rounds = audits.get(key, [])
            if not rounds:
                raise BuildError(f"Repair {key} is an edit with no proposed_wording and no audit under --audits; give wording_withheld_reason or audit a wording.")
            round_number, path, audit = latest_audit(rounds)
            if audit_preserved(audit):
                row["proposed_wording"] = audit["proposed_wording"]
                row["audit_path"] = str(path)
                selection.append({"candidate_id": key, "audit_round": round_number, "audit_path": str(path), "verdict": "preserved", "rounds_seen": len(rounds)})
            else:
                changed = [k for k, v in (audit.get("checks") or {}).items() if v == "changed"]
                row["proposed_wording"] = None
                row["wording_withheld_reason"] = f"the latest audited wording (round {round_number}, {path.name}) was {audit.get('verdict')}" + (f" with changed {', '.join(changed)}" if changed else "") + "; no wording is recorded"
                selection.append({"candidate_id": key, "audit_round": round_number, "audit_path": str(path), "verdict": audit.get("verdict"), "rounds_seen": len(rounds), "withheld": True})
        elif row["kind"] == "edit" and row.get("proposed_wording") is not None and row.get("audit_path") is None:
            rounds = audits.get(key, [])
            matching = [(n, p, a) for n, p, a in rounds if a.get("proposed_wording") == row["proposed_wording"]]
            if matching:
                round_number, path, audit = matching[-1]
                row["audit_path"] = str(path)
                selection.append({"candidate_id": key, "audit_round": round_number, "audit_path": str(path), "verdict": audit.get("verdict"), "rounds_seen": len(rounds)})
            else:
                raise BuildError(f"Repair {key} gives proposed_wording but no audit_path, and no audit under --audits audited that exact wording.")
        if row.get("proposed_wording") is not None and row.get("compare_path") is None:
            for directory in (compare_dir, audits_dir):
                candidate = directory / f"{key}.features.json" if directory else None
                if candidate is not None and candidate.is_file():
                    row["compare_path"] = str(candidate)
                    break
        rows.append(row)
    return {"snapshot_id": journal.start["snapshot_id"], "summary": summary_of(fragment, "repairs"), "repairs": rows, "_audit_selection": selection}


def build_review(fragment: dict, journal: Journal, ctx: Context) -> dict:
    candidates = journal.candidates()
    decisions = journal.decisions()
    repairs = rs.by_id(journal.notes["repairs"]["repairs"], "candidate_id", "repairs")
    framed = journal.start["schema_version"] >= 7
    support = rs.candidate_support(journal.notes, journal.blocks, journal.start["snapshot_id"]) if framed else {}
    issues = []
    for n, key in enumerate([c for c in candidates if decisions[c]["status"] in rs.FINAL_STATUSES], 1):
        decision, repair, candidate = decisions[key], repairs[key], candidates[key]
        issue = {"id": f"W{n:02d}", "candidate_id": key, "decision_status": decision["status"], "category": decision["category"],
                 "anchor": candidate["anchor"], "diagnosis": decision["diagnosis"], "action": repair["action"],
                 "scientific_fidelity": repair["scientific_fidelity"], "severity": decision["severity"], "confidence": decision["confidence"],
                 "proposed_wording": repair["proposed_wording"]}
        if "close_criterion" in decision:
            issue["close_criterion"] = decision["close_criterion"]
        if framed:
            issue.update(support[key], initial_reader_question=candidate["question"])
        if repair.get("author_question") is not None:
            issue["author_question"] = repair["author_question"]
        evidence = []
        for anchor in [*candidate["evidence"], *decision["evidence"]]:
            if anchor not in evidence:
                evidence.append(anchor)
        if evidence:
            issue["evidence"] = evidence
        issues.append(issue)
    ledger = []
    for concept in journal.notes["checks"]["concepts"]:
        ledger.append({k: concept[k] for k in ("concept_id", "term", "first_mention", "earliest_usable_explanation", "first_critical_use", "assessment")})
    extra = {k: v for k, v in fragment.items() if k not in ("summary", "issues", "concept_ledger", "snapshot_id")}
    review = {"snapshot_id": journal.start["snapshot_id"], "summary": summary_of(fragment, "review"), "issues": issues, **extra}
    if review.get("depth") is None:
        review["depth"] = rs.derived_depth(journal.notes)
    if framed:
        review.update(review_frame(fragment, issues, journal))
    if ledger:
        review["concept_ledger"] = ledger
    return review


def review_frame(fragment: dict, issues: list[dict], journal: Journal) -> dict:
    """The frame of a schema 7 review: the fragment's scope_note, the fixed not_assessed entries plus the fragment's own, and the priority list.

    A priority_list in the fragment is taken as given (review_steps.check_frame then holds it
    to the rules); without one the default order is used.
    """
    scope_note = fragment.get("scope_note")
    if not isinstance(scope_note, str) or not scope_note.strip():
        raise BuildError("review fragments need scope_note: what was read (edition, sections or blocks, reader runs) and what was left out.")
    added = fragment.get("not_assessed", [])
    if not isinstance(added, list) or any(not isinstance(entry, str) or not entry.strip() for entry in added):
        raise BuildError("not_assessed in a review fragment must be a list of nonempty strings; the three fixed entries are inserted for you.")
    priority = fragment.get("priority_list")
    return {"scope_note": scope_note, "not_assessed": [*rs.NOT_ASSESSED, *(entry for entry in added if entry not in rs.NOT_ASSESSED)],
            "priority_list": rs.default_priority(issues, journal.blocks) if priority is None else priority}


# ---------------------------------------------------------------- report

CHECKPOINT_MODE_ORDER = ("fresh-prefix", "bounded-request", "masked-sequential", "full-context")
PRIORITY_QUOTE_CHARS = 60


def one_line(text: str) -> str:
    return " ".join(text.split())


def finding_block(issue: dict, question: str) -> list[str]:
    sources = issue["reader_sources"]
    lines = [f"### {issue['id']} · {issue['severity']} · confidence {issue['confidence']} · {issue['anchor']['block_id']}",
             *("> " + line for line in issue["anchor"]["quote"].split("\n")), "",
             f"- Reader's question: {one_line(question)}", f"- Diagnosis: {one_line(issue['diagnosis'])}", f"- Action: {one_line(issue['action'])}"]
    if issue.get("proposed_wording") is not None:
        lines.append(f"- Proposed wording: {one_line(issue['proposed_wording'])}")
    if issue.get("author_question") is not None:
        lines.append(f"- Question for the author: {one_line(issue['author_question'])}")
    raised = f"raised at {sources} reader checkpoint{'' if sources == 1 else 's'}" if sources else "raised by the editor's check, by no reader"
    lines += [f"- Meaning check: {one_line(issue['scientific_fidelity'])}", f"- Done when: {one_line(issue['close_criterion'])}",
              f"- Support: {raised}; challenger: {issue['challenge'] or 'not run'}."]
    return lines


def depth_line(depth: str) -> str:
    left_out = rs.DEPTHS[depth]
    return f"Depth: {depth}." + (f" Not done at this depth: {'; '.join(left_out)}." if left_out else "")


def records_line(notes: dict, index: dict) -> str:
    decisions = notes["decisions"]["decisions"]
    modes = [point["mode"] for point in notes["reconcile"]["checkpoints"]]
    by_mode = ", ".join(f"{modes.count(mode)} {mode}" for mode in CHECKPOINT_MODE_ORDER if mode in modes) or "none"
    reason = notes["decisions"].get("no_challenge_reason")
    if reason is not None:
        challenge = f"not run ({one_line(reason)})"
    else:
        challenge = f"run on all {len(decisions)} candidates" if decisions else "no candidates to challenge"
    counts = {status: sum(1 for row in decisions if row["status"] == status) for status in ("accepted", "needs_author", "merged", "rejected")}
    audits = sum(1 for row in notes["repairs"]["repairs"] if row.get("audit_path") is not None)
    return (f"Records: {len(notes['reading']['paragraphs'])} of {len(index['blocks'])} blocks read; reader checkpoints: {by_mode}; "
            f"candidates: {counts['accepted']} accepted, {counts['needs_author']} author question{'' if counts['needs_author'] == 1 else 's'}, {counts['merged']} merged, {counts['rejected']} rejected; "
            f"independent challenge: {challenge}; fidelity audits attached: {audits}.")


def render_report(review: dict, notes: dict, index: dict, appendix_name: str) -> tuple[str, str | None]:
    """The user-facing report of a checked schema 7 review, and the appendix of minor items (None when there is none).

    Deterministic and English; no model writes any of it. The reader's question of each finding
    is the recorded candidate's, so the report shows the question as it was first asked. Every
    finding that misleads or hinders appears once under Findings, the priority list first;
    minor items appear only in the appendix.
    """
    questions = {key: row["question"] for key, row in rs.all_candidates(notes).items()}
    by_candidate = {issue["candidate_id"]: issue for issue in review["issues"]}
    priority = [by_candidate[key] for key in review["priority_list"]]
    findings = priority + [issue for issue in review["issues"] if issue["severity"] != "minor" and issue["candidate_id"] not in review["priority_list"]]
    minor = [issue for issue in review["issues"] if issue["severity"] == "minor"]
    eligible = sum(1 for issue in review["issues"] if rs.eligible_for_priority(issue))
    lines = ["# Writing review", "", "## What was reviewed", review["scope_note"].strip(), "",
             "Not assessed: " + "; ".join(review["not_assessed"]) + ".", "",
             *([depth_line(review["depth"]), ""] if "depth" in review else []), records_line(notes, index), "", "## Priority list"]
    for number, issue in enumerate(priority, 1):
        quote = one_line(issue["anchor"]["quote"])
        shown = quote if len(quote) <= PRIORITY_QUOTE_CHARS else quote[:PRIORITY_QUOTE_CHARS] + "…"
        lines.append(f"{number}. [{issue['severity']}] {issue['anchor']['block_id']} \"{shown}\" — {one_line(questions[issue['candidate_id']])} ({issue['id']})")
    if not priority:
        lines.append("No finding that misleads or hinders was accepted.")
    elif eligible > len(priority):
        lines += ["", f"The list shows the {len(priority)} most consequential of {eligible}; all are under Findings."]
    asked = [issue for issue in findings + minor if issue.get("author_question") is not None]
    if asked:
        lines += ["", "## Questions for the author", *(f"- {issue['id']}, {issue['anchor']['block_id']}: {one_line(issue['author_question'])}" for issue in asked)]
    lines += ["", "## Findings"]
    for issue in findings:
        lines += [*finding_block(issue, questions[issue["candidate_id"]]), ""]
    if not findings:
        lines += ["None.", ""]
    if not minor:
        return "\n".join(lines), None
    lines += ["## Minor items", (f"1 minor item does not hinder understanding; it is listed in {appendix_name}." if len(minor) == 1
                                 else f"{len(minor)} minor items do not hinder understanding; they are listed in {appendix_name}."), ""]
    appendix = ["# Writing review: minor items", ""]
    for issue in minor:
        appendix += [*finding_block(issue, questions[issue["candidate_id"]]), ""]
    return "\n".join(lines), "\n".join(appendix)


def write_text_new(path: Path, text: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise Error(f"Output already exists: {path}; choose a new path.", "output_exists") from exc


def report(journal_path: Path, index_path: Path, review_path: Path, out: Path, appendix: Path | None) -> dict:
    """Render report.md from a review that passes check-final; a review that fails it is not rendered."""
    appendix = appendix or out.with_name(out.stem + "-minor.md")
    if appendix.resolve() == out.resolve():
        raise BuildError("--appendix must be a different file from --out.")
    index = rp.load_json(index_path)
    review = rp.load_json(review_path)
    rs.check_final(journal_path, index, review)
    start, notes, _ = rs.load_run(journal_path, index)
    if start["schema_version"] < 7:
        raise BuildError(f"notes_build.py report renders the review of a schema 7 journal; this journal is schema {start['schema_version']}, "
                         "whose review has no scope note or priority list. Start a new run to get a rendered report.")
    text, minor = render_report(review, notes, index, appendix.name)
    for path in (out, appendix) if minor is not None else (out,):
        if os.path.lexists(path):
            raise Error(f"Output already exists: {path}; choose a new path.", "output_exists")
    write_text_new(out, text)
    if minor is not None:
        write_text_new(appendix, minor)
    severities = [issue["severity"] for issue in review["issues"]]
    return {"ok": True, "command": "report", "output": str(out), "appendix": str(appendix) if minor is not None else None, "journal": str(journal_path),
            "counts": {"priority_list": len(review["priority_list"]), "findings": len(severities) - severities.count("minor"), "minor": severities.count("minor")},
            "next": "tell the user, in their language, where the report and the records are; do not add sections to the report"}


# ---------------------------------------------------------------- driver


def section_ends(journal_path: Path, index_path: Path, out: Path, max_level: int | None, mode: str) -> dict:
    """Write the fixed prefix schedule as a checks fragment: one reader at the end of every section of the scope."""
    if os.path.lexists(out):
        raise Error(f"Output already exists: {out}; choose a new path.", "output_exists")
    if max_level is not None and max_level < 1:
        raise BuildError("--max-level must be at least 1 (1 ends a section only at a top-level heading).")
    index = rp.load_json(index_path)
    journal = Journal(journal_path, index)
    if "checks" in journal.notes:
        raise BuildError("The journal already records the checks stage, so its checkpoint plan is fixed. section-ends writes a fragment for a checks stage that is still to be built; "
                         "to change the plan, start a corrected run (review_steps.py corrected-run --restart-at checks).")
    rows = rp.section_ends(index, included=journal.included, max_level=max_level)
    if not rows:
        raise BuildError("No section end was found: the scope includes no body block (text or table) before a heading or the end of the text.")
    plan, sections = [], []
    for number, row in enumerate(rows, 1):
        block = journal.blocks[row["block_id"]]
        where = "end of the text before the first heading" if row["heading"] is None else f'end of the section "{row["heading"]}"'
        plan.append({"id": f"S{number:02d}", "anchor": {"block_id": block["block_id"], "quote": rp.closing_quote(block["text"])}, "mode": mode, "reason": f"Fixed schedule: {where}."})
        sections.append({"id": f"S{number:02d}", "block_id": block["block_id"], "heading": row["heading"], "words_seen": len(index["text"][:block["end_offset"]].split())})
    rp.write_json_atomic(out, {"checkpoint_plan": plan})
    return {"ok": True, "command": "section-ends", "output": str(out), "journal": str(journal_path), "entries": len(plan), "sections": sections,
            "next": f"give {out} to notes_build.py checks as one more --source (delete the entries you do not want first); after the checks stage is recorded, dispatch_reader.py prefix-batch calls the readers"}


def build(stage: str, journal_path: Path, index_path: Path, sources: list[str], out: Path, audits_dir: Path | None, compare_dir: Path | None, challenges: list[str] | None = None,
          no_wording: bool = False) -> dict:
    if stage not in STAGES:
        raise BuildError("stage must be one of: " + ", ".join(STAGES) + ".")
    if no_wording and stage != "repairs":
        raise BuildError("--no-wording applies to the repairs stage only.")
    if os.path.lexists(out):
        raise Error(f"Output already exists: {out}; choose a new path.", "output_exists")
    index = rp.load_json(index_path)
    journal = Journal(journal_path, index)
    journal.require(stage)
    ctx = Context(index, journal.included)
    fragment = merge(load_fragments(sources, ctx))
    if ctx.failures:
        raise BuildError("ctx.q() recorded quotes that are not exact text: " + "; ".join(f"{b}: {q!r}" for b, q in ctx.failures[:12]) + ("; ..." if len(ctx.failures) > 12 else ""))
    if stage == "reading":
        value = build_reading(fragment, journal, ctx)
    elif stage == "checks":
        value = build_checks(fragment, journal, ctx)
    elif stage == "reconcile":
        value = build_reconcile(fragment, journal, ctx)
    elif stage == "decisions":
        value = build_decisions(fragment, journal, ctx, challenges)
    elif stage == "repairs":
        value = build_repairs(fragment, journal, ctx, audits_dir, compare_dir, no_wording)
    else:
        value = build_review(fragment, journal, ctx)
    selection = value.pop("_audit_selection", None)
    failures = anchor_failures(value, journal.blocks)
    if failures:
        raise BuildError(f"{len(failures)} quotation(s) are not exact text of the index: " + "; ".join(failures[:12]) + ("; ..." if len(failures) > 12 else ""))
    if stage == "review":
        framed = journal.start["schema_version"] >= 7
        rp.check_review(index, value, english=framed)
        if framed:
            rs.check_frame(value)
        rs.check_depth(value, journal.notes)
        counts = {"issues": len(value["issues"]), "accepted": sum(1 for i in value["issues"] if i["decision_status"] == "accepted"),
                  "needs_author": sum(1 for i in value["issues"] if i["decision_status"] == "needs_author"), "concept_ledger": len(value.get("concept_ledger", []))}
    else:
        rs.validate_notes(stage, value, journal.start, index, journal.notes)
        counts = {k: len(v) for k, v in value.items() if isinstance(v, list)}
    rp.write_json_atomic(out, value)
    result = {"ok": True, "command": stage, "output": str(out), "journal": str(journal_path), "counts": counts,
              "checked": ["every quotation is exact text of its block", "coverage of the included blocks (reading, checks)",
                          "field vocabularies and record shape through review_steps.validate_notes" if stage != "review"
                          else "final review schema through review_packets.check_review, the scope note, not-assessed list, and priority list through review_steps.check_frame (schema 7 journals), "
                               "and the depth through review_steps.check_depth",
                          "needs_author repairs carry a question", "edit wording taken from the latest audit only when it found the meaning preserved"]}
    if selection is not None:
        result["audit_selection"] = selection
    if stage == "review":
        result["next"] = "run review_steps.py ready, check-final with this file, then notes_build.py report"
    else:
        result["next"] = f"review_steps.py record --stage {stage} --notes {out}"
    return result


def parser() -> argparse.ArgumentParser:
    p = rp.JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=(*STAGES, "report", "section-ends"))
    p.add_argument("--journal", required=True, help="The review_steps journal (00-start.json and the stages recorded so far).")
    p.add_argument("--index", required=True)
    p.add_argument("--source", action="append", help="A fragment: .json, .py (FRAGMENT or build(ctx)), or a directory of them; repeatable, merged in order. Required for every stage; not used by report and section-ends.")
    p.add_argument("--out", required=True, help="The notes file to write, report.md for report, or the plan fragment for section-ends (new path).")
    p.add_argument("--review", help="report: the review.json that passed check-final; it is checked again before anything is rendered.")
    p.add_argument("--appendix", help="report: where the minor items go when there are any (default: <out stem>-minor.md beside --out).")
    p.add_argument("--audits", help="repairs: directory of fidelity audits named <cid>.audit.json, <cid>-2.audit.json, ...; the latest round of each candidate decides its wording.")
    p.add_argument("--compare", help="repairs: directory of <cid>.features.json compare results (default: --audits).")
    p.add_argument("--no-wording", action="store_true", help="repairs: build the repairs of a quick review. Every edit records its action with proposed_wording null and a fixed wording_withheld_reason; "
                                                             "--audits and --compare are not read; a fragment that gives proposed_wording is refused.")
    p.add_argument("--max-level", type=int, help="section-ends: only headings of this level or higher end a section (2: # and ##). Default: every heading.")
    p.add_argument("--mode", choices=sorted(rs.PREFIX_MODES), default="fresh-prefix", help="section-ends: the mode of the planned readers (default fresh-prefix).")
    p.add_argument("--challenge", action="append", help="decisions: a challenge file from dispatch_reader.py challenge; repeatable. Each decision gets the challenge_path of the file that holds its candidate.")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.stage == "report":
            if not args.review:
                raise Error("report needs --review PATH: the review.json that passed check-final.", "cli_error")
            result = report(Path(args.journal), Path(args.index), Path(args.review), Path(args.out), Path(args.appendix) if args.appendix else None)
        elif args.stage == "section-ends":
            result = section_ends(Path(args.journal), Path(args.index), Path(args.out), args.max_level, args.mode)
        elif not args.source:
            raise Error(f"{args.stage} needs --source: a fragment file or a directory of fragments.", "cli_error")
        else:
            result = build(args.stage, Path(args.journal), Path(args.index), args.source, Path(args.out), Path(args.audits) if args.audits else None, Path(args.compare) if args.compare else None, args.challenge,
                           args.no_wording)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Error as exc:
        error = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        error = {"ok": False, "error": {"code": "input_output_error", "message": f"{type(exc).__name__}: {exc}"}}
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
