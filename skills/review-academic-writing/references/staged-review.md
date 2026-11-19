# Work through the review in recorded stages

## Contents

- Purpose and limits
- Depth
- 1. Record the source, reader, and scope
- 2. Save reading notes
- 3. Save checks and candidate problems
- 4. Reconcile reader observations
- 5. Decide which candidates survive checking
- 6. Prepare repairs and check their meaning
- 7. Check readiness and produce the report
- Interrupted work, corrections, and smaller tasks
- Field vocabularies

## Purpose and limits

Use this procedure for every substantive review, structure plan, or revision. Its purpose is to make the actual checking work inspectable and to keep early impressions from becoming recommendations without verification. A long final answer, an announced plan, or a list of checks to perform does not substitute for completed notes.

Keep the working notes outside the installed skill. Complete a stage, save it, inspect it, and record it with the helper before working on the next stage. Use separate tool actions for these transitions. Correct errors found in the current draft notes before recording that stage; use a new draft filename when retaining the correction history is useful. Already recorded notes and original reader observations stay unchanged. Do not ask a model or a script to generate all stages and the final report together and then record them one by one. Scripts may organize and validate completed notes; they do not perform the semantic reading merely by populating fields.

Record quotations, short local paraphrases, actual observations, checked locations, unresolved questions, and concise reasons for decisions. These are external work products, not a transcript of private reasoning. Be detailed about the manuscript while keeping each note relevant to the check. Do not inflate a simple passage into a long abstract summary.

The helper checks saved artifacts, source consistency, declared coverage, order of recording, and correspondence to the final findings. It cannot establish that a model thought carefully, cannot rule out a report drafted elsewhere beforehand, and cannot certify scientific truth or human reading difficulty. Apply the procedure honestly and state the checks actually performed.

All paths below are examples. Replace `${CLAUDE_SKILL_DIR}` and manuscript/run filenames with real locations. Use a new journal directory; keep original source files intact.

## Depth

A review is quick, standard, or deep (SKILL.md, "Select the task, depth, and execution mode"). The stages, their order, and the journal schema are the same at every depth. The depth changes what two stages record:

| Depth | Reconcile records | Repairs record |
| --- | --- | --- |
| quick | The prefix checkpoints: the fixed schedule and any the editor added. | The action of each repair. An edit has `proposed_wording: null` and a `wording_withheld_reason`; no audit is attached. |
| standard | The same. | Proposed wording for each accepted item where the manuscript supplies the facts, each with its `audit_path`. |
| deep | The same, and at least one `masked-sequential` checkpoint, with branches where they were asked. | As standard. |

The fixed schedule is one prefix reader at the end of every section; `notes_build.py section-ends` writes it as a checks fragment before the checks stage is built (workflow.md, "Checkpoint design"). Build the repairs of a quick review with `notes_build.py repairs --no-wording`: it sets the wording of every edit to null, fills in the reason, and reads no audit. The review states its depth in `depth`. `notes_build.py review` takes it from the fragment or derives it from the records, and `check-final` refuses a depth the records do not support: `quick` with proposed wording or a masked checkpoint, `standard` with a masked checkpoint, `deep` without one. A standard review may end with no wording when every audit failed.

The depth is not part of the scope record, so it can be raised without a new start. To extend a finished review, start a corrected run: `review_steps.py corrected-run --restart-at repairs` for standard, `--restart-at reconcile` for deep (`--restart-at checks` when the plan must gain recall questions). Then record the stages from that point afresh.

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

**Paragraph relations.** In each included block's `structure_check`, describe how its proposition connects to preceding material, what it establishes for subsequent material, and whether its claim matches the evidence and scope. Point to actual locations. Check dispersed information by naming the pieces that must be combined. Do not replace this with a generic rating such as “flow is good.” For a suspected central sentence that reads smoothly but may say little, add a `proposition_checks` entry (review-rubric.md, section 3b): its exact `anchor`, the `proposition` it conveys from the visible text, the `supplied_relations` you added yourself and their source, its `function` (`fact`, `definition`, `premise`, `comparison`, `preview`, `reminder`, `navigation`, `other`), and whether the missing relation is `needed_here`. The entry is optional; the helper checks its fields and that the anchor quotes that passage.

