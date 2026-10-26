#!/usr/bin/env python3
"""Dispatch reader and auditor packets to a stateless `claude -p` call and keep every byte.

Python standard library only. One runtime adapter (the `claude -p` command with tools,
MCP, and settings disabled), four subcommands:

  prefix   build or take a prefix packet, dispatch it, run review_packets check-observation.
  masked   drive a masked_reader run to completion: packet, dispatch, record, repeat.
  branch   issue one branch question from a recorded display event and record the answer.
  audit    run the independent fidelity audit for one proposed wording.

Every call writes <label>.prompt.txt, <label>.cli.json, <label>.response.raw,
<label>.response.json (when parsable) and <label>.provenance.json under the events
directory. A rejection that names the note budget, or a response that is not one JSON
object, keeps the original files, writes <label>.invalid-reason.txt, and re-dispatches the
same packet with one correction sentence (labels b and c); after two corrections the
command stops with a clear message. The summary is printed and written as summary.json.

This program records what was sent and what came back. It cannot prove that the model
used nothing else; the isolation note in each provenance file says what the runtime
flags disable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import masked_reader as mr  # noqa: E402
import review_packets as rp  # noqa: E402


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "prompts"
PROMPT_FILES = {"prefix": "prefix.txt", "masked": "masked.txt", "branch": "branch.txt", "audit": "fidelity-audit.txt"}
SYSTEM_PROMPTS = {
    "reader": "You are the reader described in the user's message. Return only the JSON the message asks for, with no code fence and no commentary.",
    "auditor": "You are the auditor described in the user's message. Return only the JSON the message asks for, with no code fence and no commentary.",
}
RUNTIME_FLAGS = ["-p", "--restricted", "--strict-mcp-config", "--tools", "", "--setting-sources", "", "--no-session-persistence", "--permission-prompts", "none"]
ISOLATION_NOTE = "claude -p --restricted --strict-mcp-config --tools '' --setting-sources '' : tools disabled by the runtime, MCP and settings skipped; not an OS sandbox"
BUDGET_TARGET_FRACTION = 0.85
MAX_CORRECTIONS = 2
ATTEMPT_SUFFIXES = ("", "b", "c")
LOOKUP_STALL_LIMIT = 3
BUDGET_MESSAGE = re.compile(r"carry_memory has (\d+) (words|characters); limit is (\d+)")
AUDIT_KEYS = {"candidate_id", "proposed_wording", "original", "verdict", "checks", "notes", "auditor"}
AUDIT_CHECKS = ("claim_strength", "quantifiers", "assumptions", "population", "measured_outcome", "time_horizon", "numerical_values", "uncertainty", "causal_vs_associative")
AUDIT_VERDICTS = {"preserved", "changed", "undetermined"}
AUDIT_CHECK_VALUES = {"same", "changed", "not_applicable"}
CODE_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(.*?)\n\s*```\s*\Z", re.S)


class DispatchError(ValueError):
    """A usage or environment error; nothing was dispatched for it."""


class Failure(Exception):
    """Why an attempt was not accepted.

    kind is `budget` (the helper named the note budget; correctable), `format` (no single
    JSON object or wrong shape; correctable), or `rejected` (any other helper rejection;
    not re-dispatched).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise DispatchError(f"Refusing to overwrite: {path}") from exc


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# ---------------------------------------------------------------- runtime adapter


class Runtime:
    """The one way this program talks to a model: `claude -p` with everything disabled."""

    def __init__(self, claude: str | None, model: str | None, effort: str | None, timeout: float) -> None:
        self.claude = claude or os.environ.get("CLAUDE_BIN") or shutil.which("claude")
        if not self.claude:
            raise DispatchError("claude executable not found; pass --claude or put claude on PATH.")
        self.model = model
        self.effort = effort
        self.timeout = timeout

    def command(self, role: str) -> list[str]:
        command = [self.claude, *RUNTIME_FLAGS, "--system-prompt", SYSTEM_PROMPTS[role], "--output-format", "json"]
        if self.model:
            command += ["--model", self.model]
        if self.effort:
            command += ["--effort", self.effort]
        return command

    def call(self, role: str, prompt: str) -> dict[str, Any]:
        command = self.command(role)
        started_at = utc_now()
        clock = time.monotonic()
        try:
            run = subprocess.run(command, input=prompt.encode("utf-8"), capture_output=True, timeout=self.timeout)
            stdout, stderr, exit_code, timed_out = run.stdout, run.stderr, run.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, exit_code, timed_out = exc.stdout or b"", exc.stderr or b"", None, True
        return {
            "command": command,
            "started_at": started_at,
            "seconds": round(time.monotonic() - clock, 1),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": stdout,
            "stderr": stderr.decode("utf-8", "replace"),
        }


