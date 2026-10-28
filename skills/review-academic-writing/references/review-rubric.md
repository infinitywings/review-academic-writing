# Operational review rubric

## Contents

- Review contract and information boundaries
- Concepts, dispersion, and paragraph function
- Definitions, evidence, and scientific meaning
- False positives, editing priority, and acceptance
- Wording and sentence construction

## Review contract

- Fix the intended reader, permitted background knowledge, document purpose, reading path, and length constraints.
- Establish the reader's communication need from the requested genre and task, independently of the purpose the manuscript happens to state. Record the manuscript's stated purpose separately. For a research or laboratory report, a clear account of what the authors did or learned does not by itself explain what the investigation tests, demonstrates, or contributes. If the assignment explicitly asks for a personal learning reflection, respect that purpose.
- If unspecified, provisionally assume a field-aware researcher unfamiliar with this project; for proposals, consider a panelist from a neighboring subfield and state the assumption.
- Include an abstract, figure, caption, or appendix in the reader's knowledge only if the declared reading path has already encountered it.
- Distinguish scientific validity, textual support, fluency, cohesion, incremental understanding, and retrieval cost; do not collapse them into one clarity score.
- Diagnose predicted reader difficulty; claim observed human confusion only when human observations were supplied.

## Information boundaries

- Declare the actual mode: full-context structural audit, simulated sequential audit, or isolated prefix audit.
- An isolated reader needs fresh history plus only the reader profile, the permitted prefix, and state derived sequentially from that prefix.
- A child inheriting the full manuscript is not isolated; neither is a model that has already read the document and is told to forget it.
- Do not pass a full-document concept ledger to the prefix reader: even early entries can incorporate later meanings or conditions.
- Check first critical uses at sentence-level boundaries when necessary; receiving a whole paragraph can expose a definition after the problematic sentence.
- Freeze the initial reader question and its evidence before a full-document editor diagnoses or repairs it.
- Record the editor's independent checks before any reader observation enters the journal; reconcile observations afterwards in their own recorded stage, and ask targeted questions through branches that never feed the main reading.
- Prefix restriction tests information availability; it does not establish a model of finite human working memory.

## 1. Concepts before first critical use

- Record first mention (M), earliest usable explanation (E), and first critical use (U), with source locations.
- A critical use requires the reader to apply the concept to interpret a central claim, comparison, condition, or inference.
- E after U is a candidate issue only if the permitted background and reasonable local inference do not already suffice.
- Ask what meaning, scope, or condition the reader must know at U, and identify precisely which part is missing.
- Accept a sufficient intuitive explanation before a later formal definition; do not require all implementation details at first mention.
- Prefer advancing a short explanation or condition, or postponing the dependent statement, over moving an entire methods section.

## 2. Dispersion, retrieval, and terminology

- Identify the exact pieces that must be combined, their locations, intervening topic changes, and inconsistent aliases or scopes.
- Distinguish information never supplied, information supplied too late, and information supplied earlier but costly to retrieve.
- Repetition and distance alone are not defects; explain the concrete retrieval or integration burden.
- Check whether a short reminder, stable name, explicit cross-reference, or grouping of jointly needed conditions is sufficient.
- Treat lexical overlap, semantic similarity, and distance measures as candidate-finding heuristics, not proof of coherence or human difficulty.

## 3. Paragraph and section function

- State each unit's controlling question or proposition, its role, the evidence or inference it contributes, and its connection to adjacent units.
- Flag topic continuity without an argument relation: two paragraphs can discuss the same entity without explaining why the second advances the first.
- A topic sentence is useful when it reveals the unit's role; its absence alone is not an error.
- Check whether a reader must finish a paragraph to discover why its earlier details matter or how many points it develops. Consider an earlier controlling statement or a brief forecast when it reduces this reconstruction; do not require every paragraph to follow one template.
- Split competing paragraph functions or reorder dependent units only when the proposed change improves the reader's reasoning path.

### Manuscripts

- Introduction: can the reader connect the problem, prior knowledge, specific unresolved issue, research question, and claimed contribution?
- Examine procedural aims, personal motivation, and broader significance separately. A tentative motivation can be scientifically honest while still leaving the target audience's reason to care unexplained. Conversely, missing results in an introduction are not evidence that its motivation is false. If the supplied manuscript cannot support a significance statement, ask the author for the missing scientific rationale instead of inventing it.
- Background and methods: are needed terms, objects, assumptions, comparisons, and measurement meanings available before critical use?
- Results: are the relevant metric, comparison, population, uncertainty, and evidential scope clear before the interpretation depends on them?
- Discussion: distinguish observed results, interpretation, implications, and limitations; check that conclusions do not silently broaden the evidence.
- Figures and tables: check first callout, reading order, captions, symbols, comparison basis, and agreement with the prose.

### Proposals

- Establish why the problem matters and what capability or knowledge is missing before relying on the proposed solution's significance.
- Distinguish research questions or hypotheses, aims, tasks, methods, anticipated outputs, and evaluation criteria.
- At the section level, connect each major task to the gap it addresses, required inputs, expected output, success criterion, and relevant alternative or risk.
- Check dependencies among aims or thrusts: readers should know what each produces and what later work needs from it.
- Preserve the difference between preliminary evidence, established capability, planned work, and an expected outcome.
- These are functional checks across the relevant section, not a demand that every paragraph contain every component.