**Claims, rules, and citations.** In each included block's `claim_check`, record three things, each as a nonempty string, each saying "none in this block" with the reason when that is the finding: `claim_strength`, the absolute or comparative claims (nothing, first, every, widespread, outperforms, always) with the evidence the text gives for that strength; `counterexample`, one attempt to break each formula, metric, or decision rule the block states, with the case tried and what happened; `citation_check`, whether a citation in the block carries a comparison or novelty claim and therefore needs its source checked (see the citation spot-check in workflow.md). The helper refuses a passage without all three. A block with no claim, no rule, and no citation still gets the three lines; they are then short.

**Words and sentences.** In `wording_check`, inspect terms and phrases in context. For a questioned expression, record what it means, whether a name is necessary, and what an ordinary description could express. Check subjects, actions, noun strings, nesting, qualifications, and reference consistency. Also record why a passage or necessary term should remain when that is the result; do not create an issue just to demonstrate effort. Include at least one exact quote from the current block in each passage's `evidence`; add other supporting source anchors when relevant.

**Checkpoint plan.** This stage is the editor's independent review: record it before consulting any reader observation, and the helper refuses `checkpoints`, `branches`, or `observations` here. Save the selection plan in `checkpoint_plan`, one entry per intended checkpoint with `id`, `anchor`, `mode`, and `reason`; state whether the selection used the full text. The anchor quote of a prefix entry ends where its reader stops and occurs once in its block. An empty plan is valid when no independent reader will be run. A `masked-sequential` entry may carry `recall_questions`, the closed questions the masked reader will answer from its notes alone (masked-reading.md section 5b): each with `id`, `after_block` (an included block), `question`, `options` (at least two, exactly one of them the verbatim `I don't know`), and `answer` (one of the other options). The helper checks their shape here; the answers never reach a reader. Dispatch readers only after this stage is recorded, following workflow.md and masked-reading.md; `dispatch_reader.py prefix-batch` refuses a journal whose checks stage is not recorded.

**Candidates.** Give each possible issue `id`, `anchor`, `question`, and `evidence`, where evidence is an array of exact source anchors. A question such as “Does the reader need the formal equation at this sentence?” is a candidate, not proof that the equation is too late. Keep the provenance of a reader question in the surrounding notes. Combine obvious duplicate symptoms or keep them for explicit merging in the next step. Zero candidates is valid.

**Open questions from reading.** Every `open_questions` entry saved in the reading notes must reach this stage: either a candidate carries it (its `question` quotes the open question verbatim, or its optional `open_questions` array lists it as `{"block_id", "question"}` exactly as the reading note has it) or `no_candidate` lists it with `block_id`, `question`, and a `reason` why no candidate follows from it (answered by a later passage, ordinary background, outside the review's scope). The helper refuses checks notes that leave an open question in neither place, a `no_candidate` entry for a question reading never raised, a question listed twice, or a `no_candidate` entry for a question a candidate already carries. `no_candidate` is required; an empty array is valid when reading raised no open questions.

Inspect the saved source anchors and the check of every included block, then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage checks --notes run/checks.json
```

## 4. Reconcile reader observations

Use [the reconcile template](../assets/notes/reconcile.json). Record this stage after the checks stage is frozen and after every reader run it cites is complete. It binds the reader evidence to the journal and records the editor's classification of each observation; it does not reopen the independent checks.

**Checkpoints.** Include each completed checkpoint in `checkpoints`, with `id`, `anchor`, actual `mode`, `figure_policy` (`text-only` for prefix packets, which carry no figures; for a masked run the policy its case declares, `strict-prefix` or `reference-jump`), `result`, `limitation`, and its attachment. A `fresh-prefix` or `bounded-request` checkpoint must give `plan_id` naming the checks-stage plan entry with the same anchor and mode, plus `packet_path` and `observation_path`. In a `notes_build.py reconcile` fragment such a checkpoint may be the shorthand `{id, plan_id, events_dir, limitation}`: the builder takes the two paths from `prefix-batch.json` in that events directory, or from the files of the labeled call. A `masked-sequential` checkpoint gives `run_path` and `record_sequence`; the helper replays the run, requires every case block to be text of this source, and requires the anchor quotation to be text the reader had seen by that event. When the run had recall events, list the ones this checkpoint draws on in `recall_sequences`; the helper replays them and binds their packet, response, questions, and receipt files. A `full-context` checkpoint is an editorial hypothesis and needs no attachment. If no checkpoint was completed, use an empty array and give `no_checkpoints_reason`. Do not mark a planned or unavailable call as completed. Retain original bytes and controller records separately. The template lists one entry per mode; delete the entries you do not use.

