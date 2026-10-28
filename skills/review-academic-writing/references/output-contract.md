# Structured output contract

## Required stage records

For a completed review, follow staged-review.md and use `scripts/review_steps.py` to save the source/scope, reading, checks, reconcile, decisions, and repairs before final drafting. The JSON templates in `assets/notes/` are blank structures to fill during the relevant step, not completed examples. The legacy source validator below remains useful on its own, but its success does not satisfy the staged-work requirement.

The stage helper enforces declared block coverage, validates conventional source anchors, binds the journal to the index and reader profile, and requires a readiness receipt before `check-final`. It binds a prefix checkpoint by its packet and observation files, a masked checkpoint by its run directory and record sequence, a branch by its run directory and branch sequence, every proposed wording by its fidelity audit file, which must report `verdict: preserved` with no property `changed` and a hedge-by-hedge report, and a `sentence`-scope edit by its feature comparison file. Every final issue adds `candidate_id` and `decision_status`. Match the candidate anchor and saved decision fields (`category`, `diagnosis`, `severity`, `confidence`) and repair fields (`action`, `scientific_fidelity`, `proposed_wording`). Include `proposed_wording` as null when absent. A `needs_author` item must also retain the saved `author_question`; do not turn it into an asserted rewrite. Include all accepted/author-question candidates in the full structured review, while prioritizing the main prose report. Do not include rejected or merged candidates as additional defects.

Raw note files and their receipts make changes and skipped stages detectable under the helper's checks. They do not authenticate the model's private process, establish that a note is thoughtful, or prove that the model did not draft a report elsewhere first. State this scope accurately.

## Helper commands

Run `python3 scripts/review_packets.py --help` from the skill directory or use its absolute path. Use `index`, `packet`, `check-observation`, and `check-review`. Keep run outputs outside the skill folder. Existing output files cause an error; choose a new filename or intentionally remove an obsolete run artifact after retaining the version needed.

Supply only UTF-8 `.md` or `.txt` to `index`. Normalize document extraction before indexing. Character offsets refer to Python Unicode characters in the normalized string, not UTF-8 bytes, PDF coordinates, or token counts. Treat end offsets as exclusive. Use exact source quotations for most checkpoint selection.

Preserve all generated hashes and fields unchanged. Rebuild a packet if its profile or source changes. Re-index a revised manuscript. Do not update hashes manually to make a stale observation appear current.

## Observation

Require:

```json
{
  "snapshot_id": "copy from packet",
  "checkpoint_id": "copy from packet",
  "prefix_sha256": "copy from packet",
  "packet_sha256": "copy from packet",
  "understanding": "A short current interpretation; qualify uncertainty.",
  "issues": []
}
```

For every issue, require `id`, `category`, `anchor`, `reader_question`, `consequence`, `severity`, and `confidence`. Set `anchor` to an object with a visible `block_id` and an exact, nonempty `quote`. Optionally supply `evidence` as an array of the same anchor objects. Keep issue IDs unique within the response.

Use these categories: `unavailable_prerequisite`, `ambiguous_reference`, `missing_relation`, `retrieval_burden`, `scope_ambiguity`, `other`. Do not label a missing prerequisite as late before seeing later text. Use `critical`, `major`, `minor`, or `optional` for severity and `high`, `medium`, or `low` for confidence. An empty issues list is valid.

An observation may add `cannot_place`, an array of `{"anchor": {"block_id", "quote"}, "note"}` entries for nouns or references the reader could not place at that point; each quote must be visible text, and the entries are not issues. Run `check-observation --packet PACKET --observation OBSERVATION`. The packet checksum binds the response to the exact reader profile as well as the text. Keep the actual input/context provenance in a controller record, not in the worker's self-description.

## Final review

Require:

```json
{
  "snapshot_id": "copy from index",
  "summary": "Overall finding and the scope actually reviewed.",
  "issues": [
    {
      "id": "W01",
      "candidate_id": "C01",
      "decision_status": "accepted",
      "category": "late_prerequisite",
      "anchor": {"block_id": "B0001", "quote": "exact source quotation"},
      "diagnosis": "Which meaning or relation is needed here, and where it is established.",
      "action": "The specific text to move, add, split, clarify, or ask the author to determine.",
      "proposed_wording": null,
      "scientific_fidelity": "Which assumptions and claim limits are preserved; name any unresolved author decision.",
      "severity": "major",
      "confidence": "high",
      "evidence": [{"block_id": "B0003", "quote": "exact source support"}]
    }
  ]
}
```

Use final categories `late_prerequisite`, `unavailable_prerequisite`, `ambiguous_reference`, `scattered_information`, `retrieval_burden`, `missing_relation`, `paragraph_function`, `terminology_scope_drift`, `unsupported_claim`, `unnecessary_term`, `word_choice`, `sentence_structure`, `other`. Keep the same severity/confidence values. An empty issues list with a substantive summary is valid. For wording findings, put the exact proposed replacement in `action` or an additional `proposed_wording` field, and explain scientific fidelity.

`action` is a verb phrase with a position: it names at least one block id of the indexed source (B0003) and says what to do there (move, add, replace, split, ask). A vague comment is a defect in its own right (focused, locatable feedback is what reviewers act on), so `check-final` refuses a final issue whose action names no block id, and refuses a `needs_author` issue whose `author_question` does not end with a question mark. The same two checks run when the repairs stage is recorded, so a report drafted from recorded repairs already satisfies them.

Add other fields such as `initial_reader_question`, `reader_observation_id`, `affected_locations`, `execution_mode`, `coverage`, or `concept_ledger` when useful. Include `author_question` for a staged item that requires the author's decision. Keep quotations in additional fields accurate too; do not assume the validator checks arbitrary added data. Use `evidence` for all machine-validated supporting anchors.

For a staged review, the final `evidence` may use the candidate anchor or evidence already recorded for that candidate or its decision. Check and record new evidence before including it in a final finding. The stage helper rejects an unrecorded addition even when the quotation is real.

For the editor's concept ledger, record term, M/E/U locations, usable meaning at U, allowed background or short inference, scope, aliases, reminder need, and evidence. When using structured anchors, use `first_mention`, `earliest_usable_explanation`, and `first_critical_use`, each null or an anchor object, plus an optional `evidence` array. For a structure plan, record paragraph order, question, entry knowledge, newly established concepts, claim/evidence, and figure placement. These are editorial analyses; the script checks conventional ledger anchors but does not validate their semantics.

Run `check-review --index INDEX --review REVIEW`. Interpret success as schema, snapshot, range, and exact-quotation consistency only. A successful check does not establish scientific validity, human comprehension, completeness of review, or effective context isolation.
