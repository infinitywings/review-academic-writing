#!/usr/bin/env python3
"""Plant known clarity defects in the example manuscript and score findings against the key.

Standard library only (plus the skill's review_packets.py for block ids).

  list   the defects in defects.json
  plant  write a copy of the manuscript with the chosen defects and a key that says, for each
         defect, which blocks a finding should point at, and which blocks are controls
  score  given findings made on the planted copy, say which defects were found and how many
         findings fell on control blocks

The key also carries material for testing a challenger of candidates: for each defect the
reader's question at the planted place and whether a challenge should leave the candidate
standing (`kept`) or dispute it (`disputed`), and a list of `adequate` objections, questions
that the manuscript answers before the quoted place and that a challenger should dispute.

A defect is a list of exact replacements; each must match the text exactly once, so planting
is deterministic. A defect counts as found when the quotation of at least one finding
overlaps one of its expected quotations in the same block, so a different question about the
same paragraph does not count (a finding that gives a block and no quotation counts by block).
A finding on a control block (a passage written to have no problem and left untouched) is a
false alarm. No model judges anything here.

What the numbers mean: recall on planted defects and false alarms on control passages of one
short invented manuscript. Planted defects are easier to find than real ones, and the score
says nothing about whether findings on a real manuscript are right.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "review-academic-writing" / "scripts"))
import review_packets as rp  # noqa: E402

Error = rp.ReviewDataError
FINDING_LISTS = ("candidates", "issues", "observations", "findings")


def load_spec(path: Path) -> dict[str, Any]:
    spec = rp.load_json(path)
    if not isinstance(spec, dict) or not isinstance(spec.get("defects"), list) or not isinstance(spec.get("controls"), list) or not isinstance(spec.get("manuscript"), str):
        raise Error("The defects file needs manuscript (a file name), controls (quotes), and defects (a list).")
    seen: set[str] = set()
    for defect in spec["defects"]:
        if not isinstance(defect, dict) or not all(isinstance(defect.get(k), str) and defect[k] for k in ("id", "type", "description")):
            raise Error("Every defect needs an id, a type, and a description.")
        if defect["id"] in seen:
            raise Error(f"Defect id {defect['id']} is listed twice.")
        seen.add(defect["id"])
        steps = defect.get("replace")
        if not isinstance(steps, list) or not steps or any(not isinstance(s, dict) or not isinstance(s.get("find"), str) or not s["find"] or not isinstance(s.get("with"), str) for s in steps):
            raise Error(f"Defect {defect['id']}: replace must be a nonempty list of {{find, with}} strings.")
        if not isinstance(defect.get("expect"), list) or not defect["expect"] or any(not isinstance(q, str) or not q for q in defect["expect"]):
            raise Error(f"Defect {defect['id']}: expect must be a nonempty list of quotes from the planted text.")
        if not isinstance(defect.get("question"), str) or not defect["question"] or defect.get("challenge_expected") not in ("kept", "disputed"):
            raise Error(f"Defect {defect['id']}: question (the reader's question at the first expected quote) and challenge_expected (kept or disputed) are required.")
    if any(not isinstance(q, str) or not q for q in spec["controls"]):
        raise Error("controls must be a list of quotes.")
    adequate = spec.setdefault("adequate", [])
    if not isinstance(adequate, list) or any(not isinstance(a, dict) or not all(isinstance(a.get(k), str) and a[k] for k in ("id", "quote", "question")) for a in adequate):
        raise Error("adequate must be a list of {id, quote, question}: objections the manuscript already answers before the quoted place.")
    if len({a["id"] for a in adequate} | seen) != len(adequate) + len(seen):
        raise Error("adequate ids must be distinct from each other and from the defect ids.")
    return spec


def choose(spec: dict[str, Any], wanted: str) -> list[dict[str, Any]]:
    if wanted == "all":
        return list(spec["defects"])
    if wanted == "none":
        return []
    ids = [part.strip() for part in wanted.split(",") if part.strip()]
    by_id = {d["id"]: d for d in spec["defects"]}
    unknown = [i for i in ids if i not in by_id]
    if unknown or not ids or len(ids) != len(set(ids)):
        raise Error("--defects must be 'all', 'none', or a comma-separated list of distinct ids from the defects file" + (f"; unknown: {', '.join(unknown)}." if unknown else "."))
    return [by_id[i] for i in ids]


def apply(text: str, defects: list[dict[str, Any]]) -> str:
    for defect in defects:
        for step in defect["replace"]:
            count = text.count(step["find"])
            if count != 1:
                raise Error(f"Defect {defect['id']}: the text to replace occurs {count} times, not once: {step['find'][:60]!r}.")
            text = text.replace(step["find"], step["with"])
    return text


def block_of(index: dict[str, Any], quote: str, label: str) -> str:
    holders = [b["block_id"] for b in index["blocks"] if quote in b["text"]]
    if len(holders) != 1:
        raise Error(f"{label}: the quote is in {len(holders)} blocks of the planted text, not one: {quote[:60]!r}.")
    return holders[0]


def build_key(text: str, spec: dict[str, Any], planted: list[dict[str, Any]]) -> dict[str, Any]:
    index = rp.build_index(text)
    defects = [{"id": d["id"], "type": d["type"], "description": d["description"], "question": d["question"], "challenge_expected": d["challenge_expected"],
                "expect": [{"block_id": block_of(index, q, f"Defect {d['id']}"), "quote": q} for q in d["expect"]]} for d in planted]
    adequate = [{"id": a["id"], "question": a["question"], "anchor": {"block_id": block_of(index, a["quote"], f"Adequate {a['id']}"), "quote": a["quote"]}} for a in spec["adequate"]]
    controls = [{"block_id": block_of(index, q, "Control"), "quote": q} for q in spec["controls"]]
    expected = {a["block_id"] for d in defects for a in d["expect"]}
    clash = sorted({c["block_id"] for c in controls} & expected)
    if clash:
        raise Error(f"Control blocks {', '.join(clash)} are also expected blocks of a planted defect; a control must be a passage no defect touches.")
    return {"snapshot_id": index["snapshot_id"], "planted": [d["id"] for d in planted], "defects": defects, "controls": controls, "adequate": adequate}


def plant(spec_path: Path, wanted: str, out: Path) -> dict[str, Any]:
    spec = load_spec(spec_path)
    planted = choose(spec, wanted)
    clean = rp.normalize_text((spec_path.parent / spec["manuscript"]).read_text(encoding="utf-8"))
    text = apply(clean, planted)
    key = build_key(text, spec, planted)
    out.mkdir(parents=True, exist_ok=False)
    (out / "manuscript.md").write_text(text, encoding="utf-8", newline="\n")
    rp.write_json_atomic(out / "key.json", key)
    return {"ok": True, "command": "plant", "manuscript": str(out / "manuscript.md"), "key": str(out / "key.json"),
            "planted": key["planted"], "control_blocks": sorted({c["block_id"] for c in key["controls"]})}


def anchors_from(value: Any, label: str) -> list[dict[str, str]]:
    """Findings as {id, block_id, quote} from a candidates/issues/observations/findings file or a plain list."""
    if isinstance(value, dict):
        items = [item for name in FINDING_LISTS for item in (value.get(name) if isinstance(value.get(name), list) else [])]
    elif isinstance(value, list):
        items = value
    else:
        raise Error(f"{label} must be a JSON object with candidates, issues, observations, or findings, or a JSON list.")
    rows = []
    for n, item in enumerate(items, 1):
        anchor = item.get("anchor", item) if isinstance(item, dict) else None
        if not isinstance(anchor, dict) or not isinstance(anchor.get("block_id"), str):
            raise Error(f"{label}: finding {n} has no anchor with a block_id.")
        quote = anchor.get("quote")
        rows.append({"id": str(item.get("id") or item.get("candidate_id") or f"{label}#{n}"), "block_id": anchor["block_id"], "quote": quote if isinstance(quote, str) else ""})
    return rows


def score(key: dict[str, Any], text: str, findings: list[dict[str, str]]) -> dict[str, Any]:
    index = rp.build_index(rp.normalize_text(text))
    if index["snapshot_id"] != key.get("snapshot_id"):
        raise Error("The manuscript is not the planted copy this key was written for.")
    blocks = {b["block_id"]: b["text"] for b in index["blocks"]}
    for row in findings:
        if row["block_id"] not in blocks:
            raise Error(f"Finding {row['id']} points at {row['block_id']}, which is not a block of the planted manuscript.")
        if row["quote"] and row["quote"] not in blocks[row["block_id"]]:
            raise Error(f"Finding {row['id']} quotes text that is not in {row['block_id']}; it was made on a different text.")
    controls = {c["block_id"] for c in key["controls"]}

    def span(block_id: str, quote: str) -> tuple[int, int]:
        start = blocks[block_id].find(quote)
        return start, start + len(quote)

    def hits(row: dict[str, str], anchor: dict[str, str]) -> bool:
        if row["block_id"] != anchor["block_id"]:
            return False
        if not row["quote"]:
            return True
        (a, b), (c, d) = span(row["block_id"], row["quote"]), span(anchor["block_id"], anchor["quote"])
        return a < d and c < b

    defects = []
    for defect in key["defects"]:
        by = [row["id"] for row in findings if any(hits(row, anchor) for anchor in defect["expect"])]
        defects.append({"id": defect["id"], "type": defect["type"], "found": bool(by), "by": by})
    counted = {i for d in defects for i in d["by"]}
    false_alarms = [row["id"] for row in findings if row["block_id"] in controls]
    found = sum(1 for d in defects if d["found"])
    return {"ok": True, "command": "score", "planted": len(defects), "found": found, "missed": [d["id"] for d in defects if not d["found"]],
            "defects": defects, "findings": len(findings), "control_blocks": len(controls), "false_alarms": false_alarms,
            "elsewhere": sum(1 for row in findings if row["id"] not in counted and row["block_id"] not in controls),
            "note": "Recall on planted defects and false alarms on control passages of an invented manuscript; not a measure of findings on real manuscripts."}


def parser() -> argparse.ArgumentParser:
    p = rp.JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("list", "plant"):
        command = commands.add_parser(name)
        command.add_argument("--defects-file", default=str(HERE / "defects.json"), help="The defects file (default: evals/defects.json); its manuscript is read from the same directory.")
        if name == "plant":
            command.add_argument("--defects", default="all", help="'all' (default), 'none' (the clean manuscript, to see what is flagged without any planted defect), or a comma-separated list of defect ids, planted in that order.")
            command.add_argument("--out", required=True, help="New directory for the planted manuscript.md and key.json.")
    scoring = commands.add_parser("score")
    scoring.add_argument("--key", required=True, help="key.json written by plant.")
    scoring.add_argument("--manuscript", required=True, help="The planted manuscript.md the findings were made on.")
    scoring.add_argument("--findings", required=True, action="append", help="A JSON file of findings with anchors (checks or reconcile notes, a prefix observation, a final review, or a list of {block_id, quote}); repeatable.")
    scoring.add_argument("--out", help="Also write the score to this new JSON file.")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "list":
            spec = load_spec(Path(args.defects_file))
            result = {"ok": True, "command": "list", "defects": [{k: d[k] for k in ("id", "type", "description")} for d in spec["defects"]], "controls": len(spec["controls"])}
        elif args.command == "plant":
            result = plant(Path(args.defects_file), args.defects, Path(args.out))
        else:
            findings = [row for path in args.findings for row in anchors_from(rp.load_json(path), Path(path).name)]
            result = score(rp.load_json(args.key), Path(args.manuscript).read_text(encoding="utf-8"), findings)
            if args.out:
                rp.write_json_atomic(args.out, result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Error as exc:
        error = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        error = {"ok": False, "error": {"code": "input_output_error", "message": f"{type(exc).__name__}: {exc}"}}
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
