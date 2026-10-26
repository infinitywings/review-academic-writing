# Evidence and optional research tools

Research snapshot: **2026-10-19**. Later publication, release, repository, and availability statuses require rechecking against the primary sources.

This is a research-informed design, not a validated measure of human manuscript comprehension. The sources below motivate checks or identify optional tools; listing them does not install, integrate, execute, or validate them. Report actual tool use separately.

## Conceptual foundations and design inspiration

| Primary source | What to borrow and what remains unvalidated |
|---|---|
| [ReviewCritique — EMNLP 2024](https://aclanthology.org/2024.emnlp-main.292/) | Borrow its attention to review deficiencies and human–model disagreement; writing disagreement in 3 of 20 directly reviewed papers does not establish a universal failure rate or the mechanism behind a particular clarity error. |
| [BooookScore — ICLR 2024](https://arxiv.org/html/2310.00785v4) | Borrow source-localized confusion questions and error categories; its full-summary prompt permits resolution from any part of the summary, so its evaluation does not validate this skill's first-critical-use or prefix-only criterion. |
| [KnowSim — 2026 preprint](https://arxiv.org/abs/2608.17150) | Borrow explicit knowledge units and prerequisites as a representation of what a reader may need; transfer from conversational knowledge-state modeling to long-manuscript review remains unvalidated. |
| [SimpleToM — ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/hash/524ff06d5375d76e93d8490e654a81bf-Abstract-Conference.html) | Borrow the distinction between identifying a knowledge boundary and applying it in subsequent reasoning; its tasks do not establish that simulated sequential reading reproduces human manuscript comprehension. |
| [Language models that match reader experience are better predictors of reading times — Journal of Memory and Language, 2026](https://sfb1102.uni-saarland.de/publication/language-models-that-match-reader-experience-are-better-predictors-of-reading-times/) | Borrow the need to specify reader experience when interpreting model-based difficulty; its short-text reading-time results do not validate a general-purpose ChatGPT or Claude manuscript reviewer. |
| [Lost in the Middle — TACL 2024](https://aclanthology.org/2024.tacl-1.9/) | Borrow the distinction between context availability and reliable context use; long-context retrieval results neither imply unlimited model memory nor provide a human working-memory simulator. |
| [Align to Structure — AAAI 2026](https://ojs.aaai.org/index.php/AAAI/article/view/41085) | Borrow explicit rhetorical-structure planning as a generation design; reported task improvements do not establish better first-read comprehension of scientific papers or proposals. |
| [CogWriter — Findings of ACL 2025](https://aclanthology.org/2025.findings-acl.511/) | Borrow the separation of planning, generation, and monitoring; results on the paper's writing tasks do not validate this skill's scientific-fidelity checks or predicted reading-cost diagnoses. |

These sources provide conceptual inspiration. This skill's default review process does not execute their authors' systems, reproduce their evaluation results, or claim their published performance.

## Optional tools for direct use after separate setup

| Primary project source | Appropriate direct use and limit |
|---|---|
| [TAACO — authors' repository](https://github.com/LCR-ADS-Lab/TAACO) | If separately installed and tested, use its cohesion indices to nominate passages for inspection; lexical and semantic connections do not establish prerequisite order, valid inference, or human comprehension. |
| [Comprehensive Coherence Calculator (CCC) — authors' repository](https://github.com/WeizheXu/coherencepip) | If separately installed and tested, use its semantic-relatedness measures as supplementary signals; such scores do not determine whether a necessary definition arrives before critical use or whether evidence supports a claim. |

Before any optional execution, verify current installation instructions, model/data dependencies, license, processing location, and the exact operation requested; avoid adding a dependency unless it resolves a concrete review need.

## Interpretation rules

- Separate direct empirical findings, task-transfer hypotheses, and this skill's design choices in explanations to the user.
- Do not cite an adjacent benchmark as validation of the complete review workflow.
- Describe generated findings as model-based predictions unless actual reader observations were provided.
- Distinguish a runnable repository, a paper with project links, and an integration that has actually been executed successfully.
- Evaluating the full workflow requires human comprehension and retrieval-cost outcomes, false-positive analysis, and checks that edits preserve scientific meaning.
