# Read progressively with limited visible text and finite notes

## Purpose and evidence limits

Use this optional experimental procedure when the user asks for masking, a sliding window, bounded memory, or subagents that form an interpretation as they read. It creates conditions under which understanding can change; it does not ask an informed editor to pretend to forget a manuscript already in its context. Its observations are model behavior under recorded input conditions, not measured human memory, attention, reading time, or comprehension.

For the scope and limits of the initial teaching-text pilot, see [pilot-results.md](pilot-results.md). Preserve that distinction when describing what has actually been tested.

Keep three roles separate. A controller selects and supplies material. A fresh reader understands the material actually supplied. A full-document editor later checks the reader's observations and makes recommendations through the existing six stages. The editor may know the whole document; that knowledge must not enter reader packets, memories, or leading questions.

An accurate final paraphrase can conceal difficult wording or an unstated relation that the model supplied. Neutral reading therefore supplements the explicit concept-order, paragraph, wording, sentence, and scientific-fidelity checks. Do not count questions or lookups as writing defects. Do not treat fewer questions as proof that a revision is better.

## 1. Fix the reading conditions before dispatch

Save the purpose, source edition, included material, reader background, reading route, window rule, note budget, navigation rules, and evaluation plan. State whether the source is a complete document, an introduction, or an excerpt. Background must be independent of unseen project-specific content. No expert answers, expected problems, or full-document glossary belong in the reader profile.

For paginated English scientific text, start with approximately half to one original page per main-body display. End at paragraphs or subsections when practical; do not expose a four-page section just to preserve a section boundary. Preserve whole sentences, equations, citations, and source locations. Record shorter windows and any overflow. A half-page window is an engineering starting point, not a human perceptual span. Use exact sentence or clause checkpoints, separately identified, when a full window would already contain the later explanation being tested.

Use `assets/masked-reader-profile.json` as a starting profile. The helper counts the gist and every note's text against `memory_limit`, in whitespace-separated words by default or in non-whitespace characters when `memory_unit` is `characters`; 180 words is a configurable default, not a biological capacity estimate. For unsegmented Chinese set `memory_unit` to `characters` and choose the limit as an experiment parameter; do not call either bound a human capacity. Keep user-facing review language separate from the language chosen for counted working notes.

Record the current reading path, including any abstract, figure overview, or appendix already encountered. A figure seen ahead of its main-text location becomes part of the seen material. Do not describe access as a simple one-way page prefix when navigation has made it noncontiguous.

## 2. Prepare the case outside the skill

Create a UTF-8 JSON case with:

```json
{
  "id": "anonymous-case-id",
  "blocks": [
    {"id": "B001", "text": "Verified source paragraph.", "page": 1, "kind": "paragraph"},
    {"id": "B002", "text": "Next verified paragraph.", "page": 2, "kind": "paragraph"}
  ],
  "windows": [["B001"], ["B002"]],
  "asset_policy": "reference-jump",
  "assets": [
    {"id": "Figure1", "path": "/absolute/path/figure.png", "caption": "Verified caption.", "referenced_by": ["B001"], "encountered_by": ["B002"]}
  ]
}
```

Windows must partition the blocks once in source order. For assets, `referenced_by` identifies explicit references already encountered. Optional `encountered_by` identifies a block at whose reading position the figure or table is physically visible, even if the author omitted or misnumbered its reference. Under `asset_policy: reference-jump` either grants access once the corresponding block has been seen; under `strict-prefix` only the layout position does, so a figure referenced early but placed late stays closed until the reader reaches its page. Every run carries exactly one policy, and the reconcile stage records it as the checkpoint's `figure_policy`; do not report runs under different policies as one condition. Map this position from the verified layout; do not use a future paragraph merely to expose an asset early. Keep missing figure numbers and incorrect captions intact for diagnosis. Do not quietly repair the source during extraction.

For a PDF extracted with `scripts/extract_pdf.py`, build the case from the index and the page sidecar. Windows hold about `--budget` words, never cross a heading, and contain every block once. Assets come from a list you supply (`[{"id": "Figure1", "page": 4, "path": "/abs/fig1.png", "caption": "..."}]`); `referenced_by` is every block that mentions `Figure N`, `Fig. N`, or `Table N`, and `encountered_by` is the first block on the asset's page. The command reports mentioned figures or tables missing from your list; check each mapping against the PDF.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/extract_pdf.py case --index run/index.json --pages run/manuscript.md.pages.json --budget 250 --asset-policy strict-prefix --assets run/assets.json --out run/case.json
```

Remove teaching quality labels, comments, revision labels, and answer keys from reader inputs, filenames, and image margins. Keep them in evaluator-only files. Preserve formulas and distinguish extraction corrections from editorial changes. A path to an image is not visual inspection: an enabled image capability must actually show the image before a reader claims to understand its visual content.

## 3. Start and display one packet

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/masked_reader.py start --case run/case.json --profile run/profile.json --out run/reader
python3 ${CLAUDE_SKILL_DIR}/scripts/masked_reader.py packet --run run/reader --out run/packet-001.json
```

