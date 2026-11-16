#!/usr/bin/env python3
"""Run parts of the skill on the planted-defect tests with a real model, and count the outcomes.

  audit    send every case of audit-cases.json to `dispatch_reader.py audit` and compare the
           auditor's verdict with the expected one
  prefix   plant the defects, send a prefix reader to the end of every section of the planted
           manuscript, and score the readers' issues with planted.py

Both take --model (required; the tests have no built-in model) and --repeats: a model does
not give the same answer twice, so compare versions over several runs. Calls go through the
skill's own dispatcher, so every prompt, raw answer, and cost is saved under --out. Results
stay in --out; do not commit them unless you mean to publish them.

The verdict comparison is exact: an expected `changed` is matched only by `changed`, an
expected `preserved` only by `preserved`. `undetermined` and a call that produced no audit
are reported as themselves.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "skills" / "review-academic-writing"
sys.path.insert(0, str(HERE))
import planted  # noqa: E402

rp = planted.rp
Error = rp.ReviewDataError
DISPATCH = SKILL / "scripts" / "dispatch_reader.py"
EXPECTED = {"changed", "preserved"}


def dispatch(kind: str, events: Path, argv: list[str], options: argparse.Namespace) -> dict[str, Any]:
    """One dispatcher call in its own events directory; returns its summary.json (or the failure)."""
    command = [sys.executable, "-I", str(DISPATCH), kind, "--events", str(events), "--runtime", options.runtime, "--model", options.model, *argv]
    for flag in ("claude", "codex", "config", "effort"):
        if getattr(options, flag):
            command += [f"--{flag}", getattr(options, flag)]
    completed = subprocess.run(command, capture_output=True, text=True)
    summary = events / "summary.json"
    result = rp.load_json(summary) if summary.is_file() else {"ok": False, "outcome": (completed.stderr or completed.stdout).strip()[:500], "totals": {}}
    if not result.get("ok"):
        # The dispatcher's outcome names the exit code; the command's own words (an expired login, a rate limit) are in the raw answer.
        raw = sorted(events.glob("*.response.raw"))
        said = raw[0].read_text(encoding="utf-8", errors="replace").strip()[:300] if raw else ""
        result["failure"] = " | ".join(part for part in (str(result.get("outcome", "")), said) if part)
    return result


def stop_if_nothing_ran(summaries: list[dict[str, Any]]) -> None:
    """A run in which no call succeeded measured nothing; say why instead of reporting zeros."""
    if summaries and not any(s.get("ok") for s in summaries):
        raise Error("Every call failed, so there is nothing to score. First failure: " + str(summaries[0].get("failure", "unknown")), "no_call_succeeded")


def totals(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    cost = [s.get("totals", {}).get("cost_usd") for s in summaries]
    return {"calls": sum(s.get("totals", {}).get("calls", 0) for s in summaries),
            "seconds": round(sum(s.get("totals", {}).get("seconds", 0) or 0 for s in summaries), 1),
            "cost_usd": round(sum(c for c in cost if c), 4) if any(c is not None for c in cost) else None}


def load_cases(path: Path) -> list[dict[str, Any]]:
    value = rp.load_json(path)
    cases = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(cases, list) or not cases:
        raise Error("The cases file needs a nonempty cases list.")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not all(isinstance(case.get(k), str) and case[k] for k in ("id", "type", "original", "proposed_wording", "expected")):
            raise Error("Every case needs id, type, original, proposed_wording, and expected.")
        if case["expected"] not in EXPECTED:
            raise Error(f"Case {case['id']}: expected must be changed or preserved.")
        if case["id"] in seen:
            raise Error(f"Case id {case['id']} is listed twice.")
        seen.add(case["id"])
    return cases


def audit_one(case: dict[str, Any], repeat: int, out: Path, options: argparse.Namespace) -> dict[str, Any]:
    home = out / f"{case['id']}-r{repeat}"
    home.mkdir(parents=True)
    request = {"candidate_id": case["id"], "original": [{"block_id": "B0001", "quote": case["original"]}], "proposed_wording": case["proposed_wording"], "basis": []}
    (home / "input.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = dispatch("audit", home / "events", ["--input", str(home / "input.json"), "--out", str(home / "audit.json"), "--label", case["id"]], options)
    verdict, changed = "no_audit", []
    if (home / "audit.json").is_file():
        audit = rp.load_json(home / "audit.json")
        verdict = audit["verdict"]
        changed = sorted(k for k, v in audit["checks"].items() if v == "changed")
    return {"case": case["id"], "repeat": repeat, "verdict": verdict, "changed": changed, "summary": summary}


def run_audit(options: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(Path(options.cases))
    out = Path(options.out)
    out.mkdir(parents=True, exist_ok=False)
    jobs = [(case, r) for case in cases for r in range(1, options.repeats + 1)]
    with ThreadPoolExecutor(max_workers=options.jobs) as pool:
        rows = list(pool.map(lambda job: audit_one(job[0], job[1], out, options), jobs))
    stop_if_nothing_ran([row["summary"] for row in rows])
    table = []
    for case in cases:
        verdicts = [row["verdict"] for row in rows if row["case"] == case["id"]]
        table.append({"id": case["id"], "type": case["type"], "expected": case["expected"], "verdicts": verdicts,
                      "matched": sum(1 for v in verdicts if v == case["expected"]),
                      "changed_properties": sorted({p for row in rows if row["case"] == case["id"] for p in row["changed"]})})

    def share(expected: str) -> dict[str, int]:
        chosen = [row for row in table if row["expected"] == expected]
        return {"cases": len(chosen), "runs": sum(len(row["verdicts"]) for row in chosen), "matched": sum(row["matched"] for row in chosen)}

    result = {"ok": True, "command": "audit", "model": options.model, "runtime": options.runtime, "repeats": options.repeats,
              "expected_changed": share("changed"), "expected_preserved": share("preserved"),
              "calls_without_audit": sum(1 for row in rows if row["verdict"] == "no_audit"), "cases": table,
              "totals": totals([row["summary"] for row in rows]),
              "note": "Auditor verdicts on invented sentence pairs with a fixed expected verdict; not a measure of audits on real edits."}
    rp.write_json_atomic(out / "results.json", result)
    return result


def checkpoints(index: dict[str, Any]) -> list[str]:
    """The last block of every section: a body block followed by a heading or by the end of the text."""
    blocks = index["blocks"]
    heading = [b["text"].lstrip().startswith("#") for b in blocks]
    return [b["block_id"] for n, b in enumerate(blocks) if not heading[n] and (n + 1 == len(blocks) or heading[n + 1])]


def prefix_one(repeat: int, number: int, block: str, home: Path, options: argparse.Namespace) -> dict[str, Any]:
    label = f"P{number:02d}"
    events = home / f"r{repeat}" / label
    events.parent.mkdir(parents=True, exist_ok=True)
    summary = dispatch("prefix", events, ["--index", str(home / "index.json"), "--profile", options.profile, "--through-block", block, "--label", label], options)
    observation = events / f"{label}.observation.json"
    issues = rp.load_json(observation).get("issues", []) if observation.is_file() and summary.get("ok") else []
    return {"repeat": repeat, "label": label, "through_block": block, "ok": bool(summary.get("ok")),
            "findings": [{"id": f"{label}:{issue.get('id', n)}", "anchor": issue["anchor"]} for n, issue in enumerate(issues, 1)], "summary": summary}


def run_prefix(options: argparse.Namespace) -> dict[str, Any]:
    out = Path(options.out)
    out.mkdir(parents=True, exist_ok=False)
    planted.plant(Path(options.defects_file), options.defects, out / "planted")
    text = (out / "planted" / "manuscript.md").read_text(encoding="utf-8")
    key = rp.load_json(out / "planted" / "key.json")
    made = subprocess.run([sys.executable, "-I", str(SKILL / "scripts" / "review_packets.py"), "index", "--input", str(out / "planted" / "manuscript.md"), "--out", str(out / "index.json")], capture_output=True, text=True)
    if made.returncode != 0:
        raise Error("Could not index the planted manuscript: " + made.stderr.strip()[:300])
    points = checkpoints(rp.load_json(out / "index.json"))
    jobs = [(r, n, block) for r in range(1, options.repeats + 1) for n, block in enumerate(points, 1)]
    with ThreadPoolExecutor(max_workers=options.jobs) as pool:
        rows = list(pool.map(lambda job: prefix_one(*job, out, options), jobs))
    stop_if_nothing_ran([row["summary"] for row in rows])
    runs = []
    for repeat in range(1, options.repeats + 1):
        mine = [row for row in rows if row["repeat"] == repeat]
        findings = planted.anchors_from([f for row in mine for f in row["findings"]], f"r{repeat}")
        scored = planted.score(key, text, findings)
        failed = [row["label"] for row in mine if not row["ok"]]
        # A run with a failed reader saw less of the text than the others; it is listed but not counted.
        runs.append({"repeat": repeat, "complete": not failed, "readers": len(mine), "readers_failed": failed,
                     **{k: scored[k] for k in ("found", "missed", "findings", "false_alarms", "elsewhere")},
                     "found_by": {d["id"]: d["by"] for d in scored["defects"] if d["found"]}})
    complete = [run for run in runs if run["complete"]]
    result = {"ok": True, "command": "prefix", "model": options.model, "runtime": options.runtime, "repeats": options.repeats,
              "planted": key["planted"], "checkpoints": points, "complete_runs": len(complete),
              "found_in_runs": {d: sum(1 for run in complete if d in run["found_by"]) for d in key["planted"]},
              "runs": runs, "totals": totals([row["summary"] for row in rows]),
              "note": "Prefix readers on an invented manuscript with planted defects; recall and false alarms here do not carry over to real manuscripts."}
    rp.write_json_atomic(out / "results.json", result)
    return result


def parser() -> argparse.ArgumentParser:
    p = rp.JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("audit", "prefix"):
        command = commands.add_parser(name)
        command.add_argument("--model", required=True, help="Model for every call, passed to dispatch_reader.py (or 'default' for the command's own).")
        command.add_argument("--out", required=True, help="New directory for the call files and results.json.")
        command.add_argument("--repeats", type=int, default=1, help="Runs per case or per reader set (default 1).")
        command.add_argument("--jobs", type=int, default=4, help="Calls in flight at once (default 4).")
        command.add_argument("--runtime", choices=("claude", "codex", "http"), default="claude")
        for flag, text in (("claude", "claude executable"), ("codex", "codex executable"), ("config", "JSON config for --runtime http"), ("effort", "effort setting passed through")):
            command.add_argument(f"--{flag}", help=text)
    commands.choices["audit"].add_argument("--cases", default=str(HERE / "audit-cases.json"))
    commands.choices["prefix"].add_argument("--defects-file", default=str(HERE / "defects.json"))
    commands.choices["prefix"].add_argument("--defects", default="all", help="'all' (default), 'none' for the clean manuscript, or a comma-separated list of defect ids.")
    commands.choices["prefix"].add_argument("--profile", default=str(SKILL / "assets" / "reader-profile.json"), help="Reader profile for the packets (default: the skill's general reader).")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.repeats < 1 or args.jobs < 1:
            raise Error("--repeats and --jobs must be at least 1.")
        result = run_audit(args) if args.command == "audit" else run_prefix(args)
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
