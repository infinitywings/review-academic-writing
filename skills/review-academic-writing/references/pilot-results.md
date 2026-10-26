# Development pilot: 20 October 2026

This is a record of limitations and design decisions, not a certification of human-like reading or review accuracy.

## Materials and execution

Six selected teaching texts were read with both progressive masking and a whole-text neutral-reading comparison: four short versions from Gopen and Swan's 1990 *The Science of Scientific Writing*, a Northwestern NuWrite introduction marked weak, and a 12-page Northwestern report marked good. Both Northwestern PDFs explicitly say Posted: 2009 and contain instructor comments. The good/weak documents are different reports, not a content-matched revision pair. The Gopen revisions also change some content.

The fixed v0.1 protocol used fresh subagent contexts per display and at most 180 English whitespace words of reader-generated notes. The six masked runs completed 26 distinct body windows in 36 display events. The long report contributed 16 windows and 26 events, including five figure lookups and five returns. Six fresh whole-text readers used the same neutral task. One separate editor reviewed the weak introduction through all six saved stages. One later diagnostic restored an already-seen passage without adding future text or instructor comments.

Input/output bytes, source hashes, actual dispatch identifiers, note counts and record chains were saved and checked. Full worker tool traces were unavailable. The supplied input boundary and fresh dispatches were recorded; operating-system isolation and every image-tool call were not independently audited.

## What the observations support

- Progressive reading produced inspectable understanding, questions, qualifications and chosen lookups. It did not reliably elicit the writing criticisms in the teaching keys, and no superiority over whole-text review was established.
- Each of the four Gopen cases had recognition reported in at least one masked event; all four whole-text readers also recognized the source. These are development examples, not unfamiliar held-out texts.
- The weak introduction was readily paraphrased. The staged editor found useful local wording issues but did not explicitly diagnose several instructor priorities concerning scientific purpose, broader significance and early paragraph guidance. Neutral comprehension success must not substitute for a genre-aware editorial audit.
- In the long report, a count of four algorithms entered the notes and later disappeared. On reaching the description of three algorithms, the reader did not flag the count discrepancy. A single post-hoc call with the earlier passage restored did flag it. This is evidence about the available information in these calls, not a causal estimate or a human-memory result.
- The masked reader requested Figures 1, 2, 3, 9 and 10, but not Table 1. The whole-text reader, reporting inspection of all ten assets, identified a prose/table percentage discrepancy that the masked reader did not. Asset coverage differed between conditions.

## Changes after the fixed runs

The helper installed at the time of this pilot used run-format version 2; the current controller writes version 5: version 3 added exact-quotation checks, version 4 added structured notes with provenance and branch records, and version 5 added the asset policy label and the background basis. Version-2 runs still need the version-2 controller. It explicitly marks the end of supplied text and allows assets to become available at their verified layout position through `encountered_by`, in addition to explicit references. Version 1 had no end marker, and one misnumbered figure lacked an eligible reference. The original runs and controller were preserved; no original results were replaced with version-2 results. Ten helper checks passed, including the two new boundary behaviors and rejection of earlier manifests by the new controller. These checks do not establish review quality. Always use the matching controller: the preserved old controller did not check format versions, so protection is not bidirectional.

The rubric was also clarified to distinguish a manuscript's stated purpose from the target reader's communication need, to examine significance separately from the truth of a tentative motivation, and to check whether a paragraph reveals its organizing point only after its details. These are post-pilot additions. Their effect has not been evaluated on new held-out material.

## Sources

- Gopen, G. D., & Swan, J. A. (1990). The Science of Scientific Writing. *American Scientist*, 78(6), 550–558. Permitted reprint: https://courses.ems.psu.edu/styleforstudents/print/c10_p6.html
- Northwestern NuWrite, laboratory-report teaching samples and annotated PDFs: https://nuwrite.northwestern.edu/communities/science-writing-community/science-writing-samples-advice/writing-lab-reports/

Use `masked-reading.md` for execution. Do not cite this pilot as measured improvement in human comprehension, calibrated cognitive capacity, comprehensive expert agreement, or a comparison with a resource-matched conventional-review baseline.
