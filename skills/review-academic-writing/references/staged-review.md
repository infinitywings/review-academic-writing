# Work through the review in recorded stages

## Contents

- Purpose and limits
- 1. Record the source, reader, and scope
- 2. Save reading notes
- 3. Save checks and candidate problems
- 4. Reconcile reader observations
- 5. Decide which candidates survive checking
- 6. Prepare repairs and check their meaning
- 7. Check readiness and produce the report
- Interrupted work, corrections, and smaller tasks

## Purpose and limits

Use this procedure for every substantive review, structure plan, or revision. Its purpose is to make the actual checking work inspectable and to keep early impressions from becoming recommendations without verification. A long final answer, an announced plan, or a list of checks to perform does not substitute for completed notes.

Keep the working notes outside the installed skill. Complete a stage, save it, inspect it, and record it with the helper before working on the next stage. Use separate tool actions for these transitions. Correct errors found in the current draft notes before recording that stage; use a new draft filename when retaining the correction history is useful. Already recorded notes and original reader observations stay unchanged. Do not ask a model or a script to generate all stages and the final report together and then record them one by one. Scripts may organize and validate completed notes; they do not perform the semantic reading merely by populating fields.

Record quotations, short local paraphrases, actual observations, checked locations, unresolved questions, and concise reasons for decisions. These are external work products, not a transcript of private reasoning. Be detailed about the manuscript while keeping each note relevant to the check. Do not inflate a simple passage into a long abstract summary.

The helper checks saved artifacts, source consistency, declared coverage, order of recording, and correspondence to the final findings. It cannot establish that a model thought carefully, cannot rule out a report drafted elsewhere beforehand, and cannot certify scientific truth or human reading difficulty. Apply the procedure honestly and state the checks actually performed.

All paths below are examples. Replace `${CLAUDE_SKILL_DIR}` and manuscript/run filenames with real locations. Use a new journal directory; keep original source files intact.

## 1. Record the source, reader, and scope

Prepare and inspect the reading edition as required by SKILL.md. Run the existing index helper, save the independent reader profile, and populate [the scope template](../assets/notes/scope.json).

Record the task, included block IDs, omitted blocks and individual reasons, reading path, and extraction limits. Each `omissions` entry is an object containing `block_id` and `reason`. Account for every indexed block as included or omitted. Titles and formatting blocks may be omitted with that reason. For a full review, include every substantive block within the user's requested scope. For a sample, identify it as a sample; do not exclude a difficult passage merely to make the run look complete. Record unavailable figures, uncertain equation extraction, and any effect on the conclusions.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py start --index run/index.json --profile run/reader-profile.json --scope run/scope.json --out run/journal
```

The journal binds this source edition, profile, and scope. It is editor-only material. Never send it, the index, or these notes to a prefix reader.

## 2. Save reading notes

Use [the reading template](../assets/notes/reading.json). Fill `snapshot_id` from the index. Write one entry for each included block; use an exact source anchor and account for each block once.

For every paragraph, record:

- `paraphrase`: what it says, in one or two ordinary sentences. This should demonstrate a local reading, not repeat the section title.
- `role`: what it contributes here, such as establishing a problem, explaining a choice, reporting evidence, or limiting a conclusion. Do not force every paragraph into the same structure.
- `required_context`: facts or distinctions needed to understand it, with source locations or a stated general-background assumption where possible.
- `new_information`: the concepts, conditions, claims, or observations it adds.
- `open_questions`: specific uncertainties to check later. An empty list is appropriate when nothing needs checking.

Reading notes produced by an editor who has seen the full document are full-document editorial notes. Do not portray them as observations of a reader who has not seen later text. Keep a later explanation's location explicit instead of silently importing it into what was available earlier.

Inspect coverage and whether the paraphrases preserve the source meaning. Save the completed notes, then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reading --notes run/reading.json
```

Proceed only after included blocks are covered and the record command succeeds. An omitted or uninspectable passage must remain visible in the declared scope and final limitations.

## 3. Save checks and candidate problems

Use [the checks template](../assets/notes/checks.json). Apply the existing review rubric and wording rules to the reading notes and source. This stage collects possible problems; it does not yet authorize final recommendations.

**Concepts and claims.** For central or questioned concepts, record `concept_id`, `term`, `first_mention`, `earliest_usable_explanation`, `first_critical_use`, and a short `assessment` of whether the explanation suffices at that use. Save positions as exact anchors; use null when a position is absent or not required and explain why. Distinguish a comprehension prerequisite from supporting evidence or a later formal specification. If no concept record is relevant, use an empty `concepts` array and state `no_concepts_reason`.

**Paragraph relations.** In each included block's `structure_check`, describe how its proposition connects to preceding material, what it establishes for subsequent material, and whether its claim matches the evidence and scope. Point to actual locations. Check dispersed information by naming the pieces that must be combined. Do not replace this with a generic rating such as “flow is good.”

**Words and sentences.** In `wording_check`, inspect terms and phrases in context. For a questioned expression, record what it means, whether a name is necessary, and what an ordinary description could express. Check subjects, actions, noun strings, nesting, qualifications, and reference consistency. Also record why a passage or necessary term should remain when that is the result; do not create an issue just to demonstrate effort. Include at least one exact quote from the current block in each passage's `evidence`; add other supporting source anchors when relevant.

