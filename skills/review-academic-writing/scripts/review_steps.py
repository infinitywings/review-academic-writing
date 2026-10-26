#!/usr/bin/env python3
"""Save ordered, source-anchored review notes before drafting a final review.

Standard library only. Receipts check saved-file consistency and permitted step
order. They are not signed, tamper-proof evidence of cognitive chronology, note
quality, scientific validity, context isolation, or human comprehension. A caller
can still write prose outside this workflow; the skill must instruct it not to.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

# Running the helper should not create caches in the installed skill directory.
sys.dont_write_bytecode = True
# The helpers import each other by module name; isolated mode and symlinked installs leave this directory off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import masked_reader as mr
import review_packets as rp

STAGES = ("reading", "checks", "reconcile", "decisions", "repairs")
JOURNAL_SCHEMA = 3
FIGURE_POLICIES = {"strict-prefix", "reference-jump", "text-only"}
AUDIT_VERDICTS = {"preserved", "changed", "undetermined"}
AUDIT_CHECKS = ("claim_strength", "quantifiers", "assumptions", "population", "measured_outcome", "time_horizon", "numerical_values", "uncertainty", "causal_vs_associative")
AUDIT_CHECK_VALUES = {"same", "changed", "not_applicable"}
READY = f"{len(STAGES) + 1:02d}-ready.json"
FINAL_STATUSES = {"accepted", "needs_author"}
PREFIX_MODES = {"fresh-prefix", "bounded-request"}
CHECKPOINT_MODES = {"full-context", "masked-sequential"} | PREFIX_MODES
READER_EVIDENCE_KEYS = ("checkpoints", "branches", "observations")
OBSERVATION_KINDS = {"absent_at_critical_use", "resolved_later", "needed_again", "lost_in_notes",
                     "interpretation_changed", "inferred_connection", "asset_lookup", "adequate"}
LIMITS = [
    "note substance and diagnostic/scientific correctness",
    "whether prose was drafted outside this workflow or notes were backfilled",
    "coordinated modification of receipts, notes, and hashes",
    "runtime context isolation and human comprehension",
]
Error = rp.ReviewDataError


def obj(value: Any, label: str) -> dict:
    return rp._object(value, label)


def string(value: Any, label: str) -> str:
    return rp._nonempty_string(value, label)


def array(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise Error(f"{label} must be an array.")
    return value


def strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    result = array(value, label)
    for number, item in enumerate(result):
        string(item, f"{label}[{number}]")
    if nonempty and not result:
        raise Error(f"{label} must not be empty.")
    return result


def fields(value: dict, required: set[str], label: str) -> None:
    rp._keys(value, required, set(), label, allow_extra=True)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=rp._unique_json_object,
                      parse_constant=rp._reject_json_constant)


def read_raw(path: Path) -> bytes:
    if path.is_symlink():
        raise Error(f"Saved journal file must not be a symbolic link: {path.name}.")
    return path.read_bytes()


def write_raw(path: Path, raw: bytes) -> None:
    if os.path.lexists(path):
        raise Error(f"Output already exists: {path.name}; use a new run.", "output_exists")
    fd, tmp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise Error(f"Output already exists: {path.name}; use a new run.", "output_exists") from exc
    finally:
        os.unlink(tmp)


def sealed(payload: dict) -> dict:
    return {**payload, "receipt_sha256": rp._canonical_hash(payload)}


def verify_receipt(value: Any, label: str) -> dict:
    value = obj(value, label)
    if value.get("receipt_sha256") != rp._canonical_hash({k: v for k, v in value.items() if k != "receipt_sha256"}):
        raise Error(f"{label} checksum mismatch; preserve this run and start a new run.")
    return value


def validate_scope(scope: Any, index: dict) -> dict:
    scope = obj(scope, "scope")
    fields(scope, {"task", "included_block_ids", "omissions", "reading_path", "extraction_notes"}, "scope")
    rp._enum(scope["task"], {"review", "structure_plan", "revision", "quick_review"}, "scope.task")
    for key in ("reading_path", "extraction_notes"):
        string(scope[key], "scope." + key)
    included = strings(scope["included_block_ids"], "scope.included_block_ids", nonempty=True)
    omitted = []
    for n, row in enumerate(array(scope["omissions"], "scope.omissions")):
        row = obj(row, f"scope.omissions[{n}]")
        fields(row, {"block_id", "reason"}, f"scope.omissions[{n}]")
        omitted.append(string(row["block_id"], "omission.block_id"))
        string(row["reason"], "omission.reason")
    combined = included + omitted
    all_ids = {b["block_id"] for b in index["blocks"]}
    if len(combined) != len(set(combined)) or set(combined) != all_ids:
        raise Error("Scope must assign every indexed block exactly once: included or omitted with a reason.")
    return scope


def scoped_anchor(value: Any, blocks: dict, included: set, label: str) -> None:
    rp._anchor(value, blocks, label)
    if value["block_id"] not in included:
        raise Error(f"{label} is outside the declared review scope.")


def evidence(value: Any, blocks: dict, label: str) -> list:
    value = array(value, label)
    rp._evidence(value, blocks, label)
    return value


def coverage(ids: list, included: set, label: str) -> None:
    if len(ids) != len(set(ids)) or set(ids) != included:
        raise Error(f"{label} must cover each included block exactly once.")


def by_id(rows: list, key: str, label: str) -> dict:
    result = {}
    for n, value in enumerate(rows):
        value = obj(value, f"{label}[{n}]")
        name = string(value.get(key), f"{label}[{n}].{key}")
        if name in result:
            raise Error(f"Duplicate {label} identifier: {name}.")
        result[name] = value
    return result


def prefix_checkpoint(point: dict, start: dict, index: dict) -> list[dict]:
    """Bind a checkpoint to a frozen prefix packet and observation made from this source."""
    packet_path = Path(string(point.get("packet_path"), "checkpoint.packet_path")).resolve()
    observation_path = Path(string(point.get("observation_path"), "checkpoint.observation_path")).resolve()
    packet_raw, observation_raw = read_raw(packet_path), read_raw(observation_path)
    packet = rp.validate_packet(parse(packet_raw))
    rp.check_observation(packet, parse(observation_raw))
    if packet["snapshot_id"] != index["snapshot_id"] or packet["visible_text"] != index["text"][:packet["stop_offset"]]:
        raise Error("Checkpoint packet is not an exact prefix of this journal's source.")
    if rp._canonical_hash(packet["profile"]) != start["profile_sha256"]:
        raise Error("Checkpoint packet uses a different reader profile.")
    rp._anchor(point["anchor"], {b["block_id"]: b for b in packet["blocks"]}, "checkpoint visible anchor")
    return [{"path": str(path), "sha256": digest(raw)} for path, raw in ((packet_path, packet_raw), (observation_path, observation_raw))]


def load_masked_run(run_path: Path, index: dict) -> tuple:
    """Replay a masked_reader run and require its case to be text of this source."""
    try:
        loaded = mr.load_run(run_path)
    except (mr.Invalid, KeyError, TypeError) as exc:
        raise Error(f"Masked run does not validate: {exc}") from exc
    for block in loaded[2]["blocks"]:
        if rp.normalize_text(block["text"]) not in index["text"]:
            raise Error(f"Masked case block {block['id']} is not text of this journal's source.")
    return loaded


def masked_checkpoint(point: dict, index: dict, blocks: dict) -> list[dict]:
    """Bind a checkpoint to one display event of a masked_reader run whose case is text of this source."""
    run_path = Path(string(point.get("run_path"), "checkpoint.run_path")).resolve()
    sequence = rp._integer(point.get("record_sequence"), "checkpoint.record_sequence")
    run, _manifest, case, _profile, _state, next_sequence, _ = load_masked_run(run_path, index)
    if not 1 <= sequence < next_sequence:
        raise Error(f"checkpoint.record_sequence must name a recorded display event, 1 to {next_sequence - 1}.")
    if point["figure_policy"] != case["asset_policy"]:
        raise Error(f"checkpoint.figure_policy must equal the masked run's asset_policy ({case['asset_policy']}).")
    text = {block["id"]: rp.normalize_text(block["text"]) for block in case["blocks"]}
    record = run / "records" / f"{sequence:06d}"
    packet = parse(read_raw(record / "packet.json"))
    reachable = {m["id"] for m in packet["materials"]} | set(packet["available_lookup_ids"])
    anchor = blocks[point["anchor"]["block_id"]]
    quote = point["anchor"]["quote"]

    def seen_within_anchor(block_text: str) -> bool:
        position = index["text"].find(block_text)
        while position >= 0:
            if position < anchor["end_offset"] and position + len(block_text) > anchor["start_offset"]:
                return True
            position = index["text"].find(block_text, position + 1)
        return False

    if not any(quote in text[i] and seen_within_anchor(text[i]) for i in reachable if i in text):
        raise Error("Checkpoint anchor is not text the masked reader had seen by that display event.")
    files = [run / "manifest.json"] + [record / name for name in ("packet.json", "response.json", "receipt.json")]
    return [{"path": str(path), "sha256": digest(read_raw(path))} for path in files]


def branch_attachment(row: dict, index: dict) -> list[dict]:
    """Bind a reconciled observation to one recorded branch of a masked_reader run."""
    run_path = Path(string(row.get("run_path"), "branch.run_path")).resolve()
    sequence = rp._integer(row.get("branch_sequence"), "branch.branch_sequence")
    run, manifest, _case, profile, state, _next, _ = load_masked_run(run_path, index)
    try:
        branches = mr.load_branches(run, manifest, profile, state)
    except (mr.Invalid, KeyError, TypeError) as exc:
        raise Error(f"Masked run branches do not validate: {exc}") from exc
    if not 1 <= sequence <= len(branches):
        raise Error(f"branch.branch_sequence must name a recorded branch, 1 to {len(branches)}.")
    record = run / "branches" / f"{sequence:06d}"
    files = [run / "manifest.json"] + [record / name for name in ("packet.json", "response.json", "receipt.json")]
    return [{"path": str(path), "sha256": digest(read_raw(path))} for path in files]


def validate_candidates(value: dict, blocks: dict, included: set, stage: str) -> dict:
    candidates = by_id(array(value.get("candidates"), stage + ".candidates"), "id", "candidates")
    for row in candidates.values():
        fields(row, {"anchor", "question", "evidence"}, "candidate")
        scoped_anchor(row["anchor"], blocks, included, "candidate.anchor")
        string(row["question"], "candidate.question")
        evidence(row["evidence"], blocks, "candidate.evidence")
    return candidates


def all_candidates(notes: dict) -> dict:
    return by_id(notes["checks"]["candidates"] + notes["reconcile"]["candidates"], "id", "candidates")


def validate_notes(stage: str, value: Any, start: dict, index: dict, previous: dict) -> list[dict]:
    """Validate observable records, not whether the natural-language checks are good."""
    value = obj(value, stage + " notes")
    fields(value, {"snapshot_id", "summary"}, stage + " notes")
    if value["snapshot_id"] != start["snapshot_id"]:
        raise Error("Notes snapshot_id does not match this journal's source.")
    string(value["summary"], stage + ".summary")
    blocks = {b["block_id"]: b for b in index["blocks"]}
    included = set(start["scope"]["included_block_ids"])
    attachments = []
    if stage == "reading":
        rows = array(value.get("paragraphs"), "reading.paragraphs")
        seen = []
        for n, row in enumerate(rows):
            row = obj(row, f"paragraphs[{n}]")
            fields(row, {"anchor", "paraphrase", "role", "required_context", "new_information", "open_questions"}, "paragraph")
            scoped_anchor(row["anchor"], blocks, included, "paragraph.anchor")
            seen.append(row["anchor"]["block_id"])
            for key in ("paraphrase", "role"):
                string(row[key], "paragraph." + key)
            for key in ("required_context", "new_information", "open_questions"):
                strings(row[key], "paragraph." + key)
        coverage(seen, included, "Reading notes")
    elif stage == "checks":
        concepts = array(value.get("concepts"), "checks.concepts")
        if not concepts:
            string(value.get("no_concepts_reason"), "checks.no_concepts_reason")
        by_id(concepts, "concept_id", "concepts")
        for row in concepts:
            fields(row, {"term", "first_mention", "earliest_usable_explanation", "first_critical_use", "assessment"}, "concept")
            for key in ("term", "assessment"):
                string(row[key], "concept." + key)
            for key in ("first_mention", "earliest_usable_explanation", "first_critical_use"):
                if row[key] is not None:
                    rp._anchor(row[key], blocks, "concept." + key)
        rows = array(value.get("passages"), "checks.passages")
        seen = []
        for row in rows:
            row = obj(row, "passage")
            fields(row, {"block_id", "structure_check", "wording_check", "evidence"}, "passage")
            seen.append(string(row["block_id"], "passage.block_id"))
            for key in ("structure_check", "wording_check"):
                string(row[key], "passage." + key)
            ev = evidence(row["evidence"], blocks, "passage.evidence")
            if not any(a["block_id"] == row["block_id"] for a in ev):
                raise Error("Each passage check needs an exact quotation from that passage.")
        coverage(seen, included, "Structure and wording checks")
        if any(key in value for key in READER_EVIDENCE_KEYS):
            raise Error("Reader observations belong to the reconcile stage; the checks stage is the editor's independent review.")
        plan = by_id(array(value.get("checkpoint_plan"), "checks.checkpoint_plan"), "id", "checkpoint_plan")
        for row in plan.values():
            fields(row, {"anchor", "mode", "reason"}, "checkpoint_plan")
            scoped_anchor(row["anchor"], blocks, included, "checkpoint_plan.anchor")
            rp._enum(row["mode"], CHECKPOINT_MODES, "checkpoint_plan.mode")
            string(row["reason"], "checkpoint_plan.reason")
        validate_candidates(value, blocks, included, "checks")
    elif stage == "reconcile":
        plan = by_id(previous["checks"]["checkpoint_plan"], "id", "checkpoint_plan")
        points = by_id(array(value.get("checkpoints"), "reconcile.checkpoints"), "id", "checkpoints")
        if not points:
            string(value.get("no_checkpoints_reason"), "reconcile.no_checkpoints_reason")
        for point in points.values():
            fields(point, {"anchor", "mode", "result", "limitation", "figure_policy"}, "checkpoint")
            scoped_anchor(point["anchor"], blocks, included, "checkpoint.anchor")
            rp._enum(point["mode"], CHECKPOINT_MODES, "checkpoint.mode")
            rp._enum(point["figure_policy"], FIGURE_POLICIES, "checkpoint.figure_policy")
            if point["mode"] in PREFIX_MODES and point["figure_policy"] != "text-only":
                raise Error("A prefix checkpoint carries no figures; its figure_policy must be text-only.")
            string(point["result"], "checkpoint.result")
            string(point["limitation"], "checkpoint.limitation")
            if point["mode"] in PREFIX_MODES or point.get("plan_id") is not None:
                planned = plan.get(string(point.get("plan_id"), "checkpoint.plan_id"))
                if planned is None or planned["anchor"] != point["anchor"] or planned["mode"] != point["mode"]:
                    raise Error("A prefix checkpoint must cite a checks-stage plan entry with the same anchor and mode.")
            if point["mode"] in PREFIX_MODES:
                attachments.extend(prefix_checkpoint(point, start, index))
            elif point["mode"] == "masked-sequential":
                attachments.extend(masked_checkpoint(point, index, blocks))
        branches = by_id(array(value.get("branches"), "reconcile.branches"), "id", "branches")
        if points.keys() & branches.keys():
            raise Error("Checkpoint and branch ids must not collide.")
        for row in branches.values():
            fields(row, {"run_path", "branch_sequence", "result", "limitation"}, "branch")
            string(row["result"], "branch.result")
            string(row["limitation"], "branch.limitation")
            attachments.extend(branch_attachment(row, index))
        earlier = by_id(previous["checks"]["candidates"], "id", "candidates")
        candidates = validate_candidates(value, blocks, included, "reconcile")
        if candidates.keys() & earlier.keys():
            raise Error("Reconcile candidates must use ids not already used in checks.")
        for row in by_id(array(value.get("observations"), "reconcile.observations"), "id", "observations").values():
            fields(row, {"source_id", "reader_question", "classification", "editor_check", "evidence", "candidate_id"}, "observation")
            if string(row["source_id"], "observation.source_id") not in points.keys() | branches.keys():
                raise Error("observation.source_id must name a checkpoint or branch recorded in this stage.")
            string(row["reader_question"], "observation.reader_question")
            rp._enum(row["classification"], OBSERVATION_KINDS, "observation.classification")
            string(row["editor_check"], "observation.editor_check")
            evidence(row["evidence"], blocks, "observation.evidence")
            if row["candidate_id"] is not None and string(row["candidate_id"], "observation.candidate_id") not in earlier.keys() | candidates.keys():
                raise Error("observation.candidate_id must name a candidate from checks or this stage.")
    elif stage == "decisions":
        candidates = all_candidates(previous)
        decisions = by_id(array(value.get("decisions"), "decisions.decisions"), "candidate_id", "decisions")
        if decisions.keys() != candidates.keys():
            raise Error("Decisions must account for every candidate exactly once, including rejected candidates.")
        for row in decisions.values():
            fields(row, {"status", "verification", "countercheck", "evidence"}, "decision")
            rp._enum(row["status"], FINAL_STATUSES | {"rejected", "merged"}, "decision.status")
            for key in ("verification", "countercheck"):
                string(row[key], "decision." + key)
            evidence(row["evidence"], blocks, "decision.evidence")
            if row["status"] in FINAL_STATUSES:
                fields(row, {"category", "diagnosis", "severity", "confidence"}, "accepted decision")
                rp._enum(row["category"], rp.REVIEW_CATEGORIES, "decision.category")
                rp._enum(row["severity"], rp.SEVERITIES, "decision.severity")
                rp._enum(row["confidence"], rp.CONFIDENCES, "decision.confidence")
                string(row["diagnosis"], "decision.diagnosis")
            elif row["status"] == "merged":
                target = string(row.get("merged_into"), "decision.merged_into")
                if target not in decisions or not isinstance(decisions[target].get("status"), str) or decisions[target]["status"] not in FINAL_STATUSES:
                    raise Error("A merged candidate must point directly to an accepted or needs_author candidate; no chains or cycles.")
    elif stage == "repairs":
        decisions = by_id(previous["decisions"]["decisions"], "candidate_id", "decisions")
        wanted = {key for key, row in decisions.items() if row["status"] in FINAL_STATUSES}
        repairs = by_id(array(value.get("repairs"), "repairs.repairs"), "candidate_id", "repairs")
        if set(repairs) != wanted:
            raise Error("Repairs must correspond exactly to accepted and needs_author candidates.")
        for key, row in repairs.items():
            fields(row, {"kind", "action", "proposed_wording", "scientific_fidelity", "new_terms_check", "affected_block_ids", "author_question"}, "repair")
            rp._enum(row["kind"], {"edit", "move", "author_question"}, "repair.kind")
            for field in ("action", "scientific_fidelity", "new_terms_check"):
                string(row[field], "repair." + field)
            for field in ("proposed_wording", "author_question"):
                if row[field] is not None:
                    string(row[field], "repair." + field)
            affected = strings(row["affected_block_ids"], "repair.affected_block_ids", nonempty=True)
            if len(affected) != len(set(affected)) or not set(affected) <= blocks.keys():
                raise Error("Affected block IDs must be distinct blocks from this source.")
            if decisions[key]["status"] == "needs_author" and (row["kind"] != "author_question" or row["proposed_wording"] is not None):
                raise Error("needs_author requires an author question, not a definitive replacement.")
            if row["kind"] == "author_question":
                string(row["author_question"], "repair.author_question")
            if row["proposed_wording"] is None:
                if row.get("audit_path") is not None:
                    raise Error("A repair without proposed wording has nothing to audit; leave audit_path null.")
            else:
                attachments.extend(fidelity_audit(row, key, blocks))
    return attachments


def fidelity_audit(row: dict, candidate_id: str, blocks: dict) -> list[dict]:
    """Bind an edit to an independent fidelity audit that found the scientific meaning preserved."""
    path = Path(string(row.get("audit_path"), "repair.audit_path")).resolve()
    raw = read_raw(path)
    audit = obj(parse(raw), "fidelity audit")
    rp._keys(audit, {"candidate_id", "proposed_wording", "original", "verdict", "checks", "notes", "auditor"}, set(), "fidelity audit")
    if audit["candidate_id"] != candidate_id:
        raise Error(f"Fidelity audit at {path.name} is for {audit['candidate_id']!r}, not {candidate_id}.")
    if audit["proposed_wording"] != row["proposed_wording"]:
        raise Error(f"Fidelity audit for {candidate_id} audited different wording than the repair proposes.")
    original = array(audit["original"], "audit.original")
    if not original:
        raise Error("audit.original must quote the source text the wording replaces.")
    for n, anchor in enumerate(original):
        rp._anchor(anchor, blocks, f"audit.original[{n}]")
        if anchor["block_id"] not in row["affected_block_ids"]:
            raise Error(f"audit.original[{n}] quotes a block outside the repair's affected_block_ids.")
    rp._enum(audit["verdict"], AUDIT_VERDICTS, "audit.verdict")
    checks = obj(audit["checks"], "audit.checks")
    rp._keys(checks, set(AUDIT_CHECKS), set(), "audit.checks")
    for name in AUDIT_CHECKS:
        rp._enum(checks[name], AUDIT_CHECK_VALUES, "audit.checks." + name)
    string(audit["notes"], "audit.notes")
    auditor = obj(audit["auditor"], "audit.auditor")
    rp._keys(auditor, {"mode", "agent_id"}, set(), "audit.auditor")
    rp._enum(auditor["mode"], {"bounded-request", "fresh-prefix", "full-context"}, "audit.auditor.mode")
    string(auditor["agent_id"], "audit.auditor.agent_id")
    changed = [name for name in AUDIT_CHECKS if checks[name] == "changed"]
    if audit["verdict"] != "preserved" or changed:
        raise Error(f"Repair {candidate_id} cannot carry proposed wording: the fidelity audit verdict is {audit['verdict']}"
                    + (f" with changed {', '.join(changed)}" if changed else "") + ". Revise the wording and audit again, or set proposed_wording to null and ask the author.")
    return [{"path": str(path), "sha256": digest(raw)}]


@contextmanager
def locked(journal: Path):
    if journal.is_symlink() or not journal.is_dir():
        raise Error("Journal must be an existing real directory.")
    path = journal / ".record.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Error("A journal operation is in progress, or an interrupted operation left a lock. Preserve the run before recovery.") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def stage_paths(journal: Path, stage: str) -> tuple[Path, Path]:
    number = STAGES.index(stage) + 1
    return journal / f"{number:02d}-{stage}.notes.json", journal / f"{number:02d}-{stage}.receipt.json"


def start_run(index: Any, profile: Any, scope: Any, out: Path) -> dict:
    index = rp.validate_index(index)
    profile = rp._profile(profile)
    scope = validate_scope(scope, index)
    payload = sealed({"schema_version": JOURNAL_SCHEMA, "artifact_type": "academic_review_steps",
                      "snapshot_id": index["snapshot_id"], "index_sha256": index["index_sha256"],
                      "profile": profile, "profile_sha256": rp._canonical_hash(profile),
                      "scope": scope, "scope_sha256": rp._canonical_hash(scope),
                      "recorded_at": datetime.now(timezone.utc).isoformat()})
    out.mkdir(parents=True, exist_ok=False)
    rp.write_json_atomic(out / "00-start.json", payload)
    return {"ok": True, "command": "start", "snapshot_id": index["snapshot_id"], "next_stage": "reading", "journal": str(out)}


def load_run(journal: Path, index: Any) -> tuple[dict, dict, str]:
    index = rp.validate_index(index)
    start = verify_receipt(parse(read_raw(journal / "00-start.json")), "start receipt")
    fields(start, {"schema_version", "artifact_type", "snapshot_id", "index_sha256", "profile", "profile_sha256", "scope", "scope_sha256", "recorded_at"}, "start receipt")
    if type(start["schema_version"]) is not int or start["schema_version"] != JOURNAL_SCHEMA or start["artifact_type"] != "academic_review_steps":
        raise Error("Unsupported journal schema; use the helper version that wrote this journal.")
    if (start["snapshot_id"], start["index_sha256"]) != (index["snapshot_id"], index["index_sha256"]):
        raise Error("Current index differs from the journal's source. Start a new run for a revised source.")
    rp._profile(start["profile"])
    validate_scope(start["scope"], index)
    if rp._canonical_hash(start["profile"]) != start["profile_sha256"] or rp._canonical_hash(start["scope"]) != start["scope_sha256"]:
        raise Error("Reader profile or scope checksum changed.")
    notes = {}
    previous_hash = start["receipt_sha256"]
    gap = False
    for stage in STAGES:
        note_path, receipt_path = stage_paths(journal, stage)
        present = (os.path.lexists(note_path), os.path.lexists(receipt_path))
        if any(present) and not all(present):
            raise Error(f"Incomplete saved {stage} stage. Preserve it and use a new run.")
        if not all(present):
            gap = True
            continue
        if gap:
            raise Error("Saved stages are out of order or a preceding stage is missing.")
        raw = read_raw(note_path)
        receipt = verify_receipt(parse(read_raw(receipt_path)), stage + " receipt")
        fields(receipt, {"stage", "snapshot_id", "previous_receipt_sha256", "notes_sha256", "attachments", "recorded_at"}, stage + " receipt")
        if receipt["stage"] != stage or receipt["snapshot_id"] != start["snapshot_id"] or receipt["previous_receipt_sha256"] != previous_hash or receipt["notes_sha256"] != digest(raw):
            raise Error(f"Saved {stage} notes or their preceding record have changed.")
        value = parse(raw)
        actual_attachments = validate_notes(stage, value, start, index, notes)
        if rp._canonical_hash(receipt["attachments"]) != rp._canonical_hash(actual_attachments):
            raise Error("A saved checkpoint attachment has changed.")
        notes[stage] = value
        previous_hash = receipt["receipt_sha256"]
    return start, notes, previous_hash


def record_stage(journal: Path, index: Any, stage: str, raw: bytes) -> dict:
    with locked(journal):
        start, previous, previous_hash = load_run(journal, index)
        if len(previous) == len(STAGES) or STAGES[len(previous)] != stage:
            expected = STAGES[len(previous)] if len(previous) < len(STAGES) else "ready"
            raise Error(f"Next operation is {expected}; cannot record {stage} now.")
        if os.path.lexists(journal / READY):
            raise Error("This run was already marked ready; preserve it and start a new run.")
        notes = parse(raw)
        attachments = validate_notes(stage, notes, start, index, previous)
        receipt = sealed({"stage": stage, "snapshot_id": start["snapshot_id"],
                          "previous_receipt_sha256": previous_hash, "notes_sha256": digest(raw),
                          "attachments": attachments, "recorded_at": datetime.now(timezone.utc).isoformat()})
        note_path, receipt_path = stage_paths(journal, stage)
        write_raw(note_path, raw)
        rp.write_json_atomic(receipt_path, receipt)
    n = STAGES.index(stage) + 1
    return {"ok": True, "command": "record", "stage": stage, "next_stage": STAGES[n] if n < len(STAGES) else "ready", "notes_sha256": receipt["notes_sha256"]}


def verify_ready(journal: Path, start: dict, notes: dict, previous_hash: str) -> dict:
    if set(notes) != set(STAGES):
        raise Error("All " + ", ".join(STAGES) + " notes must be saved before drafting.")
    if not os.path.lexists(journal / READY):
        raise Error("Run ready successfully before drafting and checking the final review.")
    ready = verify_receipt(parse(read_raw(journal / READY)), "ready receipt")
    if ready.get("ready_to_draft") is not True or ready.get("snapshot_id") != start["snapshot_id"] or ready.get("last_receipt_sha256") != previous_hash:
        raise Error("Ready receipt no longer corresponds to this journal.")
    return ready


def ready_run(journal: Path, index: Any) -> dict:
    with locked(journal):
        start, notes, previous_hash = load_run(journal, index)
        if set(notes) != set(STAGES):
            missing = [s for s in STAGES if s not in notes]
            raise Error("Cannot draft the final review; missing stages: " + ", ".join(missing) + ".")
        path = journal / READY
        if os.path.lexists(path):
            receipt = verify_ready(journal, start, notes, previous_hash)
        else:
            receipt = sealed({"snapshot_id": start["snapshot_id"], "last_receipt_sha256": previous_hash,
                              "recorded_at": datetime.now(timezone.utc).isoformat(), "ready_to_draft": True})
            rp.write_json_atomic(path, receipt)
    return {"ok": True, "command": "ready", "ready_to_draft": True, "receipt_sha256": receipt["receipt_sha256"],
            "verified": ["saved stages in permitted order", "source/profile/scope and file consistency", "declared passage coverage", "exact anchors and candidate decisions"], "not_verified": LIMITS}


def check_final(journal: Path, index: Any, review: Any) -> dict:
    with locked(journal):
        start, notes, previous_hash = load_run(journal, index)
        verify_ready(journal, start, notes, previous_hash)
        rp.check_review(index, review)
        candidates = all_candidates(notes)
        decisions = by_id(notes["decisions"]["decisions"], "candidate_id", "decisions")
        repairs = by_id(notes["repairs"]["repairs"], "candidate_id", "repairs")
        final = by_id(review["issues"], "candidate_id", "final issues")
        if final.keys() != repairs.keys():
            raise Error("Final issues must match all accepted/needs_author candidates exactly; rejected/merged or new issues cannot appear.")
        for key, issue in final.items():
            decision, repair = decisions[key], repairs[key]
            if issue.get("decision_status") != decision["status"]:
                raise Error(f"Final issue {key} must preserve decision_status.")
            for field in ("category", "diagnosis", "severity", "confidence"):
                if issue[field] != decision[field]:
                    raise Error(f"Final issue {key}.{field} differs from the saved decision; revise notes in a new run.")
            for field in ("action", "scientific_fidelity"):
                if issue[field] != repair[field]:
                    raise Error(f"Final issue {key}.{field} differs from its saved repair.")
            if issue["anchor"] != candidates[key]["anchor"]:
                raise Error(f"Final issue {key} changes the candidate's source anchor.")
            if issue.get("proposed_wording") != repair["proposed_wording"]:
                raise Error(f"Final issue {key} changes or omits the saved proposed wording.")
            if repair["author_question"] is not None and issue.get("author_question") != repair["author_question"]:
                raise Error(f"Final issue {key} must preserve the author's unresolved question.")
            allowed = {rp._canonical_hash(a) for a in decision["evidence"] + candidates[key]["evidence"] + [candidates[key]["anchor"]]}
            if any(rp._canonical_hash(a) not in allowed for a in issue.get("evidence", [])):
                raise Error(f"Final issue {key} adds evidence not recorded during checking.")
    return {"ok": True, "command": "check-final", "issue_count": len(final),
            "verified": ["ready receipt and saved-stage consistency", "final review schema and exact source anchors", "final issues exactly linked to saved decisions and repairs", "needs_author status and author question retained"],
            "not_verified": LIMITS + ["whether final prose preserves the implications of its structured status"]}


def parser() -> argparse.ArgumentParser:
    p = rp.JsonArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Freeze reader assumptions and declared coverage in a new journal.")
    start.add_argument("--index", required=True)
    start.add_argument("--profile", required=True)
    start.add_argument("--scope", required=True)
    start.add_argument("--out", required=True)
    for name in ("record", "ready", "check-final"):
        command = commands.add_parser(name)
        command.add_argument("--journal", required=True)
        command.add_argument("--index", required=True)
        if name == "record":
            command.add_argument("--stage", required=True, choices=STAGES)
            command.add_argument("--notes", required=True)
        elif name == "check-final":
            command.add_argument("--review", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        index = rp.load_json(args.index)
        if args.command == "start":
            result = start_run(index, rp.load_json(args.profile), rp.load_json(args.scope), Path(args.out))
        elif args.command == "record":
            result = record_stage(Path(args.journal), index, args.stage, Path(args.notes).read_bytes())
        elif args.command == "ready":
            result = ready_run(Path(args.journal), index)
        else:
            result = check_final(Path(args.journal), index, rp.load_json(args.review))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except Error as exc:
        error = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        error = {"ok": False, "error": {"code": "input_output_error", "message": str(exc)}}
    print(json.dumps(error, ensure_ascii=True), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
