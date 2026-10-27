---
name: review-academic-writing
description: Review academic manuscripts and research proposals for concept order, argument structure, paragraph function, reader effort, scientific fidelity, word choice, and sentence construction. Prefer common words and natural-language explanations; identify unnecessary jargon, coined terms, acronym proliferation, and dense noun phrases. Give source-anchored diagnoses and precise edits. Use for writing reviews, panel-readiness checks, structure planning, rewriting, AI-like prose, 生僻词, 不要造新术语, 用词句式, 先用后定义, 概念分散, or 论证衔接. Support fresh-context prefix checks and experimental masked sliding-window reading with bounded notes when requested; label full-context review clearly.
---

# Academic Writing Review

Evaluate what the specified reader can understand at each point, what they must retrieve or infer, and whether a repair preserves the science. Produce an actionable review rather than an overall clarity score. Treat this workflow as research-informed and experimental; distinguish model-predicted difficulty from observed human difficulty.

## Complete and save the work before drafting the report

Treat review as a sequence of recorded tasks. Do not jump from reading the manuscript to generating recommendations. Read [references/staged-review.md](references/staged-review.md) before beginning any review or revision and follow its stage requirements.

| Order | Complete and save | Required before proceeding |
| --- | --- | --- |
| 1. Source and scope | Verified source index, reader profile, reading path, included passages, omissions, and extraction limits. | The source and the actual scope are explicit. |
| 2. Reading notes | A substantive note for every included block: its meaning, role, required information, new information, and unresolved questions. | Every included block is accounted for; a generic statement that it was read is insufficient. |
| 3. Checks and candidates | Concept introduction records, paragraph relations, wording and sentence checks, the checkpoint selection plan, and candidate issues, recorded before any reader observation is consulted. | Checks include what is already clear; candidates are still provisional. |
| 4. Reconcile reader observations | Frozen prefix, masked, and branch observations, each classified against the independent checks, plus any candidates they add. | The independent checks are already recorded; observations are evidence to classify, not findings. |
| 5. Decisions | For each candidate, inspect supporting and contrary evidence; accept, reject, merge, or identify a question for the author, with a short reason. | Every candidate has a disposition; a first impression is not a confirmed defect. |
| 6. Repairs and verification | A specific repair or author question for each retained item, affected locations, an independent fidelity audit for every proposed wording, and checks of newly introduced terminology. | Each proposed repair is supported by a recorded decision and its meaning has been checked. |
| 7. Final report | Prioritized findings and edits drawn from the completed records. | Run the readiness check before drafting, then validate the report against the records. |

Create actual intermediate files outside the skill directory. Save and inspect one stage before starting the next; use separate tool steps when files are available. Do not generate all stages and the final report in one call, write a finished review first and backfill notes, or treat a plan/checklist as evidence that a check happened. Keep notes factual: source quotations, concise paraphrases, observed questions, checks performed, and short decision reasons. Do not substitute an internal monologue or a lengthy general summary for these records.

Use `scripts/review_steps.py` to record stages and check readiness as described in staged-review.md. Its checks concern files, order, coverage, and correspondence; they cannot prove a model's private reasoning, diligence, or the truth of a diagnosis. If the runtime cannot execute the helper, preserve the same staged notes in the working document and disclose that automatic checks were unavailable. Continue the substantive review.

Commands in this file and in `references/` write script paths as `${CLAUDE_SKILL_DIR}/scripts/...`; Claude Code replaces the variable with this skill's directory when it loads SKILL.md. Where it appears unexpanded (Codex, a manual copy, or a reference file), use the directory that contains this SKILL.md in its place.

For quick or narrowly scoped review, reduce the declared coverage and note detail in proportion to the task; retain the sequence and evidence requirements. Do not create candidate issues or edits to fill a quota. Keep full-editor notes separate from the inputs to independent prefix readers. When resuming interrupted work, verify saved records before continuing. Draft notes in the current stage may be corrected before recording it. If a new finding or an earlier error affects an already recorded stage, preserve the old run and start a corrected run with the affected downstream work rechecked; never quietly rewrite the frozen history.

