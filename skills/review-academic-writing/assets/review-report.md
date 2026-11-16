# Layout of the writing review report

`scripts/notes_build.py report --journal run/journal --index run/index.json --review run/review.json --out run/report.md` writes this report from a review that passes `check-final`. No model writes any of it. Use the layout by hand only when the helper cannot be run, fill every placeholder from the recorded review and stage notes, and say in the scope note that the report was assembled by hand.

```
# Writing review

## What was reviewed
<scope_note: what was read (edition, sections or blocks, reader runs) and what was left out>

Not assessed: <not_assessed entries joined with "; ">.

Records: <included> of <total> blocks read; reader checkpoints: <count per mode, or "none">; candidates: <a> accepted, <q> author questions, <g> merged, <r> rejected; independent challenge: run on all <n> candidates | not run (<no_challenge_reason>); fidelity audits attached: <count of repairs with an audit>.

## Priority list
1. [misleads] B0007 "<first 60 characters of the anchor quote>…" — <the reader's question as first recorded> (W03)
2. [hinders] B0012 "<anchor quote>" — <the reader's question> (W01)

The list shows the 10 most consequential of <N>; all are under Findings.

## Questions for the author
- W05, B0012: <author_question>

## Findings
### W03 · misleads · confidence high · B0007
> <anchor quote>

- Reader's question: <the candidate's question>
- Diagnosis: <diagnosis>
- Action: <action>
- Proposed wording: <proposed_wording>
- Question for the author: <author_question>
- Meaning check: <scientific_fidelity>
- Done when: <close_criterion>
- Support: raised at <n> reader checkpoints; challenger: <verdict, or "not run">.

## Minor items
<N> minor items do not hinder understanding; they are listed in <appendix file name>.
```

Rules of the layout:

- The priority list holds the accepted findings that mislead or hinder, in the order of `priority_list` in the review: at most ten, `misleads` before `hinders`. The sentence under the list appears only when more such findings exist than are listed. When there is none, the list is the one sentence “No finding that misleads or hinders was accepted.”
- “Questions for the author” lists every finding above minor that carries an author question, and is left out when there is none.
- “Findings” holds every finding that misleads or hinders, accepted or put to the author, exactly once: the priority list first, then the rest in the order of the review. The lines “Proposed wording” and “Question for the author” appear only when the issue has one. When there is no such finding, the section says “None.”
- Minor items appear only in the appendix file (`<report name>-minor.md` beside the report), each with the same block as a finding. The “Minor items” section and the appendix are left out when there is no minor item.
- Nothing else is added: no overall score, no grade of a version as better or clearer, no finding that is not in the checked review. The stage notes, receipts, reader files, and audits stay in the run directory as the supporting record; tell the user where they are.