**Branches.** Include each targeted question answered through the branch procedure in masked-reading.md in `branches`, with `id`, `run_path`, `branch_sequence`, `result`, and `limitation`. The helper replays the branch against its display event and binds its files.

**Observations.** For each useful reader observation, record `id`, `source_id` (a checkpoint or branch id above), the original `reader_question`, a `classification` from the table in masked-reading.md (`absent_at_critical_use`, `resolved_later`, `needed_again`, `lost_in_notes`, `interpretation_changed`, `inferred_connection`, `asset_lookup`, or `adequate`), the `editor_check` that produced it with exact `evidence`, and `candidate_id` when it supports a checks candidate or a candidate added here, else null. Keep the original reader question; the editor's later explanation does not replace it. A classification that dismisses the reader's question shows the words that answer it. `resolved_later` gives `resolved_by`, an exact anchor of the manuscript words that answer the question; they must begin after the checkpoint's anchor, because words the reader had already passed make the observation `adequate` or `needed_again`. `adequate` gives exactly one of two proofs: `resolved_by`, quoting words that end by the checkpoint's anchor (text the reader had at that point), or `background`, an exact quotation from the entry of the journal's reader profile that covers the question. Every other classification leaves both null. The helper checks the quotations and their position; when the observation's source is a branch, which has no anchor in this record, it checks only that the quotation is exact text. If you cannot quote the answer, the question is not answered: classify it `absent_at_critical_use`. A recall answer that was wrong or `I don't know` is recorded the same way: `source_id` is the masked checkpoint whose `recall_sequences` include that recall, `reader_question` is the recall question followed by the choice the reader made (for example `Recall 2, Q3: what was held fixed? — I don't know`), and `editor_check` says what the receipts show: the event at which the note that carried the answer was dropped or revised (`notes_dropped`, `since`, `sources`), or that the source had not stated it by then. Classify it `lost_in_notes` only in the first case.

**Candidates.** Add candidates that the observations raise to `candidates`, with new ids. Do not reuse or rewrite a checks candidate.

Then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reconcile --notes run/reconcile.json
```

## 5. Decide which candidates survive checking

Use [the decisions template](../assets/notes/decisions.json). Reopen the relevant source locations; do not decide solely from a summary of the candidate.

**Independent challenge.** A `countercheck` written by the editor who raised the candidate rarely rejects it. After the reconcile stage is recorded and before deciding, put every candidate to a fresh, tool-less challenger that sees the whole manuscript and the reader profile and, for each candidate, only its id, its exact anchor, and the reader's question; it does not see the diagnosis or the evidence the editor collected. Save the request as `{"candidates": [{"candidate_id", "anchor", "question"}]}` and run

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dispatch_reader.py challenge --events run/events-challenge --input run/challenge-input.json --index run/index.json --profile run/reader-profile.json --out run/challenge.json --model MODEL
```