Inspect the packet boundary before dispatch. A normal packet contains only the current blocks, the previous reader's finite `carry_memory`, generic task instructions, and IDs eligible for targeted lookup. It excludes earlier raw text, earlier full responses, future text, expert comments, and the editor's map. The last main-body packet explicitly marks the end of the supplied text; an excerpt ending does not imply that the full paper lacks methods or evidence.

On Claude Code, `scripts/dispatch_reader.py masked` performs this whole loop: it issues each packet, sends `assets/prompts/masked.txt` plus the packet to a fresh tool-less `claude -p` call, records the response with the call's session id as the agent id, applies the correction protocol in section 4, stops after three lookups without new sources or at `--max-events`, and writes a summary. `dispatch_reader.py branch` does the same for one branch question.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dispatch_reader.py masked --events run/events --run run/reader --case run/case.json --profile run/profile.json --max-events 40
```

Start a new worker for **every display event**, including each lookup and the return to the main text. Where available use `fork_turns: none`; otherwise use an equivalent stateless request. Do not continue a conversation that retains earlier raw text and call that bounded memory. Pass the exact packet inline, or authorize reading that one packet file only. Permit only supplied image paths and writing the designated response file. Do not send the full skill, source index, case file, sibling packets, or expert answers to the worker.

The task is neutral: understand the question, claims, method, evidence, and limitations well enough to explain them to a colleague. Do not ask the reader to find a quota of defects, act confused, propose edits, or anticipate a known teacher comment. Ordinary background inference is allowed, but project-specific inference must be distinguished from what the author stated. Understanding an author claim does not establish its truth. Record recognition of a remembered exact example without supplying the recalled lesson.

## 4. Save the observation and continue

The packet includes the exact response schema. Save a brief current interpretation, anchored understood claims labeled explicit (the text states it), inferred (a short inference from the text), or background (it rests on the declared reader background rather than on the text), specific uncertainties with whether they are needed now, any resolved or revised understanding, and a choice to continue or request one target. An anchor's quotation, when given, must be exact text of a block the reader has seen; a quotation for a figure or table is not checked. The only content carried to the next fresh reader is `carry_memory`:

- `gist`: the current overall account;
- `notes`: one entry per retained item, each with a stable `id`, a `kind` (`established` for what the author text has established, `tentative` for the reader's provisional inferences, `open_question` for unresolved matters, `qualification` for limitations, conditions, negation, and uncertainty needed to avoid changing the science), the `text`, the `sources` it rests on (ids the reader has seen), and `since`, the display event at which it first appeared.

A carried note keeps its `id` and `since`; its text, kind, or sources may be revised. A carried note that is not kept must be listed in `dropped` with a reason. A new note cites only sources the reader has seen and carries the current sequence as `since`. The helper enforces these rules and records in each receipt which notes were added, revised, and dropped, so a fact that later disappears from the notes can be traced to the event where it was dropped and the sources it rested on. These are concise external work products, not a request for private reasoning. The complete observation remains in an external history for the final editor; it does not travel onward. A later explanation may resolve an earlier question, but must not overwrite the original question. The controller must not silently improve, correct, or complete the reader's carried beliefs using the full document.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/masked_reader.py record --run run/reader --packet run/packet-001.json --response run/response-001.json --agent-id ACTUAL_WORKER_ID
python3 ${CLAUDE_SKILL_DIR}/scripts/masked_reader.py packet --run run/reader --out run/packet-002.json
```

The next packet follows the recorded action. For lookup, the helper returns only a previously seen block or an asset with an encountered reference or layout position. The lookup reader uses `advance` to return to the original main-text window in another fresh context. Preserve the reason for lookup and what changed after it. This return display has a real call cost; include it when reporting resources.