## Select the task and execution mode

- Use **review** by default: diagnose and suggest changes without editing the source manuscript.
- Use **structure plan** when asked to reorganize: specify paragraph order, concept prerequisites, claim introduction, and figure placement.
- Use **revision** only when the user requests rewriting or source changes. Preserve a source version and show the proposed change.
- Use **quick review** when requested: prioritize the central argument and disclose the passages sampled.

Record the execution mode for each component:

| Mode | Requirement | Permitted conclusion |
| --- | --- | --- |
| `fresh-prefix` | Start a worker with no inherited conversation; supply only one prepared prefix packet and generic reader instructions. Inspect the actual input and tool trace for future-text access. | Report a model's response under a controlled supplied-text boundary. |
| `bounded-request` | Make a stateless model request containing only the packet and generic instructions, with retrieval/files/tools disabled. | Report a response under the recorded request boundary. |
| `masked-sequential` | Follow masked-reading.md: a fresh context per display, current window or authorized lookup, and only finite reader-generated notes from the previous event. Attach the frozen run and any branches in the reconcile stage by run path and record or branch sequence. | Report a model's recorded changes in understanding under the supplied visibility and memory conditions. |
| `full-context` | Read the manuscript or inherit a history containing it. | Report structural diagnoses and hypothesized first-reading problems. |

Do not rename full-context role-play as an isolated audit. Do not claim a fresh subagent is a security sandbox: shared files and tools may remain accessible. If the worker accesses future text, invalidate that observation and rerun in a fresh context if possible; otherwise report the limitation and continue the structural review. Use already authorized capabilities; do not require a new API account for ordinary review.

If a complete worker tool trace is unavailable, distinguish the recorded fresh-context dispatch and supplied packet from unverified subsequent tool access. Do not claim that access was audited; continue the full-document review and qualify how checkpoint observations are used.

Read [references/workflow.md](references/workflow.md) for execution details and worker instructions. Read [references/review-rubric.md](references/review-rubric.md) before making substantive diagnoses. Read [references/wording-and-sentences.md](references/wording-and-sentences.md) for every wording review or rewrite; apply its plain-language default in all modes. Read [references/output-contract.md](references/output-contract.md) when producing structured results or using the validation script.

When the user requests a mask, sliding window, bounded memory, or subagents that read progressively, read [references/masked-reading.md](references/masked-reading.md) and use its experimental mode where the runtime permits fresh contexts. It supplements the seven recorded review stages. It does not replace the full-document argument, wording, sentence, and scientific-fidelity checks. Do not infer that an accurate paraphrase establishes clear writing, or that a neutral reader must spontaneously identify every stylistic problem.

## 1. Establish the reader and source

1. Locate the actual supplied manuscript and verify its version. Do not substitute a remembered filename, abstract, or old draft for a missing source.
2. State the document purpose, target reader, assumed background, reading path, requested depth, language, and page/word constraints. Use explicit user preferences. Otherwise assume a field-trained researcher unfamiliar with this project; for a broad proposal panel, assume a neighboring-subfield reviewer. Proceed with the stated assumption unless missing information prevents useful work.
   Distinguish the reader's communication need from the purpose stated by the manuscript. A report may clearly describe an activity or learning objective while leaving its scientific question or significance unclear; use the requested genre and assignment to decide what is required.