One call may carry several candidates (a section's worth; about ten is a reasonable batch), and the same model as the readers is used. The challenger answers each candidate with one verdict and a reason: `stands`; `answered`, quoting the manuscript words that answer the question (the helper checks the quotation and works out `answer_position`: `before` the anchor, `after` it, or `at` it); `not_material`; or `duplicate` of another candidate in the request. The result has the shape of [the challenge template](../assets/notes/challenge.json).

Record the file on each challenged decision as `challenge_path`. A challenge disputes the candidate when the verdict is `not_material` or `duplicate`, or `answered` by words the reader has already seen (`before` or `at`). An answer that comes only `after` the anchor does not dispute it: it confirms a problem of order. A decision that keeps a disputed candidate (`accepted` or `needs_author`) must say why in `challenge_response`; the helper refuses it otherwise. The editor may overrule the challenger, and a challenger that is unsure is told to answer `stands`, so a dispute is a reason to look again, not a ruling. The helper requires a challenge of every candidate: each decision carries `challenge_path`, and the stage is refused when one is missing. `review_steps.py challenge-input --journal run/journal --index run/index.json --out run/challenge-inputs` writes the input files from the recorded candidates in batches of ten (`--batch N`), and `notes_build.py decisions --challenge run/challenge-01.json --challenge run/challenge-02.json` fills in each decision's `challenge_path` from the files. Only when no fresh challenger can be run in this environment, challenge none and say why in `no_challenge_reason` at the top of the decisions notes, as for a reader that could not be dispatched; the report then says that candidates were not independently challenged. Journals written before schema 7 are checked when a challenge is present and never require one. A challenger is a model reading the same text; its verdicts are evidence for the decision and are not findings about human readers.

For every candidate from the checks and reconcile stages, record `candidate_id`, `verification` describing what was checked, `countercheck` describing what could make the existing text adequate, exact source `evidence`, and one `status`:

| Status | Meaning |
| --- | --- |
| `accepted` | The checked evidence supports a specific writing problem. Record `category`, `diagnosis`, `close_criterion`, `severity`, and `confidence`. |
| `rejected` | An earlier explanation, expected background, reasonable inference, honest preview, or another checked fact makes the proposed criticism unwarranted. State the actual reason. |
| `merged` | It is a symptom of another retained candidate. Set `merged_into` to that accepted or author-question candidate; do not count it again. |
| `needs_author` | Available text cannot settle a necessary scientific or interpretive question. Record `category`, `diagnosis`, `close_criterion`, `severity`, and `confidence`, explicitly describing the uncertainty rather than a confirmed defect with an invented solution. |

**Close criterion.** Every `accepted` and `needs_author` decision states, in `close_criterion`, one sentence saying what the target reader can state or find once the passage is fixed, for example “The reader can say what the window measures before reaching the threshold in B0003.” A later re-test puts exactly that to a fresh reader, so write it about the reader and not about the text's defect: the helper refuses a kept decision without one, one that repeats the `diagnosis`, and one on a `rejected` or `merged` decision, which has nothing left to close. If you cannot say what the reader could do afterwards, the diagnosis is not yet about the reader; go back to it.

**Severity.** Give every kept decision one of three levels, named by what happens to the target reader (review-rubric.md, section 7): `misleads`, `hinders`, or `minor`. Assign them in one pass over all kept candidates, after every challenge result is in, and use the two recorded facts as evidence: at how many reader checkpoints the question was raised independently (`review_steps.py challenge-input` prints this count per candidate in `support`) and the challenger's verdict. The report lists at most ten findings that mislead or hinder as priorities and moves minor items to an appendix, so the level decides where a finding appears. Journals written before schema 7 keep the earlier `critical`, `major`, `minor`, `optional`.

The countercheck is substantive. “I considered alternatives” is insufficient. For example, inspect whether the score's sign is already explained before criticizing the later formula; inspect whether a repeated limitation serves a different purpose before calling it redundant. Do not manufacture rejected candidates to fill a quota.

**Pairwise judgments.** A decision sometimes turns on “which of two readings (or two passages, or two candidate diagnoses) is better.” Human agreement on such preferences is low (Jourdan et al. report κ 0.33 among expert annotators) and a model judge prefers whatever comes first or sounds like its own prose, so a pairwise judgment counts only when it survives an order swap: ask the question twice, with the two options in opposite order, in separate calls; if the two answers disagree, record the decision as undetermined (reject the candidate or make it an author question) rather than taking either answer. Write the `verification` as the describable difference (shorter, earlier, one fewer undefined term, a condition stated or missing), not as “better.”

Save a decision for every actual candidate, including rejected and merged items. When none arose, keep an empty list and a meaningful summary of the checks performed. Then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage decisions --notes run/decisions.json
```

## 6. Prepare repairs and check their meaning

Use [the repairs template](../assets/notes/repairs.json). Cover exactly the accepted and author-question items. Draft concrete sentence replacements or moves after the issue has been assessed, not while treating the initial impression as established.

For each retained candidate, record:

- `candidate_id`: the retained candidate being addressed.
- `kind`: `edit`, `move`, or `author_question`.
- `edit_scope` (required when `kind` is `edit`, null otherwise): the one thing the edit changes, from `pointer` (a referent or cross-reference), `apposition` (a short explanation set beside a term), `term` (one word or phrase replaced), `reorder` (clauses of one sentence reordered), `split` (one sentence split into two), or `sentence` (the whole sentence rewritten). Write the wording for that scope only and leave numbers, conditions, hedges, and causal words outside the scope untouched; the audit catches what the scope did not name. General models over-edit when asked to “polish,” so the instruction that produces a wording is the scope plus the one purpose, never “improve this paragraph.”
- `action` and `proposed_wording`: the full replacement or the precise move from one location to another; use null for wording when no justified replacement is possible. The action names at least one block id (B0003) with its verb, so the report can say where to act; the helper refuses an action without a block id.
- `wording_withheld_reason`: required when `kind` is `edit` and `proposed_wording` is null; say why no replacement is offered (the manuscript does not supply the fact, the audit rejected every attempt, the fix depends on an author decision). Omit it or set it to null in every other case; the helper refuses it beside a proposed wording, on a move, or on an author question. An accepted edit without wording and without this reason is refused, so an item cannot be accepted and then left to the author without saying so.
- `scientific_fidelity`: the source-to-repair check of claim strength, numbers, units, conditions, negation, assumptions, population, outcomes, time, and uncertainty. Name the relevant facts rather than writing only “preserved.” If intentionally narrowing an unsupported claim, say so.
- `new_terms_check`: whether the proposed text introduced a label, acronym, compound noun, or unnecessary synonym, and what was done about it.
- `affected_block_ids`: a nonempty list of known locations requiring corresponding changes or the author's clarification; inspect definitions, subsequent uses, and relevant captions.
- `author_question`: the actual question when the text does not supply a scientific decision, written as a question that ends with a question mark; the helper refuses a statement. A `needs_author` item must remain a question with no asserted replacement.
- `audit_path`: for every repair with `proposed_wording`, the file written by an independent fidelity auditor; null otherwise.
- `compare_path`: the `difficulty_features.py compare` result for the wording, required when `edit_scope` is `sentence` and optional for the other scopes; null when there is no wording. `heavy_edit_reason`: required when that comparison reports `edit_weight` `heavy` (the wording replaces more than half of the original's words), saying why the sentence could not be changed in smaller steps; null otherwise. A heavy edit without a reason is refused: split it into several repairs with one purpose each, or state the reason.

**Independent fidelity audit.** Do not accept your own fidelity statement for a proposed wording. Give a fresh, tool-less auditor only the exact original text (as anchors), the proposed wording, the basis facts with their manuscript locations, and the list of hedges found in the original, using [the audit prompt](../assets/prompts/fidelity-audit.txt); it must not see the editor's reasons, and the prompt tells it that the rewrite was submitted by someone else. Save its JSON unchanged in the shape of [the audit template](../assets/notes/fidelity-audit.json). The helper records a repair with wording only when the audit verdict is `preserved` and none of the ten properties (claim strength, quantifiers, assumptions, population, measured outcome, time horizon, numerical values, uncertainty, causal versus associative wording, added qualification) is `changed`; otherwise revise the wording and audit again, or set `proposed_wording` to null and ask the author. Keep a rejected audit; do not edit it.

Three rules follow from what rewriting models are known to do. (1) Hedges are compared one by one, not by impression: the controller lists every occurrence of a hedge or condition it finds in the original (`hedges_in_original`: H1, H2, … with the phrase and its clause; the phrases are may, might, suggest, associated with, under these conditions, in 3 of 5 cases, and the rest of the list in `scripts/review_packets.py`), the auditor reports each occurrence by id as `kept`, `strengthened`, or `removed` in `checks.hedges`, quotes in `kept_as` the words of the proposed wording that carry each kept one, and any occurrence not kept makes `claim_strength` or `uncertainty` `changed`. The helper refuses an audit that skips an occurrence, whose `kept_as` is not in the proposed wording or contains neither a listed hedge nor the original phrase, in which two kept occurrences rest on the same words (two “may” kept by one, or a surviving condition standing in for a deleted “may”), or that reports one removed without a changed property; the audit records the occurrence list and any extra patterns (`hedge_patterns`), and the helper finds the same list again from `original`. `dispatch_reader.py audit` applies the same rules before writing the file, so the auditor is asked to correct a bad report rather than the record failing later. These checks hold the report to the texts; they cannot see a hedge that survives on a different claim, which the audit prompt asks the auditor to judge. Rewriting models turn “may” into “is” far more often than the reverse, and the drift accumulates across rounds. (2) Every round is audited against the manuscript. A second or third wording after a failed audit is compared with the original anchors, never with the earlier wording; the audit carries `audit_round` (1, 2, …) and its `original` must quote text at the candidate's anchor or at one of the candidate's or decision's recorded evidence anchors (an apposition added at a term's first mention quotes the evidence anchor there), which the helper checks. (3) The tenth property, `added_qualification`, catches the opposite drift: a qualifier, concession, or disclaimer the wording adds without a basis entry that locates it in the manuscript is `changed`, however cautious it sounds. Models asked to “check the claims” add such hedges; cautious-sounding is not the same as faithful.

On Claude Code, `python3 ${CLAUDE_SKILL_DIR}/scripts/dispatch_reader.py audit --events run/events --input run/C04.input.json --out run/C04.audit.json --round 1 --model MODEL` (the model the user chose; see SKILL.md) sends the prompt and the input (with `hedges_in_original` added) to a tool-less `claude -p` call, checks the returned shape, and writes the audit with `audit_round`, `hedges_in_original`, `hedge_patterns`, and `auditor` filled in; use `--round 2` for the revised wording of the same candidate and `--hedge-list` to add patterns for a field's own hedging phrases.

**Choosing between two wordings.** When two candidate wordings both pass the audit and the choice between them is a preference, apply the pairwise rule of section 5: compare them in both orders in separate calls, treat disagreement as undetermined (keep the shorter or the one closer to the original, and say so), and describe the difference rather than calling one better.

**Feature comparison.** `python3 ${CLAUDE_SKILL_DIR}/scripts/difficulty_features.py compare --lexical-only --input run/C04.input.json --out run/C04.features.json` (or `--repairs run/repairs.json` for every repair with wording, taking each repair's original from its `audit_path`) needs no spaCy and reports, for the original anchors and the proposed wording, the share of the original's words the proposal replaces (`edit_ratio`, with `edit_weight` light up to 0.2, medium up to 0.5, heavy above), the type-token ratio, and the mean word length, with advisory flags when the proposal is less varied or uses longer words (rewrites by language models tend both ways). With spaCy installed and without `--lexical-only`, it also counts the eight parse-based difficulty features and flags any feature the proposal has more of. The edit ratio is the one measure with a consequence: a `sentence`-scope edit must attach the result as `compare_path`, and a heavy edit needs `heavy_edit_reason` or a split. The helper recomputes the edit ratio from the two texts in the comparison and, in a schema 6 journal, refuses a comparison whose original is not the text the fidelity audit quoted. A heavy edit is not a change of meaning; it needs a reason, not a rewrite. The other flags are reasons to look at the wording again (a new noun stack, an object relative, a noun used as a verb, a longer or flatter vocabulary); they are not gates and do not change the audit requirement above.

For a structure plan, record exact moves and resulting dependencies. For requested revision, preserve the source, re-index the proposed version, and perform the relevant rechecks described in workflow.md; keep those results with the repair evidence. Do not claim the revised document was independently reread if it was only compared by the original editor.

**Keep, revert, and when to stop.** Leaving a passage as it is, or recommending a return to an earlier wording, is a successful outcome when the evidence shows that the text works; a candidate rejected because the existing text is adequate needs no repair to justify the review. Continue revising only while a located, evidenced problem remains, not because the text could be more polished. An optional change that shows no gain is labeled optional and does not start another round.

Save and inspect the proposed changes and their meaning checks, then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py record --journal run/journal --index run/index.json --stage repairs --notes run/repairs.json
```

## 7. Check readiness and produce the report

Before compiling the final review, run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py ready --journal run/journal --index run/index.json
```

Resolve a failed readiness check before representing the review as complete. The check cannot approve the substantive quality of the notes; inspect that separately. Its output lists `overlapping_wordings`: blocks that two or more repairs with proposed wording affect. Each wording was audited alone, so for each listed block read the text with all of those wordings applied (in a copy, never in the author's file) and check that no definition, reference, example, or reminder a later passage relies on was lost and that the wordings do not overlap; say in the `scope_note` which combinations were checked and which, if any, are alternatives rather than edits to apply together. Retain the receipt.

**The structured review.** Compile it from the recorded decisions and repairs, using the output contract and adding `candidate_id` and `decision_status` to every final issue. Carry the recorded category, diagnosis, close criterion, severity, confidence, action, scientific-fidelity statement, and proposed wording into their corresponding fields. Preserve author questions as questions. Use only the candidate anchor or evidence already recorded for that candidate or its decision in final evidence. Every accepted and author-question candidate is a final issue; a rejected or merged candidate is not.

**The frame.** A review of a schema 7 journal also carries three top-level fields. `scope_note` says what was read (edition, sections or blocks, reader runs) and what was left out, in ordinary words. `not_assessed` lists what the review does not judge; it includes `whether the claims are true`, `whether the evidence is sufficient`, and `whether the work is novel`, and may add more. `priority_list` names, by candidate id, the accepted findings that mislead or hinder: all of them when there are ten or fewer, otherwise ten, `misleads` before `hinders`, and never a `hinders` finding while a `misleads` finding is left off. A minor item or an author question cannot be on it. Each issue also carries the two recorded facts behind its severity: `reader_sources`, the number of reader checkpoints and branches whose observations name the candidate, and `challenge`, the challenger's verdict or null when no challenge was run.

A review may also state its `depth` (see "Depth"); a review without it, as 0.21.0 wrote, is still accepted.

`notes_build.py review` (workflow.md) writes all of this from a fragment that gives `summary` and `scope_note`: it inserts the depth the records support (unless the fragment gives `depth`), the three fixed `not_assessed` entries, each issue's recorded support, the candidate's first question as `initial_reader_question`, and the priority list ordered by severity and then by position in the document, unless the fragment gives its own `priority_list`. Then run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_steps.py check-final --journal run/journal --index run/index.json --review run/review.json
```

`check-final` refuses a review whose issues differ from the saved decisions and repairs, whose recorded support differs from the journal, whose stated depth the records do not support, or whose frame breaks a rule above, and names the field.

**The report.** Render it from the checked review; do not write it by hand:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/notes_build.py report --journal run/journal --index run/index.json --review run/review.json --out run/report.md
```

The command runs `check-final` again and renders nothing when it fails. The report gives the scope note and what was not assessed; the depth and what it leaves out; one line of counts taken from the records (blocks read, reader checkpoints by mode, candidates accepted, put to the author, merged, and rejected, whether the independent challenge was run, fidelity audits attached); the priority list; every question for the author, those of minor items included; and every finding that misleads or hinders exactly once, with the reader's first question, the diagnosis, the action, the proposed wording when there is one, the meaning check, the close criterion, and the support. Minor items go to `report-minor.md` beside the report (`--appendix PATH` chooses another file), which is written only when there is a minor item. No model writes any part of the report, so do not edit it, add sections, or repeat a finding in a second place; [the layout](../assets/review-report.md) is for a runtime that cannot run the helper. Tell the user, in their language, where the report and the supporting records are. A rejected candidate may explain why something should stay; it must not reappear as a criticism.

The counts line shows how many candidates were rejected. When none was, say why in one sentence of the `scope_note` (for example, a short excerpt with few candidates, or every candidate confirmed by a reader observation), because a decisions stage that rejects nothing has not shown that its counterchecks could fail.

Describe, do not grade. The report prints the recorded diagnosis, action, and meaning check, so write those to say what differs between the original and a proposed wording or order (shorter by six words, the definition now precedes its first use, one fewer undefined term, the condition is stated), never that a version is “better,” “clearer,” or “improved”: a preference judgment has low agreement even among human experts, and a model's preference tracks order and its own style. A pairwise judgment enters the records only when both orders agreed (section 5); otherwise record that the choice is undetermined and what would settle it. Every recommendation names its block and the verb of the action, and every author question is a question; the helper refuses a final issue that lacks either.

If a new diagnosis appears while compiling the review, return to the relevant checks in a corrected run. Do not simply add it to the review or invent earlier notes for it.

## Interrupted work, corrections, and smaller tasks

- **Resume:** inspect the source binding, saved notes, and actual recorded stage. Continue from completed work; label any reconstructed record as reconstructed, not an original observation.
- **Correct:** a current-stage draft may be fixed before it is recorded; this alone does not require a new journal. After changing an already recorded note, keep the old journal and start a corrected run with `review_steps.py corrected-run --from run/journal --index run/index.json --out run/journal-2 --restart-at decisions --reason "..."`: it copies the start receipt and the stages before `--restart-at` byte for byte (so their receipts stay the originals and the chain is verified on both sides), writes `supersedes.json` naming the old journal, the restart stage, and the copied receipt hashes, and leaves the stages from `--restart-at` on to be recorded afresh; the ready receipt of the new journal carries the supersedes record. Check the copied stages before relying on them, and redo every dependent decision and repair. A changed reader profile, reading edition, or scope is a new source binding: start a new run with `start`, not a corrected run. Do not manually update hashes or edit receipts to suppress a mismatch. Preserve original reader observations even when their parsed or corrected versions need separate files.
- **Sampled review** (task `quick_review`): select and declare a smaller set of passages, then complete the same stages for that set. A one-paragraph task needs one substantive reading entry, not an artificial full-manuscript analysis.
- **Unavailable execution:** if files or helper execution are unavailable, write the same stage records in successive sections of the working document and state that automatic checks were not run. Do not claim file validation or independent reader execution that did not occur.
- **Unavailable content:** record the omission and its consequences. Do not fill uncertain fields with guesses to pass a check.

No stage requires a fixed number of problems or edits. An adequately supported decision to keep the text is a successful outcome.

## Field vocabularies

The helper accepts only these values; anything else is refused with a message naming the field.

| Field | Values |
| --- | --- |
| `scope.task` | `review`, `structure_plan`, `revision`, `quick_review` |
| `checkpoint_plan.mode`, `checkpoint.mode` | `fresh-prefix`, `bounded-request`, `masked-sequential`, `full-context` |
| `checkpoint.figure_policy` | `text-only` (required for `fresh-prefix` and `bounded-request`), `strict-prefix`, `reference-jump` (a masked checkpoint must repeat its run's `asset_policy`; a `full-context` checkpoint may declare any of the three) |
| `checkpoint.record_sequence` (masked) | an integer from 1 to the number of recorded display events of the run; `branch.branch_sequence` likewise names a recorded branch; `checkpoint.recall_sequences` (masked, optional) names recorded recall events once each |
| `checkpoint_plan[].recall_questions` (masked-sequential, optional) | each `{id, after_block, question, options, answer}`: `after_block` an included block, `options` at least two distinct strings with exactly one `I don't know`, `answer` one of the others |
| `observation.classification` | `absent_at_critical_use`, `resolved_later`, `needed_again`, `lost_in_notes`, `interpretation_changed`, `inferred_connection`, `asset_lookup`, `adequate` |
| `observation.resolved_by`, `observation.background` | `resolved_later`: `resolved_by` is an exact anchor that begins after the source checkpoint's anchor. `adequate`: exactly one of `resolved_by` (ends by that anchor) or `background` (an exact quotation from the frozen reader profile). Null on every other classification. For a branch source the anchor is checked for exact text only |
| `decision.status` | `accepted`, `needs_author`, `rejected`, `merged` (with `merged_into` naming an accepted or needs_author candidate) |
| `decision.category` (accepted and needs_author) | `late_prerequisite`, `unavailable_prerequisite`, `ambiguous_reference`, `scattered_information`, `retrieval_burden`, `missing_relation`, `paragraph_function`, `terminology_scope_drift`, `unsupported_claim`, `unnecessary_term`, `word_choice`, `sentence_structure`, `other` |
| `decision.close_criterion` (accepted and needs_author) | one nonempty sentence, different from the `diagnosis`; absent or null on `rejected` and `merged`; the final issue repeats it exactly |
| `decision.severity` | `misleads`, `hinders`, `minor` (journals before schema 7: `critical`, `major`, `minor`, `optional`; a reader observation's own `severity` keeps those four) |
| review `scope_note`, `not_assessed`, `priority_list` | a nonempty string; a list that includes `whether the claims are true`, `whether the evidence is sufficient`, `whether the work is novel`; candidate ids of accepted `misleads` and `hinders` issues, at most ten and all of them when fewer, `misleads` first, no more severe finding left off |
| review `depth` (optional) | `quick`, `standard`, `deep`. `quick`: no repair carries proposed wording and no masked checkpoint is recorded; `standard`: no masked checkpoint; `deep`: at least one. Checked by `check-final` when present |
| final issue `reader_sources`, `challenge` | the number of distinct checkpoints and branches whose observations name the candidate; the challenger's verdict or null; both recomputed by `check-final` |
| `decision.confidence` | `high`, `medium`, `low` |
| `repair.kind` | `edit`, `move`, `author_question` |
| `repair.edit_scope` (edit only) | `pointer`, `apposition`, `term`, `reorder`, `split`, `sentence` |
| `repair.action`, `repair.author_question` | an action names a block id of this source; an author question ends with a question mark |
| `audit.verdict` | `preserved`, `changed`, `undetermined`; each of the ten `audit.checks` (the nine of 0.17 plus `added_qualification`) is `same`, `changed`, or `not_applicable` |
| `challenge.challenges[].verdict` | `stands`, `answered`, `not_material`, `duplicate`; `answer_position` is `before`, `after`, `at`, or null (filled in by the helper) |
| `audit.checks.hedges[]` | `{id, term, status, kept_as}`: one entry per occurrence in `hedges_in_original`; `status` is `kept`, `strengthened`, or `removed`; `kept_as` quotes the proposed wording for a kept occurrence and is null otherwise (schema 5 journals also accept the earlier `{term, status}` entries) |
| `audit.audit_round` | a positive integer: 1 for the first wording of a candidate, 2 for the revision after a failed audit, and so on |
| `audit.auditor.mode` | `bounded-request`, `fresh-prefix`, `full-context` |
| comparison `lexical.edit_weight` | `light` (edit ratio up to 0.2), `medium` (up to 0.5), `heavy` (above 0.5; the repair needs `heavy_edit_reason`) |