Freeze exact packet and response bytes and actual dispatch identifiers. The helper checks order, source bindings, file hashes, permitted targets, and the note budget; it does not judge scientific truth or semantic adequacy. Preserve an invalid original response if a format or budget correction is necessary, keep the correction separate, and record why it was requested. Readers count their own words inaccurately: in the 2026-10-20 trial, a correction that only restated the limit still failed, and one that stated the counting method and a target below the limit passed. A budget correction therefore states the limit, how the helper counts (whitespace-separated words, or non-whitespace characters, of the gist plus every note's text), and a target of 85 percent of the limit. Request at most two corrections per packet, labeled b and c, then stop the run and report it at the last recorded event. Never rewrite a frozen response or its receipt to pass validation. Retain the original controller for earlier run formats.

If three consecutive lookup requests supply no new source material, stop that loop and report the execution limit. Do not invent a human cognitive threshold or silently replace the reader's chosen action. A corrected continuation is a new, labeled condition. If fresh contexts are unavailable, continue the full-document review and label the progressive-reading observations as unisolated hypotheses.

Fresh workers are not operating-system sandboxes. If the host cannot expose complete worker tool traces, report verified supplied inputs and fresh dispatches separately from unverified subsequent access. Source hashes do not prove that a worker avoided other files or pretrained memories. If actual access to future text or a teacher key is observed, invalidate that event and restart it without contamination when possible.

## 5. Ask one targeted question in a branch

A branch asks the reader one question from a recorded display event without changing the main reading. Save the question as JSON with `id`, `question`, `selected_with_full_text` (whether the editor chose it knowing the whole document), and `reason`. Then issue and record the branch:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/masked_reader.py branch --run run/reader --from-sequence 7 --question run/q1.json --out run/branch-001.json
python3 ${CLAUDE_SKILL_DIR}/scripts/masked_reader.py branch-record --run run/reader --packet run/branch-001.json --response run/branch-response-001.json --agent-id ACTUAL_WORKER_ID
```

The branch packet contains exactly the materials and `carry_memory` of that event plus the question; it never contains later text. Dispatch it to a fresh worker like any other packet. The answer gives `support` as `stated` (the visible text says it; quote it), `inferred` (say how the text supports it), `background` (say which declared background it relies on), or `undetermined` (say which condition or relation stays open), with exact anchors. The helper saves the answer under `branches/`, binds it to that event's receipt, and records how many main events existed when it was made, so a branch made after the run is visibly post hoc. The main path never reads branch records; the next main packet is identical with or without branches. Use branches for reconciliation and for restoring an already-seen passage to test note loss; do not count branch answers as part of the natural reading.

## 6. Reconcile observations in the reconcile stage

Use the original histories as evidence in the reconcile stage, after the editor's independent checks are recorded. They do not replace the editor's block-by-block coverage notes. Attach a frozen run through `run_path` and `record_sequence`, and a branch through `run_path` and `branch_sequence`, as staged-review.md describes. After the reader run is frozen, inspect the full source and classify each useful observation with one of these labels:

| Observation | Label | What the editor checks |
| --- | --- | --- |
| Information absent at a critical use | `absent_at_critical_use` | Is it needed there, already explained, ordinary background, or an honest preview? |
| A question resolved later | `resolved_later` | Did waiting merely follow normal article structure, or did the missing relation block the earlier claim? |
| Earlier information needed again | `needed_again` | Was a reminder warranted, or was a normal targeted lookup adequate? |
| Information lost in notes | `lost_in_notes` | Did the source omit it, or did the reader's summary omit or distort it? |
| A changed interpretation | `interpretation_changed` | Which supplied passage caused the change, and was the earlier interpretation reasonable? |
| An inferred connection | `inferred_connection` | Is the inference justified and appropriate for the specified audience, or must the author supply a scientific premise? |
| A figure/table lookup | `asset_lookup` | What did it resolve, and did text, visual evidence, and caption agree? |
| The text worked at that point | `adequate` | Record why the existing text sufficed; this supports retaining it. |

Make acceptance, rejection, merge, and author-question decisions with source and counterevidence as usual. Give concrete repairs only afterward. Preserve the ordinary-language default, necessary scientific terms, uncertainty, numerical values, and causal versus associative meaning. Missing scientific links require an author question or a justified conditional edit, not invented science.

For important discrepancies, a branch that restores an already-read passage and asks one question can help distinguish source difficulty from note loss; the receipts' `notes_dropped` lists and each note's `since` and `sources` show when and from where the information left the notes. Changing the memory budget, adding a targeted comprehension question, or supplying an otherwise inaccessible figure is a new condition. Label such follow-ups post hoc; do not count them as independent successes of the original run.

## 7. Evaluate and report without overstating the result

Use old teaching examples with concrete human comments as useful development cases, keeping the comments hidden from the readers. Check dates for the actual examples, not just site footers. Public pre-2020 material can still be familiar from training. A self-report of no recognition does not prove that it was unseen.

Compare individual teacher judgments, not global good/bad labels. Include positive examples; a good example may still contain real errors. Distinguish a target the neutral reading task did not assess from an attempted diagnosis that was wrong. Keep model-found, source-supported concerns separate from teacher-listed issues. Where an expert revision adds scientific premises or removes detail, do not attribute its effects solely to style.

Keep the teaching key out of every candidate review until that review is frozen. If a pilot reveals a missing rubric check, preserve its original outcome, record the general rule added afterward, and test that rule on new material before claiming improved coverage. Do not treat adding the rule as repairing the recorded model's miss.

Report source scope, window sizes, memory rule, actual context boundaries, complete and missing coverage, lookups, recognitions, semantic errors, accepted diagnoses, and resource cost. A whole-text neutral reading is an information-access comparison, not automatically a conventional-review baseline. A later writing audit has a different task and cannot establish that masking caused its better coverage. Small single-run pilots and model self-reports do not establish improved human comprehension. Human-reader calibration and resource-matched comparisons remain necessary for that claim.
