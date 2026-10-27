#!/usr/bin/env python3
"""JSON Schemas for the editor's responses, one per step, for constrained decoding.

Each schema restates the response shape that the step's prompt example shows and the step's validator
checks: required fields in the example's order, closed vocabularies, the block and candidate ids a field
may name, and the exact number of rows a batch returns. The orchestrator sends them as response_format
json_schema when the endpoint config sets "json_schema": true.

Why: a model that writes an unescaped straight quotation mark inside prose ends that JSON string early.
Under plain JSON mode the decoder may then close the object, dropping every later field, or pad with
whitespace until the token limit. Under a schema, and with the server's disable_any_whitespace option
(vLLM), the only continuation is the next required field, so the response stays complete. The validators
remain the authority; a schema only narrows what the model can emit.

Strings carry no minLength or maxLength: xgrammar compiles a length-bounded string to characters without a
backslash, so the model could write no \n, \" or \\ (the report lost every line break; LaTeX quotes lost
their commands). Nonempty strings are left to the validators.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Iterable

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import review_packets as rp  # noqa: E402
import review_steps as rs  # noqa: E402

SAFE_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"

Schema = dict[str, Any]


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


def obj(properties: dict[str, Schema], optional: Iterable[str] = ()) -> Schema:
    optional = set(optional)
    return {"type": "object", "properties": properties, "required": [k for k in properties if k not in optional], "additionalProperties": False}


def anchor(block_ids: Iterable[str]) -> Schema:
    return obj({"block_id": one_of(block_ids), "quote": text()})


def candidate(included: list[str], block_ids: list[str]) -> Schema:
    return obj({"id": {"type": "string", "pattern": SAFE_ID_PATTERN}, "anchor": anchor(included), "question": text(), "evidence": array(anchor(block_ids))})


def scope(task: str, block_ids: list[str]) -> Schema:
    return obj({"task": {"const": task}, "included_block_ids": array(one_of(block_ids), at_least=1, at_most=len(block_ids)),
                "omissions": array(obj({"block_id": one_of(block_ids), "reason": text()}), at_most=len(block_ids)),
                "reading_path": text(), "extraction_notes": text()})


def reading(batch: list[str]) -> Schema:
    notes = array(text())
    return obj({"summary": text(),
                "paragraphs": array(obj({"anchor": anchor(batch), "paraphrase": text(), "role": text(),
                                         "required_context": notes, "new_information": notes, "open_questions": notes}), exactly=len(batch))})


def checks(batch: list[str], included: list[str], block_ids: list[str], remaining_checkpoints: int) -> Schema:
    nullable_anchor = nullable(anchor(block_ids))
    return obj({
        "summary": text(),
        "checkpoint_plan": array(obj({"id": text(), "anchor": anchor(included), "mode": {"const": "bounded-request"}, "reason": text()}),
                                 at_most=max(remaining_checkpoints, 0)),
        "candidates": array(candidate(included, block_ids)),
        "branch_question": nullable(obj({"question": text(), "reason": text(), "after_block_id": one_of(included)})),
        "concepts": array(obj({"concept_id": text(), "term": text(), "first_mention": anchor(block_ids),
                               "earliest_usable_explanation": nullable_anchor, "first_critical_use": nullable_anchor, "assessment": text()})),
        "no_concepts_reason": text(),
        "passages": array(obj({"block_id": one_of(batch), "structure_check": text(), "wording_check": text(),
                               "evidence": array(anchor(block_ids), at_least=1)}), exactly=len(batch)),
    }, optional=("no_concepts_reason",))


def recheck(batch: list[str], block_ids: list[str]) -> Schema:
    return obj({"summary": text(), "candidates": array(obj({"id": {"type": "string", "pattern": SAFE_ID_PATTERN}, "anchor": anchor(batch), "question": text(),
                                                             "evidence": array(anchor(block_ids))}))})


def reconcile(included: list[str], block_ids: list[str], branch_ids: list[str]) -> Schema:
    checkpoint = obj({"id": text(), "record_sequence": {"type": "integer", "minimum": 0}, "anchor": anchor(included), "result": text(), "limitation": text()},
                     optional=("record_sequence", "anchor"))
    return obj({
        "summary": text(),
        "checkpoints": array(checkpoint),
        "no_checkpoints_reason": text(),
        "branches": array(obj({"id": one_of(branch_ids), "result": text(), "limitation": text()}), at_most=len(branch_ids)),
        "observations": array(obj({"id": text(), "source_id": text(), "reader_question": text(), "classification": one_of(rs.OBSERVATION_KINDS),
                                   "editor_check": text(), "evidence": array(anchor(block_ids)), "candidate_id": nullable(text())})),
        "candidates": array(candidate(included, block_ids)),
    }, optional=("no_checkpoints_reason",))


def decisions(candidate_ids: list[str], block_ids: list[str]) -> Schema:
    def decision(status: Schema, extra: dict[str, Schema], after: dict[str, Schema]) -> Schema:
        return obj({"candidate_id": one_of(candidate_ids), "status": status, **extra, "verification": text(), "countercheck": text(),
                    "evidence": array(anchor(block_ids)), **after})

    final = {"category": one_of(rp.REVIEW_CATEGORIES), "diagnosis": text(), "severity": one_of(rp.SEVERITIES), "confidence": one_of(rp.CONFIDENCES)}
    return obj({"summary": text(),
                "decisions": array({"anyOf": [decision(one_of(rs.FINAL_STATUSES), {}, final),
                                              decision({"const": "rejected"}, {}, {}),
                                              decision({"const": "merged"}, {"merged_into": one_of(candidate_ids)}, {})]},
                                   exactly=len(candidate_ids))})


def repairs(wanted: list[str], block_ids: list[str]) -> Schema:
    return obj({"summary": text(),
                "repairs": array(obj({"candidate_id": one_of(wanted), "kind": one_of({"edit", "move", "author_question"}), "action": text(),
                                      "proposed_wording": nullable(text()), "original": array(anchor(block_ids)),
                                      "basis": array(obj({"fact": text(), "location": text()})), "scientific_fidelity": text(),
                                      "new_terms_check": text(), "affected_block_ids": array(one_of(block_ids), at_least=1),
                                      "author_question": nullable(text())}), exactly=len(wanted))})


def review(issue_ids: list[str]) -> Schema:
    return obj({"summary": text(), "issue_order": array(one_of(issue_ids), exactly=len(issue_ids))})


def report() -> Schema:
    return obj({"report_markdown": text()})
