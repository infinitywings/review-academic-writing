# Planted-defect tests

A small test set for the parts of the skill that look for clarity problems. It gives numbers that do not depend on anyone's opinion of a review: the defects are planted by a script, and a script decides whether a finding points at one.

## What is here

| File | Contents |
| --- | --- |
| `manuscript.md` | A short invented manuscript, written so that every term is explained before it is used and every reference is clear. No real manuscript text. |
| `defects.json` | Eight clarity defects, each a list of exact replacements in the manuscript, with the passages a finding should point at; and four control passages that no defect touches. |
| `planted.py` | `list`, `plant`, and `score`. Standard library plus the skill's `review_packets.py`. |
| `audit-cases.json` | Fifteen sentence pairs for the fidelity auditor, each with the verdict it should reach: ten rewrites that change the meaning (a dropped hedge, a hedge moved to another claim, an added quantifier, a plan stated as done, and so on) and five that keep it. |
| `run.py` | `audit` and `prefix`: run those parts of the skill with a real model and count the outcomes. |
| `tests/` | Unit tests for planting, scoring, and the runner. They call no model; the runner is tested with a stand-in program. |

The defect types: a definition moved after the first use; an abbreviation that is never expanded; a pronoun with two possible referents; a definite reference to something not yet introduced; a result whose method paragraph was deleted; a second name for one thing; earlier information relied on without a reminder; a count that disagrees with an earlier count.

## Use

```bash
python3 evals/planted.py plant --out /tmp/planted            # all eight defects
python3 evals/planted.py plant --defects D01,D05 --out /tmp/two
python3 evals/planted.py plant --defects none --out /tmp/clean   # the clean manuscript, controls only
python3 evals/planted.py score --key /tmp/planted/key.json --manuscript /tmp/planted/manuscript.md --findings checks.json
python3 -I -m unittest discover -s evals/tests
```

`plant` writes `manuscript.md` (the planted copy) and `key.json`. Run the part of the skill under test on the planted copy, then give `score` the file that holds its findings: the checks or reconcile notes (`candidates`), a prefix reader's observation or a final review (`issues`), or a plain list of `{block_id, quote}`. A defect counts as found when the quotation of at least one finding overlaps one of its expected quotations in the same block; a different question about the same paragraph does not count. A finding on a control passage is a false alarm. Findings elsewhere are counted and not judged.

To run a part of the skill with a real model (the model is required; there is no default):

```bash
python3 evals/run.py audit  --model MODEL --repeats 3 --out /tmp/eval-audit
python3 evals/run.py prefix --model MODEL --repeats 3 --out /tmp/eval-prefix
```

`audit` sends each case to `dispatch_reader.py audit` and compares the verdict with the expected one, exactly: `undetermined` matches neither. `prefix` plants all eight defects, sends one prefix reader to the end of every section, and scores the readers' issues; with `--defects none` it runs the same readers on the clean manuscript. If every call fails (an expired login, a missing command), both stop with the failure and write no score. Both keep every prompt, raw answer, and cost under `--out` and write `results.json` there. Results are not committed; keep them outside the repository or in an ignored directory.

Runs that call a model are not stable from one call to the next, so compare two versions of a component over several runs each, with the same model.

## Read the baseline first

Readers ask questions about any text. On the clean manuscript, prefix readers still raise several per run, most of them about details of the invented study and a few on the control passages. Run `--defects none` with the same model before reading a planted run, and treat its counts as the floor: a component is not noisier than another because of questions it also asks about the clean text.

Two defects are not expected from a reader that sees the whole text so far. D07 (earlier information relied on without a reminder) is written for a reader with limited memory, because anyone who can see the earlier section can look the value up. D05 (a result whose method paragraph was deleted) may be inferred from the introduction. A reader that misses these two is not thereby worse.

The audit cases are easy on purpose in this first set: each rewrite changes one thing in a short sentence. A strong model may match all of them, which shows only that the clear cases are handled.

## What the numbers mean

Recall on planted defects and false alarms on four control passages of one short invented text. Planted defects are easier to find than the ones in real manuscripts, the manuscript is far shorter than a paper, and the score says nothing about whether findings on a real manuscript are right or worth acting on. Use the numbers to check that a change to the skill did not make a component worse, not to claim that reviews improved.
