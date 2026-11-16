# Planted-defect tests

A small test set for the parts of the skill that look for clarity problems. It gives numbers that do not depend on anyone's opinion of a review: the defects are planted by a script, and a script decides whether a finding points at one.

## What is here

| File | Contents |
| --- | --- |
| `manuscript.md` | A short invented manuscript, written so that every term is explained before it is used and every reference is clear. No real manuscript text. |
| `defects.json` | Eight clarity defects, each a list of exact replacements in the manuscript, with the passages a finding should point at; and four control passages that no defect touches. |
| `planted.py` | `list`, `plant`, and `score`. Standard library plus the skill's `review_packets.py`. |
| `tests/` | Unit tests for planting and scoring. They call no model. |

The defect types: a definition moved after the first use; an acronym used without its expansion; a pronoun with two possible referents; a definite reference to something not yet introduced; a deleted sentence that linked two paragraphs; a second name for one thing; earlier information relied on without a reminder; a count that disagrees with an earlier count.

## Use

```bash
python3 evals/planted.py plant --out /tmp/planted            # all eight defects
python3 evals/planted.py plant --defects D01,D05 --out /tmp/two
python3 evals/planted.py score --key /tmp/planted/key.json --manuscript /tmp/planted/manuscript.md --findings checks.json
python3 -I -m unittest discover -s evals/tests
```

`plant` writes `manuscript.md` (the planted copy) and `key.json`. Run the part of the skill under test on the planted copy, then give `score` the file that holds its findings: the checks or reconcile notes (`candidates`), a prefix reader's observation or a final review (`issues`), or a plain list of `{block_id, quote}`. A defect counts as found when the anchor of at least one finding lies in one of its expected blocks. A finding on a control passage is a false alarm. Findings elsewhere are counted and not judged.

Runs that call a model are not stable from one call to the next, so compare two versions of a component over several runs each, with the same model.

## What the numbers mean

Recall on planted defects and false alarms on four control passages of one short invented text. Planted defects are easier to find than the ones in real manuscripts, the manuscript is far shorter than a paper, and the score says nothing about whether findings on a real manuscript are right or worth acting on. Use the numbers to check that a change to the skill did not make a component worse, not to claim that reviews improved.
