# Words, terms, and sentence construction

Apply these rules to manuscript review, proposed revisions, structure plans, and the reviewer's own explanations. Treat plain, precise prose as the default. Do not make text sound more academic by making it more abstract.

## Prefer ordinary language

- Use the most familiar word that preserves the intended meaning. Prefer concrete verbs over a noun plus a weak verb: for example, change “perform an evaluation of” to “evaluate” when the actor and action remain the same.
- Replace unnecessary formal vocabulary with common alternatives. Judge words in context; do not run mechanical substitutions that change technical meaning or claim strength.
- Describe what happens, what the system or researcher does, and under what conditions. Do not replace a clear action with a newly named abstract process.
- Remove decorative modifiers and unsupported evaluations. Retain an evaluative statement only when its meaning is specific and supported. Do not translate statistical significance into practical importance or erase a reported uncertainty.
- Keep precise, relevant limitations. Plain prose can still say “was associated with,” “under this assumption,” or “we did not measure this mechanism.” Do not strengthen a finding merely to make a sentence sound more decisive.

## Decide whether a term needs a name

For each suspicious phrase, identify whether it is an established technical term, an author-defined formal object, an ordinary description, or of uncertain status. Use the supplied manuscript and reader profile first. Verify against a primary source only when its status matters and cannot be established from the available context. Do not infer that a term is invented merely because it is unfamiliar to the model.

Apply this order of preference:

1. State a simple relationship or operation in ordinary language.
2. Use an existing, recognizable technical term when it preserves a necessary distinction; explain it briefly if the target reader needs that explanation.
3. Retain an author's existing name for a genuinely necessary defined object only when replacing it would obscure a repeated technical distinction or make the argument materially harder to follow. Explain that reason explicitly.
4. If a proposed new name appears necessary, describe the object plainly and identify the naming decision for the author. Do not invent the label during routine editing. Create names only when the user specifically requests that task.

Ask whether the reader needs to remember the label, or merely understand the sentence. A one-time distinction, a simple processing step, or a sentence's causal relation usually does not need its own name. Frequency alone is insufficient: repeating an ordinary phrase does not make a new acronym useful.

Flag unnecessary introductions such as “we call this,” “we define the notion of,” “hereafter termed,” new capitalized labels, unexplained abbreviations, and coined hyphenated compounds. Treat these as candidates for inspection, not automatic errors; preserve actual definitions and established compounds when required for precision.

When recommending removal of an existing term, give the natural-language replacement and list dependent locations that need matching edits. Preserve links to formal notation, cited methods, or measurements when those remain necessary. Do not merge scientifically different objects simply because their names look similar.

## Make sentence relations explicit

- Put an identifiable subject and the main action where the reader can find them. Use active voice when the actor is known and matters; retain passive voice when it appropriately foregrounds an object or measurement. Do not invent an actor to avoid passive voice.
- Unpack dense noun strings into verbs and short clauses that state the actual relationship. Use the manuscript's facts; ask the author when the relationship is unspecified.
- Separate multiple major claims when their conditions and evidence differ. Carry each qualifier, negation, quantifier, citation, and uncertainty statement to the sentence it governs.
- Split nesting that requires readers to hold several incomplete clauses at once. Do not impose a universal sentence-length limit or produce a string of disconnected short sentences.
- Use transitions only when the underlying relation is established. “Therefore” cannot repair a missing premise; “moreover” cannot explain why two topics belong together.
- Keep a concept's established name consistent. Do not substitute synonyms merely to avoid repetition if readers could interpret them as different objects.
- Remove empty introductions and repeated announcements when they add no context. Preserve a useful topic sentence, reminder, contrast, or qualification even if it repeats earlier information.

**Parse-based nomination.** Clark, Martin and Mahowald (2026, arXiv:2610.29162, Section 3.1) measure eight features from dependency parses that are linked to human processing difficulty, most of which current models over-use relative to human text and round-trip paraphrases: object relative clauses with a relativizer, object relative clauses without one, denominal verbs, inanimate subjects of animate-only verbs, dependency length standardized by sentence length, stacked nouns (three or more in one modifier chain), bigrams unattested in a reference corpus, and n-gram perplexity. `scripts/difficulty_features.py` approximates these counts (spaCy required; the denominal-verb and animacy measures use word lists and are marked heuristic) and ranks blocks by a within-document z-score sum. Treat the ranking as a list of passages to inspect for `wording_check`, not as findings: a high count can be a necessary technical construction, and a low count does not show that a passage is clear. The same counts can be run on a proposed wording with `compare`; a flag there is advisory and does not replace the fidelity audit.

## Check the revision, including its new vocabulary

Compare every proposed replacement with the source:

| Check | Required action |
| --- | --- |
| New label or acronym | Remove it unless the user requested naming; prefer a direct description. |
| New compound noun | Check whether it introduces a technical-looking concept absent from the source; unpack it if unnecessary. |
| Technical term retained | State the exact distinction lost by replacing it when the term was questioned. |
| Simplified technical term | Check that the replacement keeps the same mathematical, empirical, or operational meaning. |
| Shorter sentence | Verify that conditions, actors, negations, uncertainty, and causal/evidential relations are preserved. |
| New confidence or mechanism | Remove unsupported strengthening or added explanation; identify the author decision instead. |
| New synonym | Use the existing term when a different word could imply a different concept. |

Do not explain one unfamiliar expression by creating several more. Apply this check to the review's section names and diagnoses as well as to replacement manuscript text. Use descriptions such as “the definition comes after its first necessary use” instead of inventing a name for that writing problem.

## Output exact improvements

For an accepted wording issue, provide:

1. Exact original sentence or phrase and its location.
2. The specific obstacle: unnecessary name, obscure word, hidden action, unclear relation, overloaded clause, or another concrete problem.
3. A complete replacement that works in its surrounding paragraph.
4. A short explanation of what becomes easier to understand.
5. A check that the scientific meaning, technical distinctions, and qualifications are preserved.

Use final structured categories `unnecessary_term`, `word_choice`, or `sentence_structure`. Use a conceptual or scientific category instead when that is the primary defect, and include wording as part of the repair. Deduplicate the same issue across passes. Treat ordinary preferences as minor or optional unless a specific reading obstacle justifies higher severity.

## Small examples

Use these as examples of an editing operation, not reusable manuscript claims:

| Original | Prefer | Why |
| --- | --- | --- |
| “The system performs an evaluation of whether the two records agree.” | “The system checks whether the two records agree.” | State the same action directly. |
| “These observations facilitate the identification of discrepancies between the two records.” | “These observations help identify differences between the two records.” | Use familiar words without changing “help” into a guarantee. |
| “We call the time between consecutive measurements the Measurement Separation Window (MSW). We set the MSW to two seconds.” | “We set the time between consecutive measurements to two seconds.” | If the passage only needs that ordinary interval and the label has no formal role, remove the unnecessary name. Retain a formal quantity or notation if later analysis actually depends on it. |

Do not treat a “confidence interval” as merely a “range of possible values,” or replace “fixed effects” with “factors,” without checking what scientific meaning would be lost. A precise existing term can be easier to understand than an inaccurate simplification. Add a short ordinary explanation where needed rather than replacing precision with vagueness.