## 4. Definition versus evidence

- A definition problem concerns what a term, construct, quantity, or condition means.
- An argument problem concerns why one statement follows from another; identify the missing relation or premise.
- An evidence problem concerns whether observations, citations, or reasoning support a claim at its stated strength.
- Classify them separately: adding a definition cannot establish a causal claim, and a transition cannot supply missing data.
- If repair requires new scientific information, mark an author decision or verification need; do not invent a fact, citation, assumption, or result.

## 4b. Claim strength, rules, and citations

Record these in the checks stage as each block's `claim_check`; they are the checks most often skipped when a review concentrates on first-reading difficulty.

- Absolute and comparative claims: for every "no existing method", "the first", "every", "widely used", "outperforms", or "always", name the evidence the manuscript gives for that strength (a measurement, a cited survey, a stated scope) and whether the strength matches it. A claim that holds under a scope the text states elsewhere is a candidate for restating the scope at the claim, not for weakening the claim.
- One counterexample per rule: for each formula, metric, threshold, or decision rule, try one concrete case that the rule should handle (a boundary value, an empty set, a tie, a sign flip, a unit change) and record what the rule gives. A rule that fails its own stated purpose on the tried case is a candidate; a rule that survives is recorded as checked.
- Citations that carry weight: a citation that supports a comparison ("unlike [12], we ...") or a novelty claim ("no prior work ...") is a candidate for the citation spot-check in workflow.md; a citation that only points to background is not. Record which it is.

## 5. Scientific meaning invariants

For every proposed rewrite or move, compare the original and revised text for:

- Claim ownership and status: prior work versus this work; observation versus interpretation; result versus plan or hypothesis.
- Scope: population, setting, variables, domain, boundary conditions, time horizon, and applicability.
- Strength: causal versus associative, necessary versus sufficient, possible versus established, existential versus universal.
- Conditions: assumptions, qualifiers, uncertainty, exceptions, fixed versus changing quantities, and guarantees.
- Evidence: sample or measurement meaning, reported values and directions, baselines, limitations, and citation support.
- Cross-document consistency: abstract, contribution list, figures, methods, results, discussion, and dependent definitions.

Preserve these properties unless the author explicitly changes the underlying scientific position; flag ambiguous intent instead of silently strengthening or weakening it.

## 6. False-positive safeguards

- Allow harmless previews and contribution summaries when the missing mechanism is not yet needed.
- Allow reasonable inference from stated context and the agreed reader background; do not require every relation to be explicit.
- Allow layered definitions and purposeful reminders; do not merge all mentions of one concept merely because they recur.
- Respect genre and author intent; inverted-pyramid and topic-sentence-first arrangements are options, not universal rules.
- Do not call a previously supplied concept undefined, or mistake unfamiliarity to the model for unfamiliarity to the intended reader.
- Separate severity from confidence, and label optional stylistic preferences as optional.

## 7. Findings and editing priority

Each substantive finding must include:

1. Issue type, section/paragraph/sentence anchor, and a short exact quotation.
2. Reader profile, checkpoint, available premises, and the initial unanswered question or retrieval requirement.
3. Predicted reading consequence, severity, and diagnostic confidence.
4. A separate full-document diagnosis if later material is inspected.
5. A concrete minimal edit or move, the reader benefit, scientific-fidelity check, and affected locations.

- Critical: blocks interpretation or evaluation of a central contribution, method, hypothesis, or inference.
- Major: requires substantial backtracking, reconstruction, or an unsupported conceptual leap.
- Minor: creates local ambiguity or avoidable retrieval burden without blocking the main argument.
- Optional: a preference with uncertain comprehension benefit.
- Consolidate repeated symptoms caused by the same missing prerequisite; prioritize consequences, not issue counts.
- Preserve scientific meaning first; then repair central prerequisites and argument dependencies, paragraph organization, local retrieval cues, and finally wording.
- Prefer a small explanation, condition, bridge, reminder, or text move; justify larger restructuring with the dependency it repairs.
- Reassess edited passages using the same reader contract and a fresh reviewer where supported; do not pass the prior repair rationale as reader knowledge.

## Acceptance boundary

Require honest execution-mode labeling, traceable quotations, concrete repairs, false-positive checks, and scientific-fidelity checks before calling a review complete.
Synthetic regression success verifies expected behavior on those examples; human-comprehension benefits require separate reader studies and must not be claimed from the skill's design alone.

## Wording and sentence construction

- Apply wording-and-sentences.md in every wording review and rewrite. Prefer common words, concrete actions, and ordinary descriptions of nonessential concepts.
- Examine unfamiliar terms, unnecessary author-created names, new abbreviations, nominalizations, noun strings, and nested clauses. Give exact replacements that preserve scientific meaning.
- Do not coin terms while repairing prose. Keep only necessary technical distinctions, explain them at the point of need, and keep their names consistent.
- Check the reviewer's own output for newly invented labels, obscure vocabulary, and unnecessarily elaborate sentences.