3. Keep the reader profile independent of unseen manuscript content. Do not put future definitions, project-specific mechanisms, or a full-document concept summary into it.
4. Convert the selected reading edition into UTF-8 Markdown or plain text with verified reading order. For a PDF, use `scripts/extract_pdf.py`: `extract` runs the local MinerU standard-tier parse (never a remote service; it stops with the server start commands when the local parse server is not ready), and `postprocess` cleans the Markdown for indexing and writes a page sidecar and extraction notes for the scope record. Otherwise use the environment's PDF/DOCX capability. Preserve headings, formulas, citations, figure/table captions, and source-page mappings. Check multi-column order and equation loss against the PDF. If the extraction is unreliable, mark the affected region and restrict conclusions there.

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/extract_pdf.py extract --pdf manuscript.pdf --out-dir run/extract --mineru "$MINERU"
   python3 ${CLAUDE_SKILL_DIR}/scripts/extract_pdf.py postprocess --input run/extract/manuscript.md --out run/manuscript.md
   ```

   When the parse server is not ready, run `${CLAUDE_SKILL_DIR}/scripts/setup_mineru.sh` first (idempotent: installs MinerU with `uv tool install` when missing, downloads the standard-tier models, disables telemetry, sets the managed parse-server mode, starts the server and waits until it is healthy; `--check` only reports). It needs `uv` and never uses MinerU's remote service. When MinerU cannot be installed or started, rerun `extract` with `--fallback pdftotext` (or `--fallback pdfplumber`). It writes a text-only edition and marks it low fidelity in `extract.json` and in the extraction notes, so the scope record says so: two-column order, subscripts, formulas, tables, and captions may be lost or merged, and there is no OCR. Check every passage used for a finding against the PDF and restrict conclusions where the text is unreliable.

   Copy the extraction notes (`run/manuscript.md.extraction-notes.txt`) into `extraction_notes` of the scope record. For masked reading, `extract_pdf.py case` builds the case from the index, the page sidecar, a word budget per window, a required `--asset-policy`, and a list of figures and tables with their pages and image paths.
5. Select the actual reading path. Include an abstract or caption in the prefix only if this reader has encountered it. Disclose missing or uninspected figures. Create a new indexed edition for a different reading path.

Use `assets/reader-profile.json` as a starting shape. Put run artifacts outside the skill folder and retain the original source.

## 2. Build a versioned map

Run the standard-library helper against the verified text:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_packets.py index --input manuscript.md --out run/index.json
```

Use the resulting block IDs and exact quotes as locations. Keep the document snapshot identifier with every finding. Treat block IDs as stable only within that snapshot; re-index after revision.

While reading, save a block-by-block account of meaning, paragraph role, entry requirements, new information, and open questions. Then build an editor-only map of the central research question, gap, contribution, claims, supporting evidence, limitations, and section functions. For each central concept, distinguish first mention **M**, earliest usable explanation **E**, and first critical use **U**. Save exact locations and sources in the checks record before deciding what is wrong, not just whether the term appeared. Keep three relation types separate: prerequisites for understanding, evidence supporting a claim, and passages affected by an edit.

Treat `E > U` as a candidate problem. Check whether expected background or a short inference from the available text already suffices. Allow an intuitive explanation before a later formal definition and allow honest contribution previews before proof or implementation detail.

## 3. Audit the reading progression

Select checkpoints at first critical uses, dense transitions, central contribution statements, and joins between aims or sections. Record the selection plan in the checks stage and record that stage before dispatching any reader; observations enter the journal only in the reconcile stage. End a checkpoint after the relevant sentence, not after a paragraph that already supplies its missing definition. Report the checkpoint coverage; do not imply an exhaustive sentence audit when only selected locations were tested. If selection used the full text, record that fact.

Create one packet per checkpoint:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_packets.py packet --index run/index.json --profile run/reader-profile.json --stop-after-text 'Exact final sentence at the checkpoint.' --out run/checkpoint-01.json
```

Use `--occurrence N` only to resolve a repeated exact quotation; use `--stop-offset N` for an explicit exclusive character boundary or `--through-block B0003` for a whole-block boundary. Check the last visible text. Never pass `index.json`, the full manuscript, editor notes, expected problems, or sibling packets to the prefix worker.

Start an independent worker with **no inherited turns**. On Claude Code, use `scripts/dispatch_reader.py`: it sends the generic prompt from `assets/prompts/` plus the packet to `claude -p` with tools, MCP servers, and settings disabled, saves the prompt, raw result, parsed JSON, and provenance (cost, session, exit code, permission denials) for every call, and runs `check-observation` (`prefix`), drives a masked run to completion (`masked`), asks a branch question (`branch`), or runs the fidelity audit (`audit`). It applies the correction protocol in masked-reading.md and writes `summary.json`. With a Codex subscription, add `--runtime codex` (a `codex exec` call that ignores the user's configuration and is rejected if the model runs any command). On a server with an OpenAI-compatible chat-completions endpoint, add `--runtime http --config llm.json` (the package README describes the file; the API key comes from the environment variable it names). When the host exposes `fork_turns` instead, use `none`; use the host's equivalent on other runtimes. Pass only the generic worker instructions from `assets/prompts/` and that packet's JSON.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dispatch_reader.py prefix --events run/events --packet run/checkpoint-01.json --label P01
```
 Avoid giving the worker the full skill or the editor's file paths. Run each checkpoint with a new worker or stateless request; do not reuse a context that has seen a later checkpoint. Queue workers within available capacity.

