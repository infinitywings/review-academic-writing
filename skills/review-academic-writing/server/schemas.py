#!/usr/bin/env python3
"""JSON Schemas for the editor's responses, one per step, for constrained decoding.

Each schema restates the response shape that the step's prompt example shows and the step's validator
checks: required fields in the example's order, closed vocabularies (categories, the three severities,
observation classifications, statuses, edit scopes), the block ids, candidate ids, open questions, and
reader-profile entries a field may name, and the number of rows a batch returns. The orchestrator sends
them as response_format json_schema when the endpoint config sets "json_schema": true.

Why: a model that writes an unescaped straight quotation mark inside prose ends that JSON string early.
Under plain JSON mode the decoder may then close the object, dropping every later field, or pad with
whitespace until the token limit. Under a schema, and with the server's disable_any_whitespace option
(vLLM), the only continuation is the next required field, so the response stays complete. The
validators (review_steps, review_packets) remain the authority; a schema only narrows what the model can
emit. A field the helpers accept absent is optional here, so every answer they accept fits its schema;
checks.branch_question is the exception, required with null allowed, because Flash-Next left it out
whenever it could.

No string carries minLength, maxLength, or pattern (FORBIDDEN_KEYWORDS): xgrammar (vLLM 0.31) compiles a
length-bounded string to characters without a backslash, so the model could write no \\n, \\" or \\\\ (a
model-written report of the 0.14.1 server line lost every line break; LaTeX quotes lost their commands),
and a pattern repeated within one schema trips an internal xgrammar assertion. Nonempty strings and safe ids are left to the validators, and the
endpoint config's max_tokens bounds a string that runs away. const and enum are fine.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Iterable

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import review_packets as rp  # noqa: E402
import review_steps as rs  # noqa: E402

Schema = dict[str, Any]
FORBIDDEN_KEYWORDS = ("minLength", "maxLength", "pattern")


def text() -> Schema:
    return {"type": "string"}


def one_of(values: Iterable[str]) -> Schema:
    values = sorted(set(values))
    return {"type": "string", "enum": values} if values else {"type": "string", "enum": [""]}


def nullable(schema: Schema) -> Schema:
    return {"anyOf": [schema, {"type": "null"}]}


def array(items: Schema, *, exactly: int | None = None, at_least: int = 0, at_most: int | None = None) -> Schema:
    schema: Schema = {"type": "array", "items": items}
    low, high = (exactly, exactly) if exactly is not None else (at_least, at_most)
    if low:
        schema["minItems"] = low
    if high is not None:
        schema["maxItems"] = high
    return schema


def empty() -> Schema:
    """An array that must stay empty: nothing in the input could fill it."""
    return {"type": "array", "maxItems": 0}


def obj(properties: dict[str, Schema], optional: Iterable[str] = ()) -> Schema:
    optional = set(optional)
    return {"type": "object", "properties": properties, "required": [k for k in properties if k not in optional], "additionalProperties": False}


def anchor(block_ids: Iterable[str]) -> Schema:
    return obj({"block_id": one_of(block_ids), "quote": text()})


def candidate(included: list[str], block_ids: list[str], extra: dict[str, Schema] | None = None) -> Schema:
    return obj({"id": text(), "anchor": anchor(included), "question": text(), "evidence": array(anchor(block_ids)), **(extra or {})}, optional=tuple(extra or ()))


def open_question_rows(open_questions: dict[str, list[str]], extra: dict[str, Schema] | None = None) -> Schema:
    """Rows that name one reading-stage open question exactly as raised: one anyOf branch per block, its questions as an enum."""
    branches = [obj({"block_id": {"const": block}, "question": one_of(questions), **(extra or {})}) for block, questions in sorted(open_questions.items()) if questions]
    if not branches:
        return empty()
    return array({"anyOf": branches}, at_most=sum(len(set(q)) for q in open_questions.values()))


def scope(task: str, block_ids: list[str]) -> Schema:
    return obj({"task": {"const": task}, "included_block_ids": array(one_of(block_ids), at_least=1, at_most=len(block_ids)),
                "omissions": array(obj({"block_id": one_of(block_ids), "reason": text()}), at_most=len(block_ids)),
                "reading_path": text(), "extraction_notes": text()})


def reading(batch: list[str]) -> Schema:
    notes = array(text())
    return obj({"summary": text(),
                "paragraphs": array(obj({"anchor": anchor(batch), "paraphrase": text(), "role": text(),
                                         "required_context": notes, "new_information": notes, "open_questions": notes}), exactly=len(batch))})


def checks(batch: list[str], included: list[str], block_ids: list[str], remaining_checkpoints: int, open_questions: dict[str, list[str]], branch_blocks: list[str]) -> Schema:
    """`open_questions` maps each batch block to the questions its reading notes raised; `branch_blocks` are the blocks a branch question may follow."""
    nullable_anchor = nullable(anchor(block_ids))
    proposition = obj({"anchor": anchor(batch), "proposition": text(), "supplied_relations": text(), "function": one_of(rs.SENTENCE_FUNCTIONS), "needed_here": text()})
    return obj({
        "summary": text(),
        "checkpoint_plan": array(obj({"id": text(), "anchor": anchor(included), "mode": {"const": "bounded-request"}, "reason": text()}),
                                 at_most=max(remaining_checkpoints, 0)),
        "candidates": array(candidate(included, block_ids, {"open_questions": open_question_rows(open_questions)})),
        "no_candidate": open_question_rows(open_questions, {"reason": text()}),
        "branch_question": nullable(obj({"question": text(), "reason": text(), "after_block_id": one_of(branch_blocks)})),
        "concepts": array(obj({"concept_id": text(), "term": text(), "first_mention": anchor(block_ids),
                               "earliest_usable_explanation": nullable_anchor, "first_critical_use": nullable_anchor, "assessment": text()})),
        "no_concepts_reason": text(),
        "passages": array(obj({"block_id": one_of(batch), "structure_check": text(), "wording_check": text(),
                               "claim_check": obj({key: text() for key in rs.CLAIM_CHECK_FIELDS}),
                               "evidence": array(anchor(block_ids), at_least=1), "proposition_checks": array(proposition)},
                              optional=("proposition_checks",)), exactly=len(batch)),
    }, optional=("no_concepts_reason",))


def recheck(batch: list[str], block_ids: list[str]) -> Schema:
    return obj({"summary": text(), "candidates": array(candidate(batch, block_ids))})


def reconcile(included: list[str], block_ids: list[str], prefix_ids: list[str], masked_sequences: list[int], branch_ids: list[str], profile_entries: Iterable[str]) -> Schema:
    """`prefix_ids` are the bound prefix checkpoints (K01, ...), `masked_sequences` the recorded masked display events, and
    `profile_entries` the strings of the frozen reader profile, the only text background may quote."""
    kinds = []
    if prefix_ids:
        kinds.append(obj({"id": one_of(prefix_ids), "result": text(), "limitation": text()}))
    if masked_sequences:
        kinds.append(obj({"id": text(), "record_sequence": {"type": "integer", "enum": sorted(set(masked_sequences))}, "anchor": anchor(included), "result": text(), "limitation": text()}))
    checkpoints = array({"anyOf": kinds}, at_least=len(prefix_ids)) if kinds else empty()
    branches = array(obj({"id": one_of(branch_ids), "result": text(), "limitation": text()}), exactly=len(branch_ids)) if branch_ids else empty()
    return obj({
        "summary": text(),
        "checkpoints": checkpoints,
        "no_checkpoints_reason": text(),
        "branches": branches,
        "observations": array(obj({"id": text(), "source_id": text(), "reader_question": text(), "classification": one_of(rs.OBSERVATION_KINDS),
                                   "editor_check": text(), "evidence": array(anchor(block_ids)), "candidate_id": nullable(text()),
                                   "resolved_by": nullable(anchor(block_ids)), "background": nullable(one_of(profile_entries))},
                                  optional=("resolved_by", "background"))),
        "candidates": array(candidate(included, block_ids)),
    }, optional=("no_checkpoints_reason",))


def decisions(candidate_ids: list[str], block_ids: list[str], challenged: bool) -> Schema:
    """When the candidates were challenged, a kept decision may answer its challenge in challenge_response (the helper
    requires it when the challenge disputed the candidate); the controller binds challenge_path, so no schema offers it."""
    def decision(status: Schema, before: dict[str, Schema], after: dict[str, Schema]) -> Schema:
        return obj({"candidate_id": one_of(candidate_ids), "status": status, **before, "verification": text(), "countercheck": text(),
                    "evidence": array(anchor(block_ids)), **after}, optional=("challenge_response",))

    kept = {"category": one_of(rp.REVIEW_CATEGORIES), "diagnosis": text(), "close_criterion": text(), "severity": one_of(rp.REPORT_SEVERITIES),
            "confidence": one_of(rp.CONFIDENCES)}
    if challenged:
        kept["challenge_response"] = nullable(text())
    return obj({"summary": text(),
                "decisions": array({"anyOf": [decision(one_of(rs.FINAL_STATUSES), {}, kept),
                                              decision({"const": "rejected"}, {}, {}),
                                              decision({"const": "merged"}, {"merged_into": one_of(candidate_ids)}, {})]},
                                   exactly=len(candidate_ids))})


def repairs(wanted: list[str], block_ids: list[str]) -> Schema:
    return obj({"summary": text(),
                "repairs": array(obj({"candidate_id": one_of(wanted), "kind": one_of(rs.REPAIR_KINDS), "edit_scope": nullable(one_of(rs.EDIT_SCOPES)), "action": text(),
                                      "proposed_wording": nullable(text()), "wording_withheld_reason": nullable(text()), "heavy_edit_reason": nullable(text()),
                                      "original": array(anchor(block_ids)), "basis": array(obj({"fact": text(), "location": text()})),
                                      "scientific_fidelity": text(), "new_terms_check": text(), "affected_block_ids": array(one_of(block_ids), at_least=1),
                                      "author_question": nullable(text())}, optional=("edit_scope", "wording_withheld_reason", "heavy_edit_reason")), exactly=len(wanted))})


def review(issue_ids: list[str]) -> Schema:
    return obj({"summary": text(), "scope_note": text(), "issue_order": array(one_of(issue_ids), exactly=len(issue_ids))})