def parse_result(text: str) -> tuple[Any | None, str | None, bool]:
    """Parse the model's result as one JSON object; tolerate a surrounding code fence."""
    fenced = CODE_FENCE.match(text)
    body = fenced.group(1) if fenced else text
    try:
        value = json.loads(body)
    except ValueError as exc:
        return None, f"{type(exc).__name__}: {exc}", fenced is not None
    if not isinstance(value, dict):
        return None, "the result is JSON but not a single object", fenced is not None
    return value, None, fenced is not None


# ---------------------------------------------------------------- events directory


class Events:
    """Writes one labeled file set per call and accumulates the summary."""

    def __init__(self, directory: Path, prompts_dir: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir = prompts_dir
        self.calls: list[dict[str, Any]] = []

    def generic_prompt(self, name: str) -> str:
        path = self.prompts_dir / PROMPT_FILES[name]
        if not path.is_file():
            raise DispatchError(f"Generic prompt not found: {path}")
        return path.read_text(encoding="utf-8").rstrip("\n")

    def path(self, label: str, suffix: str) -> Path:
        return self.dir / f"{label}.{suffix}"

    def dispatch(self, runtime: Runtime, role: str, label: str, generic: str, payload: bytes, packet_file: Path, note: str | None, corrects: str | None) -> dict[str, Any]:
        source = generic + ("\n\n" + note if note else "")
        prompt = source + "\n\n" + payload.decode("utf-8")
        write_new(self.path(label, "prompt.txt"), prompt.encode("utf-8"))
        if note:
            write_new(self.path(label, "prompt-source.txt"), (source + "\n").encode("utf-8"))
        run = runtime.call(role, prompt)
        envelope: dict[str, Any] | None
        try:
            envelope = json.loads(run["stdout"].decode("utf-8"))
            if not isinstance(envelope, dict):
                envelope = None
        except ValueError:
            envelope = None
        if envelope is not None:
            write_new(self.path(label, "cli.json"), encoded(envelope))
            result = envelope.get("result")
            raw = result.encode("utf-8") if isinstance(result, str) else encoded(result)
        else:
            write_new(self.path(label, "cli.json"), encoded({"error": "stdout was not a JSON envelope", "stdout": run["stdout"].decode("utf-8", "replace")[-4000:]}))
            raw = run["stdout"]
        write_new(self.path(label, "response.raw"), raw)
        parsed, parse_error, fence_stripped = parse_result(raw.decode("utf-8", "replace"))
        if run["exit_code"] != 0:
            parsed, parse_error = None, parse_error or f"claude exited with {run['exit_code']}" + (" (timeout)" if run["timed_out"] else "")
        response_path = self.path(label, "response.raw")
        if parsed is not None:
            write_new(self.path(label, "response.json"), encoded(parsed))
            if fence_stripped:
                response_path = self.path(label, "response.json")
        usage = (envelope or {}).get("usage") or {}
        provenance = {
            "label": label,
            "corrects": corrects,
            "started_at": run["started_at"],
            "seconds": run["seconds"],
            "exit_code": run["exit_code"],
            "command": run["command"],
            "packet_file": str(packet_file),
            "packet_sha256": sha256_bytes(packet_file.read_bytes()) if packet_file.is_file() else None,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "model_usage": sorted((envelope or {}).get("modelUsage") or {}),
            "session_id": (envelope or {}).get("session_id"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost_usd": (envelope or {}).get("total_cost_usd"),
            "permission_denials": (envelope or {}).get("permission_denials", []),
            "parse_error": parse_error,
            "fence_stripped": fence_stripped,
            "stderr": run["stderr"][-4000:],
            "isolation": ISOLATION_NOTE,
        }
        write_new(self.path(label, "provenance.json"), encoded(provenance))
        call = {"label": label, "status": "pending", "seconds": run["seconds"], "cost_usd": provenance["cost_usd"], "session_id": provenance["session_id"], "exit_code": run["exit_code"], "parsed": parsed, "parse_error": parse_error, "response_path": response_path, "provenance": provenance}
        self.calls.append(call)
        return call

    def invalid(self, label: str, failure: Failure, next_label: str | None) -> None:
        text = f"{label} not accepted ({failure.kind}): {failure}. Original files preserved unchanged."
        if next_label:
            text += f" A correction was requested as {next_label} with the same packet and the generic prompt plus one controller sentence (see {next_label}.prompt-source.txt)."
        else:
            text += " No further correction was requested."
        write_new(self.path(label, "invalid-reason.txt"), (text + "\n").encode("utf-8"))

    def finish(self, command: str, outcome: str, ok: bool, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = [{k: call[k] for k in ("label", "status", "seconds", "cost_usd", "session_id", "exit_code")} for call in self.calls]
        summary = {
            "ok": ok,
            "command": command,
            "events_dir": str(self.dir),
            "outcome": outcome,
            "calls": rows,
            "totals": {
                "calls": len(rows),
                "seconds": round(sum(r["seconds"] or 0 for r in rows), 1),
                "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 5),
            },
            **(extra or {}),
        }
        target = self.dir / "summary.json"
        if target.exists():
            target = self.dir / f"summary-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        write_new(target, encoded(summary))
        summary["summary_file"] = str(target)
        return summary


# ---------------------------------------------------------------- correction protocol


def budget_note(sizes: list[int], unit: str, limit: int) -> str:
    counting = "the whitespace-separated words" if unit == "words" else "the non-whitespace characters"
    target = math.floor(limit * BUDGET_TARGET_FRACTION)
    if len(sizes) == 1:
        history = f"a previous attempt at this same packet returned a carry_memory of {sizes[0]} {unit}"
    else:
        history = f"{len(sizes)} previous attempts at this same packet returned carry_memory of {' and '.join(str(s) for s in sizes)} {unit}"
    return (
        f"Budget note from the controller: {history}, above the {limit}-{unit[:-1]} limit in reader_profile.memory_limit. "
        f"The limit counts {counting} of gist plus every note's text. Before returning, count them yourself and keep the total at or below "
        f"{target} {unit} (85 percent of the limit); shorten note texts rather than dropping notes you still need."
    )


def format_note(errors: list[str]) -> str:
    return (
        "Format note from the controller: a previous attempt at this same input did not return one parsable JSON object in the documented shape "
        f"({'; '.join(errors)}). Return exactly one JSON object in that shape, with no code fence, no commentary, and no text before or after it."
    )


def classify_helper_error(exc: BaseException) -> Failure:
    message = str(exc)
    return Failure("budget" if BUDGET_MESSAGE.search(message) else "rejected", message)


def attempt_chain(events: Events, runtime: Runtime, role: str, base: str, generic: str, payload: bytes, packet_file: Path, accept: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any] | None, Any, Failure | None]:
    """Dispatch, validate, and correct up to MAX_CORRECTIONS times. Returns (call, accepted value, failure)."""
    budget_sizes: list[int] = []
    format_errors: list[str] = []
    note: str | None = None
    for number, suffix in enumerate(ATTEMPT_SUFFIXES):
        label = base + suffix
        call = events.dispatch(runtime, role, label, generic, payload, packet_file, note, base if suffix else None)
        if call["parsed"] is None:
            failure = Failure("format", call["parse_error"] or "no response")
        else:
            try:
                accepted = accept(call)
                call["status"] = "ok"
                return call, accepted, None
            except Failure as exc:
                failure = exc
        call["status"] = "invalid_" + failure.kind if failure.kind != "rejected" else "rejected"
        last = failure.kind == "rejected" or number >= MAX_CORRECTIONS
        next_label = None if last else base + ATTEMPT_SUFFIXES[number + 1]
        events.invalid(label, failure, next_label)
        if last:
            return call, None, failure
        if failure.kind == "budget":
            match = BUDGET_MESSAGE.search(str(failure))
            budget_sizes.append(int(match.group(1)))
            note = budget_note(budget_sizes, match.group(2), int(match.group(3)))
        else:
            format_errors.append(str(failure))
            note = format_note(format_errors)
    raise AssertionError("unreachable")


def stop_message(base: str, failure: Failure) -> str:
    if failure.kind == "rejected":
        return f"{base}: the helper rejected the response and no correction applies ({failure}). Stopped."
    return f"{base}: two corrections were requested and the response still failed ({failure.kind}: {failure}). Stopped after attempts {base}, {base}b, {base}c."


# ---------------------------------------------------------------- subcommands


def next_label(events: Events, prefix: str) -> str:
    used = {p.name.split(".")[0].rstrip("bc") for p in events.dir.glob(f"{prefix}*.prompt.txt")}
    number = 1
    while f"{prefix}{number:02d}" in used:
        number += 1
    return f"{prefix}{number:02d}"


def run_prefix(args: argparse.Namespace, events: Events, runtime: Runtime) -> dict[str, Any]:
    label = args.label or next_label(events, "P")
    if args.packet:
        packet_file = Path(args.packet)
        packet = rp.validate_packet(rp.load_json(packet_file))
    else:
        if not args.index or not args.profile:
            raise DispatchError("prefix needs --packet, or --index and --profile with one boundary option.")
        packet = rp.build_packet(rp.load_json(args.index), rp.load_json(args.profile), through_block=args.through_block, stop_after_text=args.stop_after_text, stop_offset=args.stop_offset, occurrence=args.occurrence)
        packet_file = events.path(label, "packet.json")
        rp.write_json_atomic(packet_file, packet)
    payload = packet_file.read_bytes()

    def accept(call: dict[str, Any]) -> dict[str, Any]:
        try:
            check = rp.check_observation(packet, call["parsed"])
        except rp.ReviewDataError as exc:
            raise Failure("rejected", str(exc)) from exc
        write_new(events.path(call["label"], "check.json"), encoded(check))
        write_new(events.path(call["label"], "observation.json"), encoded(call["parsed"]))
        return check

    call, check, failure = attempt_chain(events, runtime, "reader", label, events.generic_prompt("prefix"), payload, packet_file, accept)
    if failure is not None:
        return events.finish("prefix", stop_message(label, failure), False, {"label": label})
    return events.finish("prefix", f"{call['label']} passed check-observation with {check['issue_count']} issue(s).", True, {"label": call["label"], "checkpoint_id": packet["checkpoint_id"]})


def run_masked(args: argparse.Namespace, events: Events, runtime: Runtime) -> dict[str, Any]:
    run = Path(args.run).resolve()
    if not run.exists():
        if not args.case or not args.profile:
            raise DispatchError("masked needs an existing --run, or --case and --profile to start one at --run.")
        mr.start(args.case, args.profile, run)
    generic = events.generic_prompt("masked")
    loop: list[dict[str, Any]] = []
    stall = 0
    outcome, ok = "", False
    while True:
        _run, _manifest, case, _profile, state, seq, _previous = mr.load_run(run)
        if state["cursor"] >= len(case["windows"]):
            outcome, ok = f"Run complete after {len(state['events'])} recorded event(s).", True
            break
        if len(events.calls) >= args.max_events:
            outcome = f"Stopped: --max-events {args.max_events} reached with {len(state['events'])} recorded event(s)."
            break
        label = f"E{seq:02d}"
        packet_file = events.path(label, "packet.json")
        issued = mr.packet(run, packet_file, state["pending_lookup"])
        packet = json.loads(packet_file.read_bytes())
        seen_before = set(state["seen"]) | set(state["seen_assets"])

        def accept(call: dict[str, Any], packet_file: Path = packet_file) -> dict[str, Any]:
            try:
                return mr.record(run, packet_file, call["response_path"], agent_id=call["session_id"])
            except (mr.Invalid, KeyError, TypeError, ValueError) as exc:
                raise classify_helper_error(exc) from exc

        call, record, failure = attempt_chain(events, runtime, "reader", label, generic, packet_file.read_bytes(), packet_file, accept)
        entry = {"sequence": seq, "mode": issued["mode"], "label": call["label"], "status": call["status"]}
        if failure is not None:
            entry["error"] = str(failure)
            loop.append(entry)
            outcome = stop_message(label, failure)
            break
        entry["record"] = {k: v for k, v in record.items() if k != "ok"}
        if packet["mode"] == "lookup":
            new_ids = sorted({m["id"] for m in packet["materials"]} - seen_before)
            entry["new_source_ids"] = new_ids
            stall = 0 if new_ids else stall + 1
        elif record["next_mode"] != "lookup":
            stall = 0
        loop.append(entry)
        if stall >= LOOKUP_STALL_LIMIT:
            outcome = f"Stopped: {LOOKUP_STALL_LIMIT} consecutive lookups brought no new source ids (execution limit, not a reader defect)."
            break
    return events.finish("masked", outcome, ok, {"run": str(run), "loop": loop})


def run_branch(args: argparse.Namespace, events: Events, runtime: Runtime) -> dict[str, Any]:
    run = Path(args.run).resolve()
    _run, manifest, _case, profile, state, _seq, _previous = mr.load_run(run)
    bseq = len(mr.load_branches(run, manifest, profile, state)) + 1
    label = args.label or f"BR{bseq:02d}"
    packet_file = events.path(label, "packet.json")
    mr.branch(run, args.from_sequence, args.question, packet_file)

    def accept(call: dict[str, Any]) -> dict[str, Any]:
        try:
            return mr.branch_record(run, packet_file, call["response_path"], agent_id=call["session_id"])
        except (mr.Invalid, KeyError, TypeError, ValueError) as exc:
            raise Failure("rejected", str(exc)) from exc

    call, record, failure = attempt_chain(events, runtime, "reader", label, events.generic_prompt("branch"), packet_file.read_bytes(), packet_file, accept)
    if failure is not None:
        return events.finish("branch", stop_message(label, failure), False, {"label": label, "run": str(run)})
    return events.finish("branch", f"{call['label']} recorded as branch {record['branch_sequence']} (support: {record['support']}).", True, {"label": call["label"], "run": str(run), "branch_sequence": record["branch_sequence"]})


def check_audit_shape(audit: Any, request: dict[str, Any]) -> None:
    if not isinstance(audit, dict) or set(audit) - {"auditor"} != AUDIT_KEYS - {"auditor"}:
        raise Failure("format", "audit must have exactly candidate_id, proposed_wording, original, verdict, checks, notes, auditor")
    if audit["candidate_id"] != request["candidate_id"]:
        raise Failure("format", "candidate_id must be copied from the input")
    if audit["proposed_wording"] != request["proposed_wording"]:
        raise Failure("format", "proposed_wording must be copied from the input exactly")
    if audit["original"] != request["original"]:
        raise Failure("format", "original must be copied from the input exactly")
    if audit["verdict"] not in AUDIT_VERDICTS:
        raise Failure("format", "verdict must be preserved, changed, or undetermined")
    checks = audit["checks"]
    if not isinstance(checks, dict) or set(checks) != set(AUDIT_CHECKS) or any(checks[k] not in AUDIT_CHECK_VALUES for k in AUDIT_CHECKS):
        raise Failure("format", "checks must map each of the nine properties to same, changed, or not_applicable")
    if not isinstance(audit["notes"], str):
        raise Failure("format", "notes must be a string")


def run_audit(args: argparse.Namespace, events: Events, runtime: Runtime) -> dict[str, Any]:
    input_file = Path(args.input)
    request = rp.load_json(input_file)
    if not isinstance(request, dict) or not all(k in request for k in ("candidate_id", "original", "proposed_wording", "basis")):
        raise DispatchError("--input must be a JSON object with candidate_id, original, proposed_wording, and basis.")
    label = args.label or str(request["candidate_id"])
    out = Path(args.out)
    if out.exists():
        raise DispatchError(f"Refusing to overwrite: {out}")

    def accept(call: dict[str, Any]) -> dict[str, Any]:
        audit = call["parsed"]
        check_audit_shape(audit, request)
        session = call["session_id"] or "unknown"
        audit = {k: audit[k] for k in ("candidate_id", "proposed_wording", "original", "verdict", "checks", "notes")}
        audit["auditor"] = {"mode": "bounded-request", "agent_id": "claude-p-" + session[:8]}
        write_new(out, encoded(audit))
        return audit

    call, audit, failure = attempt_chain(events, runtime, "auditor", label, events.generic_prompt("audit"), input_file.read_bytes(), input_file, accept)
    if failure is not None:
        return events.finish("audit", stop_message(label, failure), False, {"label": label})
    changed = [k for k in AUDIT_CHECKS if audit["checks"][k] == "changed"]
    return events.finish("audit", f"{call['label']} verdict {audit['verdict']}" + (f" (changed: {', '.join(changed)})" if changed else "") + f"; written to {out}.", True, {"label": call["label"], "audit": str(out), "verdict": audit["verdict"]})


# ---------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--events", required=True, help="Directory for the labeled call files and summary.json.")
        command.add_argument("--claude", help="claude executable (default $CLAUDE_BIN, then PATH).")
        command.add_argument("--model", help="Passed through as --model.")
        command.add_argument("--effort", help="Passed through as --effort.")
        command.add_argument("--timeout", type=float, default=900.0, help="Seconds allowed per call (default 900).")
        command.add_argument("--prompts-dir", help=f"Generic prompt files (default {PROMPTS_DIR}).")
        command.add_argument("--label", help="Label for this call (default: next free P01/BR01, or the candidate id).")

    prefix = commands.add_parser("prefix", help="Dispatch one prefix packet and run check-observation.")
    common(prefix)
    prefix.add_argument("--packet", help="An existing packet from review_packets.py packet.")
    prefix.add_argument("--index", help="Build the packet from this index ...")
    prefix.add_argument("--profile", help="... and this reader profile.")
    boundary = prefix.add_mutually_exclusive_group()
    boundary.add_argument("--through-block", metavar="B0001")
    boundary.add_argument("--stop-after-text", metavar="EXACT_QUOTE")
    boundary.add_argument("--stop-offset", type=int, metavar="N")
    prefix.add_argument("--occurrence", type=int, metavar="N")

    masked = commands.add_parser("masked", help="Drive a masked_reader run: packet, dispatch, record, until complete.")
    common(masked)
    masked.add_argument("--run", required=True, help="Run directory (created from --case and --profile when absent).")
    masked.add_argument("--case")
    masked.add_argument("--profile")
    masked.add_argument("--max-events", type=int, default=40, help="Maximum model calls in this invocation, corrections included (default 40).")

    branch = commands.add_parser("branch", help="Ask one question from a recorded display event and record the answer.")
    common(branch)
    branch.add_argument("--run", required=True)
    branch.add_argument("--from-sequence", required=True, type=int)
    branch.add_argument("--question", required=True)

    audit = commands.add_parser("audit", help="Run the independent fidelity audit for one proposed wording.")
    common(audit)
    audit.add_argument("--input", required=True, help="JSON with candidate_id, original, proposed_wording, basis.")
    audit.add_argument("--out", required=True, help="Audit file to write (shape of assets/notes/fidelity-audit.json).")
    return parser


def print_summary(summary: dict[str, Any]) -> None:
    totals = summary["totals"]
    print(f"{summary['command']}: {totals['calls']} call(s), {totals['seconds']} s, {totals['cost_usd']} USD. {summary['outcome']}")
    for row in summary["calls"]:
        cost = "-" if row["cost_usd"] is None else f"{row['cost_usd']:.5f}"
        print(f"  {row['label']:<8} {row['status']:<15} {row['seconds']:>7} s  {cost:>9} USD  session {row['session_id'] or '-'}")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("calls", "loop")}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        events = Events(Path(args.events), Path(args.prompts_dir) if args.prompts_dir else PROMPTS_DIR)
        runtime = Runtime(args.claude, args.model, args.effort, args.timeout)
        handler = {"prefix": run_prefix, "masked": run_masked, "branch": run_branch, "audit": run_audit}[args.command]
        summary = handler(args, events, runtime)
        print_summary(summary)
        return 0 if summary["ok"] else 1
    except DispatchError as exc:
        error = {"ok": False, "error": {"code": "dispatch_error", "message": str(exc)}}
    except (mr.Invalid, rp.ReviewDataError) as exc:
        error = {"ok": False, "error": {"code": "helper_error", "message": str(exc)}}
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        error = {"ok": False, "error": {"code": "input_output_error", "message": f"{type(exc).__name__}: {exc}"}}
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