Freeze each worker's original observation and record its actual input, call/context mode, and any tool accesses separately. Validate the observation:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_packets.py check-observation --packet run/checkpoint-01.json --observation run/observation-01.json
```

Treat this check as hash/schema/quotation validation only. It cannot prove isolation, truth, or human difficulty. If independent workers are unavailable, perform the full-context review and label proposed first-reading issues as hypotheses. Do not stop the rest of the review.

Keep finite-memory claims separate. A complete prefix tests information availability, not human working memory. Diagnose dispersed information by identifying the pieces a reader must combine and the cost of retrieving them. Do not impose an invented memory-capacity threshold.

## 4. Diagnose with the full document

Reconcile frozen observations with the editor map in the reconcile stage, after the checks stage is recorded; classify each observation and attach the run or branch it came from. Distinguish missing information, information supplied later, information supplied earlier but costly to retrieve, ambiguous reference, and absent argument relations. Record later explanations in the editor diagnosis without rewriting the initial reader question.

Audit each paragraph's role: the question it answers, its main proposition, its evidence or warrant, its link to the preceding unit, and what it establishes for the next unit. Check a proposal's path from problem and gap to objective, approach, success criterion, feasibility, and contribution. Do not confuse shared vocabulary with a causal or evidential relation.

For each candidate, first record what might make the existing text adequate: an earlier explanation, expected background, a warranted inference, or an honest preview. Inspect the relevant source passages, then save an accept/reject/merge/author-question decision with a short evidence-based reason. For each accepted issue, provide an exact anchor, available context, reader question, diagnosis, predicted consequence, severity, confidence, concrete action, and scientific-fidelity check. Deduplicate symptoms of the same dependency problem. Separate optional stylistic preferences from substantive findings. Report no issue when the text already works; do not fill a quota.

## 5. Review words, terms, and sentences

Prefer familiar words, specific actions, and natural-language explanations. Replace nonessential specialized labels and obscure vocabulary with an accurate ordinary description. Do not create new terms, named principles, abbreviations, capitalized labels, or coined compound expressions during editing unless the user explicitly requests naming. Apply the same rule to review comments and explanations.

For each unfamiliar or author-created term, check whether its name is necessary to preserve a precise distinction, connect to established literature, or refer to a genuinely defined technical object. Repetition alone does not justify a new name. When a short ordinary description suffices, recommend it. Retain an indispensable established technical term with a brief explanation where needed; do not replace a precise term with a vague or scientifically different one. Treat uncertain terminology status as uncertain rather than asserting that it is standard or invented.

Audit noun stacks, unnecessary nominalizations, delayed subjects or verbs, nested clauses, unclear actors, empty evaluative language, formulaic transitions, and needless synonym changes. Record the expression's actual meaning and whether its name is necessary in the checks stage, including why a questioned term should remain when appropriate. Draft sentence-level replacements in the repairs stage after the candidate has been assessed. Preserve legitimate qualifications and logical links when shortening or splitting. Avoid fixed word-count limits, blanket passive-voice bans, and vocabulary blacklists that ignore meaning.

When spaCy is installed (see the package README), `scripts/difficulty_features.py nominate` ranks blocks by eight parse-based counts linked to processing difficulty (references/wording-and-sentences.md names them and their source); use the top blocks as passages to inspect for `wording_check`, never as findings. Before recording a proposed wording, `difficulty_features.py compare` reports the same counts for the original and the proposal side by side and flags any feature the proposal has more of; the flag is advisory and does not replace the fidelity audit.

Before accepting a rewrite, compare its terms and abbreviations with the source. Remove newly introduced labels that merely rename an ordinary relation or process. Confirm that explaining one concept has not introduced several unexplained concepts. Keep a useful existing term consistent instead of rotating synonyms for stylistic variety.

## 6. Suggest and verify changes

Prefer the smallest effective operation: move a brief explanation, add a required condition, supply a missing warrant, clarify a referent, add a retrieval reminder, split competing paragraph functions, or reorder dependent passages. State what to move, from where, to where, and why. Supply exact proposed wording when the manuscript provides enough scientific information.

Preserve claim strength, quantifiers, assumptions, population, measured outcome, uncertainty, time horizon, numerical values, and causal versus associative wording. Have an independent, tool-less auditor compare each proposed wording with the original on exactly these properties (`assets/prompts/fidelity-audit.txt`; `dispatch_reader.py audit` runs it and fills in the auditor); the repairs stage records wording only when that audit finds every property preserved. Do not invent experiments, results, mechanisms, citations, or author intentions. When a repair requires a scientific decision, identify the question for the author and offer conditional alternatives instead of silently filling the gap.

When asked to restructure, give a paragraph-level sequence with: reader question; concepts required on entry; concepts introduced; claim and evidence; figure/table placement and purpose; and the next question enabled. Account for the word/page budget. Place figures according to the understanding they enable, without assuming every reader inspects them at the same time.

When asked to revise, re-index the edited text, check affected claims/definitions/captions, and rerun relevant checkpoints with fresh readers under the same profile. Do not supply the previous review or repair explanation to those readers. Describe this as model-based rechecking; reserve claims about improved human comprehension for actual reader evidence.

## 7. Deliver the review

Run the stage readiness check before generating the final report. Use `assets/review-report.md` for the output shape and compile findings from the saved decisions and repairs. Do not add a new diagnosis during report drafting without returning to the relevant checks in a corrected run. Lead with the few most consequential findings and their proposed repairs. Include:

- Source version, reader assumptions, actual execution mode, and coverage.
- A prioritized issue table and detailed source-anchored recommendations.
- A concept introduction map for central concepts; a paragraph/figure sequence when requested.
- A wording and sentence table with exact originals, proposed replacements, reasons, and scientific-meaning checks; identify unnecessary terms that can become ordinary descriptions.
- Proposed wording or moves with scientific-fidelity notes and unresolved author decisions.
- Accessible supporting notes showing passage coverage, checks, candidate decisions, and meaning checks; distinguish kept text from text not inspected.
- What was verified and what remains uncertain.

Use the user's requested language; preserve original-language quotations and provide English replacement prose when reviewing an English manuscript for a Chinese-speaking author. Write the review itself in common language, without inventing diagnostic labels for ordinary writing problems. Keep hashes, internal mode codes, and schema details in supporting logs; explain execution and limitations in ordinary words in the user-facing report. Cite the manuscript for manuscript-specific claims. Read [references/evidence-and-tools.md](references/evidence-and-tools.md) only when explaining the research basis or considering additional tools. Use external tools for a specific justified need, and do not equate a literature-inspired workflow with installed research software.

Validate structured final findings and their correspondence to the completed stage records. Preserve candidate IDs in the final findings; see staged-review.md for `check-final`. The existing source validator remains available as a source/quotation check:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/review_packets.py check-review --index run/index.json --review run/review.json
```

Use the environment's document-authoring skill if a Word/PDF report or tracked changes are requested. Keep complete review artifacts in the user's requested destination according to the host's persistence rules.

Read [references/plugin-extension.md](references/plugin-extension.md) only when the user asks to turn this skill into a plugin or service. Do not claim those service components are already implemented.