**Checkpoint plan.** This stage is the editor's independent review: record it before consulting any reader observation, and the helper refuses `checkpoints`, `branches`, or `observations` here. Save the selection plan in `checkpoint_plan`, one entry per intended checkpoint with `id`, `anchor`, `mode`, and `reason`; state whether the selection used the full text. An empty plan is valid when no independent reader will be run. Dispatch readers only after this stage is recorded, following workflow.md and masked-reading.md.

**Candidates.** Give each possible issue `id`, `anchor`, `question`, and `evidence`, where evidence is an array of exact source anchors. A question such as “Does the reader need the formal equation at this sentence?” is a candidate, not proof that the equation is too late. Keep the provenance of a reader question in the surrounding notes. Combine obvious duplicate symptoms or keep them for explicit merging in the next step. Zero candidates is valid.

Inspect the saved source anchors and the check of every included block, then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage checks --notes run/checks.json
```

## 4. Reconcile reader observations

Use [the reconcile template](../assets/notes/reconcile.json). Record this stage after the checks stage is frozen and after every reader run it cites is complete. It binds the reader evidence to the journal and records the editor's classification of each observation; it does not reopen the independent checks.

**Checkpoints.** Include each completed checkpoint in `checkpoints`, with `id`, `anchor`, actual `mode`, `figure_policy` (`text-only` for prefix packets, which carry no figures; for a masked run the policy its case declares, `strict-prefix` or `reference-jump`), `result`, `limitation`, and its attachment. A `fresh-prefix` or `bounded-request` checkpoint must give `plan_id` naming the checks-stage plan entry with the same anchor and mode, plus `packet_path` and `observation_path`. A `masked-sequential` checkpoint gives `run_path` and `record_sequence`; the helper replays the run, requires every case block to be text of this source, and requires the anchor quotation to be text the reader had seen by that event. A `full-context` checkpoint is an editorial hypothesis and needs no attachment. If no checkpoint was completed, use an empty array and give `no_checkpoints_reason`. Do not mark a planned or unavailable call as completed. Retain original bytes and controller records separately. The template lists one entry per mode; delete the entries you do not use.

**Branches.** Include each targeted question answered through the branch procedure in masked-reading.md in `branches`, with `id`, `run_path`, `branch_sequence`, `result`, and `limitation`. The helper replays the branch against its display event and binds its files.

**Observations.** For each useful reader observation, record `id`, `source_id` (a checkpoint or branch id above), the original `reader_question`, a `classification` from the table in masked-reading.md (`absent_at_critical_use`, `resolved_later`, `needed_again`, `lost_in_notes`, `interpretation_changed`, `inferred_connection`, `asset_lookup`, or `adequate`), the `editor_check` that produced it with exact `evidence`, and `candidate_id` when it supports a checks candidate or a candidate added here, else null. Keep the original reader question; the editor's later explanation does not replace it.

**Candidates.** Add candidates that the observations raise to `candidates`, with new ids. Do not reuse or rewrite a checks candidate.

Then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reconcile --notes run/reconcile.json
```

## 5. Decide which candidates survive checking

Use [the decisions template](../assets/notes/decisions.json). Reopen the relevant source locations; do not decide solely from a summary of the candidate.

For every candidate from the checks and reconcile stages, record `candidate_id`, `verification` describing what was checked, `countercheck` describing what could make the existing text adequate, exact source `evidence`, and one `status`:

| Status | Meaning |
| --- | --- |
| `accepted` | The checked evidence supports a specific writing problem. Record `category`, `diagnosis`, `severity`, and `confidence`. |
| `rejected` | An earlier explanation, expected background, reasonable inference, honest preview, or another checked fact makes the proposed criticism unwarranted. State the actual reason. |
| `merged` | It is a symptom of another retained candidate. Set `merged_into` to that accepted or author-question candidate; do not count it again. |
| `needs_author` | Available text cannot settle a necessary scientific or interpretive question. Record `category`, `diagnosis`, `severity`, and `confidence`, explicitly describing the uncertainty rather than a confirmed defect with an invented solution. |

The countercheck is substantive. “I considered alternatives” is insufficient. For example, inspect whether the score's sign is already explained before criticizing the later formula; inspect whether a repeated limitation serves a different purpose before calling it redundant. Do not manufacture rejected candidates to fill a quota.

