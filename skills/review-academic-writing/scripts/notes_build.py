#!/usr/bin/env python3
"""Build the six review records from fragments, with the checks a hand-written builder forgets.

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
             audit of the candidate decides whether its wording is recorded
  review     the final review from the five recorded stages and a summary

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


def expand_prefix_checkpoint(point: dict, plan: dict) -> dict:
    """A reconcile checkpoint given as {id, plan_id?, events_dir, label?, limitation, result?} -> the full entry."""
    events = Path(point["events_dir"])
    label = point.get("label") or point.get("plan_id") or point["id"]
    packet_path = events / f"{label}.packet.json"
    observation_path = events / f"{label}.observation.json"
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


def build_decisions(fragment: dict, journal: Journal, ctx: Context) -> dict:
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
    rows = []
    for key in candidates:
        row = dict(decisions[key])
        row["candidate_id"] = key
        row.setdefault("evidence", [])
        if row.get("status") in rs.FINAL_STATUSES:
            lacking = [f for f in ("category", "diagnosis", "severity", "confidence") if not row.get(f)]
            if lacking:
                raise BuildError(f"Decision {key} ({row['status']}) lacks {', '.join(lacking)}.")
        rows.append(row)
    return {"snapshot_id": journal.start["snapshot_id"], "summary": summary_of(fragment, "decisions"), "decisions": rows}


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


def build_repairs(fragment: dict, journal: Journal, ctx: Context, audits_dir: Path | None, compare_dir: Path | None) -> dict:
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
    audits = audits_by_candidate(audits_dir)
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
    issues = []
    for n, key in enumerate([c for c in candidates if decisions[c]["status"] in rs.FINAL_STATUSES], 1):
        decision, repair, candidate = decisions[key], repairs[key], candidates[key]
        issue = {"id": f"W{n:02d}", "candidate_id": key, "decision_status": decision["status"], "category": decision["category"],
                 "anchor": candidate["anchor"], "diagnosis": decision["diagnosis"], "action": repair["action"],
                 "scientific_fidelity": repair["scientific_fidelity"], "severity": decision["severity"], "confidence": decision["confidence"],
                 "proposed_wording": repair["proposed_wording"]}
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
    if ledger:
        review["concept_ledger"] = ledger
    return review


# ---------------------------------------------------------------- driver


def build(stage: str, journal_path: Path, index_path: Path, sources: list[str], out: Path, audits_dir: Path | None, compare_dir: Path | None) -> dict:
    if stage not in STAGES:
        raise BuildError("stage must be one of: " + ", ".join(STAGES) + ".")
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
        value = build_decisions(fragment, journal, ctx)
    elif stage == "repairs":
        value = build_repairs(fragment, journal, ctx, audits_dir, compare_dir)
    else:
        value = build_review(fragment, journal, ctx)
    selection = value.pop("_audit_selection", None)
    failures = anchor_failures(value, journal.blocks)
    if failures:
        raise BuildError(f"{len(failures)} quotation(s) are not exact text of the index: " + "; ".join(failures[:12]) + ("; ..." if len(failures) > 12 else ""))
    if stage == "review":
        rp.check_review(index, value)
        counts = {"issues": len(value["issues"]), "accepted": sum(1 for i in value["issues"] if i["decision_status"] == "accepted"),
                  "needs_author": sum(1 for i in value["issues"] if i["decision_status"] == "needs_author"), "concept_ledger": len(value.get("concept_ledger", []))}
    else:
        rs.validate_notes(stage, value, journal.start, index, journal.notes)
        counts = {k: len(v) for k, v in value.items() if isinstance(v, list)}
    rp.write_json_atomic(out, value)
    result = {"ok": True, "command": stage, "output": str(out), "journal": str(journal_path), "counts": counts,
              "checked": ["every quotation is exact text of its block", "coverage of the included blocks (reading, checks)",
                          "field vocabularies and record shape through review_steps.validate_notes" if stage != "review" else "final review schema through review_packets.check_review",
                          "needs_author repairs carry a question", "edit wording taken from the latest audit only when it found the meaning preserved"]}
    if selection is not None:
        result["audit_selection"] = selection
    if stage == "review":
        result["next"] = "run review_steps.py ready, then check-final with this file"
    else:
        result["next"] = f"review_steps.py record --stage {stage} --notes {out}"
    return result


def parser() -> argparse.ArgumentParser:
    p = rp.JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--journal", required=True, help="The review_steps journal (00-start.json and the stages recorded so far).")
    p.add_argument("--index", required=True)
    p.add_argument("--source", required=True, action="append", help="A fragment: .json, .py (FRAGMENT or build(ctx)), or a directory of them; repeatable, merged in order.")
    p.add_argument("--out", required=True, help="The notes file to write (new path).")
    p.add_argument("--audits", help="repairs: directory of fidelity audits named <cid>.audit.json, <cid>-2.audit.json, ...; the latest round of each candidate decides its wording.")
    p.add_argument("--compare", help="repairs: directory of <cid>.features.json compare results (default: --audits).")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        result = build(args.stage, Path(args.journal), Path(args.index), args.source, Path(args.out), Path(args.audits) if args.audits else None, Path(args.compare) if args.compare else None)
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