Save a decision for every actual candidate, including rejected and merged items. When none arose, keep an empty list and a meaningful summary of the checks performed. Then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage decisions --notes run/decisions.json
```

## 6. Prepare repairs and check their meaning

Use [the repairs template](../assets/notes/repairs.json). Cover exactly the accepted and author-question items. Draft concrete sentence replacements or moves after the issue has been assessed, not while treating the initial impression as established.

For each retained candidate, record:

- `candidate_id`: the retained candidate being addressed.
- `kind`: `edit`, `move`, or `author_question`.
- `action` and `proposed_wording`: the full replacement or the precise move from one location to another; use null for wording when no justified replacement is possible.
- `scientific_fidelity`: the source-to-repair check of claim strength, numbers, units, conditions, negation, assumptions, population, outcomes, time, and uncertainty. Name the relevant facts rather than writing only “preserved.” If intentionally narrowing an unsupported claim, say so.
- `new_terms_check`: whether the proposed text introduced a label, acronym, compound noun, or unnecessary synonym, and what was done about it.
- `affected_block_ids`: a nonempty list of known locations requiring corresponding changes or the author's clarification; inspect definitions, subsequent uses, and relevant captions.
- `author_question`: the actual question when the text does not supply a scientific decision. A `needs_author` item must remain a question with no asserted replacement.
- `audit_path`: for every repair with `proposed_wording`, the file written by an independent fidelity auditor; null otherwise.

**Independent fidelity audit.** Do not accept your own fidelity statement for a proposed wording. Give a fresh, tool-less auditor only the exact original text (as anchors), the proposed wording, and the basis facts with their manuscript locations, using [the audit prompt](../assets/prompts/fidelity-audit.txt); it must not see the editor's reasons. Save its JSON unchanged in the shape of [the audit template](../assets/notes/fidelity-audit.json). The helper records a repair with wording only when the audit verdict is `preserved` and none of the nine properties (claim strength, quantifiers, assumptions, population, measured outcome, time horizon, numerical values, uncertainty, causal versus associative wording) is `changed`; otherwise revise the wording and audit again, or set `proposed_wording` to null and ask the author. Keep a rejected audit; do not edit it. On Claude Code, `python3 ${CLAUDE_SKILL_DIR}/scripts/dispatch_reader.py audit --events run/events --input run/C04.input.json --out run/C04.audit.json` sends the prompt and the input to a tool-less `claude -p` call, checks the returned shape, and writes the audit with `auditor` filled in.

**Advisory feature comparison.** When spaCy is installed, `<nlp-env>/bin/python ${CLAUDE_SKILL_DIR}/scripts/difficulty_features.py compare --input run/C04.input.json --out run/C04.features.json` (or `--repairs run/repairs.json --checks run/checks.json` for every repair with wording) counts the eight parse-based difficulty features in the original anchors and the proposed wording and flags any feature the proposal has more of. A flag is a reason to look at the wording again (a new noun stack, an object relative, a noun used as a verb); it is not a gate, it is not recorded in the journal, and it does not change the audit requirement above.

For a structure plan, record exact moves and resulting dependencies. For requested revision, preserve the source, re-index the proposed version, and perform the relevant rechecks described in workflow.md; keep those results with the repair evidence. Do not claim the revised document was independently reread if it was only compared by the original editor.

Save and inspect the proposed changes and their meaning checks, then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage repairs --notes run/repairs.json
```

## 7. Check readiness and produce the report

Before drafting the final report, run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py ready --journal run/journal --index run/index.json
```

Resolve a failed readiness check before representing the review as complete. The check cannot approve the substantive quality of the notes; inspect that separately. Retain the receipt. Then compile the structured review from the recorded decisions and repairs, using the existing output contract and adding `candidate_id` and `decision_status` to every final issue. Carry the recorded category, diagnosis, severity, confidence, action, scientific-fidelity statement, and proposed wording into their corresponding fields. Preserve author questions as questions. Use only the candidate anchor or evidence already recorded for that candidate or its decision in final evidence. Accepted and author-question candidates must all be represented in the complete structured findings; prioritize the main report without silently losing the other retained items.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py check-final --journal run/journal --index run/index.json --review run/review.json
```

Use the completed findings and notes to write the user-facing report. Explain the most consequential changes in ordinary language. Keep implementation fields and receipts in supporting records; provide an accessible account of passage coverage, candidate decisions, and meaning checks. Report useful retained text as well as corrections. A rejected candidate may explain why something should stay; it must not reappear as an accepted criticism.

If a new diagnosis appears while drafting, return to the relevant checks in a corrected run. Do not simply add it to the report or invent earlier notes for it.

## Interrupted work, corrections, and smaller tasks

- **Resume:** inspect the source binding, saved notes, and actual recorded stage. Continue from completed work; label any reconstructed record as reconstructed, not an original observation.
- **Correct:** a current-stage draft may be fixed before it is recorded; this alone does not require a new journal. After changing an already recorded note, bound reader profile, reading edition, or scope, keep the old journal and start a new run. Explicitly carry forward only unaffected records after checking them; redo dependent decisions and repairs. Do not manually update hashes or edit receipts to suppress a mismatch. Preserve original reader observations even when their parsed or corrected versions need separate files.
- **Quick review:** select and declare a smaller set of passages, then complete the same stages for that set. A one-paragraph task needs one substantive reading entry, not an artificial full-manuscript analysis.
- **Unavailable execution:** if files or helper execution are unavailable, write the same stage records in successive sections of the working document and state that automatic checks were not run. Do not claim file validation or independent reader execution that did not occur.
- **Unavailable content:** record the omission and its consequences. Do not fill uncertain fields with guesses to pass a check.

No stage requires a fixed number of problems or edits. An adequately supported decision to keep the text is a successful outcome.
