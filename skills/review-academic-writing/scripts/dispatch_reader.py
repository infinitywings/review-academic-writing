#!/usr/bin/env python3
"""Dispatch reader and auditor packets to a stateless model call and keep every byte.

Python standard library only. Three runtime adapters, selected with --runtime: `claude`
(the `claude -p` command with tools, MCP, and settings disabled; the default), `codex`
(the `codex exec` command with the user's config ignored, no saved thread, and a read-only
sandbox; a call in which the model ran any command is rejected), and `http` (one
OpenAI-compatible POST /v1/chat/completions request per call, described by a JSON file
given as --config; the API key comes from the environment variable the file names).
There is no built-in model: claude and codex need --model NAME, or --model default to use
the command's own configured default, so the person running the review chooses it.
Four subcommands:

  prefix   build or take a prefix packet, dispatch it, run review_packets check-observation.
  masked   drive a masked_reader run to completion: packet, dispatch, record, repeat. With
           --recall-every N and --recall-questions, every N windows the reader also answers
           closed questions from its notes alone (a recall event; wrong and "I don't know"
           answers are recorded for the reconcile stage). With --until-complete the run is
           resumed in this process after a correctable stop; <run>/dispatch.pid names the
           process while it works.
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
import email.utils
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

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
HTTP_ISOLATION_NOTE = "bare chat-completions request with no tools; isolation by request content, not by sandbox"
CODEX_FLAGS = ["exec", "--ignore-user-config", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "--json"]
CODEX_ISOLATION_NOTE = "codex exec --ignore-user-config --ephemeral --sandbox read-only : user config (MCP servers, plugins, skills) ignored, no thread saved, model-run commands sandboxed read-only; the shell tool stays available, so a call whose event stream shows any command execution is rejected; not an OS sandbox for the model's context"
CODEX_DEFAULT_TIMEOUT = 900.0
HTTP_CONFIG_KEYS = ("base_url", "model", "api_key_env", "temperature", "max_tokens", "json_mode", "timeout_seconds", "extra_headers", "retry_backoff_seconds", "price_per_token", "ca_file")
HTTP_ATTEMPTS = 3
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
RETRY_AFTER_CAP_SECONDS = 60.0
CLAUDE_DEFAULT_TIMEOUT = 900.0
CLI_DEFAULT_MODEL = "default"
MODEL_REQUIRED = ("choose the reader and auditor model first: ask the user which model to use, then pass --model NAME, "
                  "or --model default to let the {cli} command use its own configured default.")
BUDGET_TARGET_FRACTION = 0.85
MAX_CORRECTIONS = 2
ATTEMPT_SUFFIXES = ("", "b", "c")
LOOKUP_STALL_LIMIT = 3
# masked --until-complete: automatic restarts after a stop whose cause was correctable (format,
# budget, anchor) but exhausted its two corrections; a helper rejection never restarts.
MASKED_DEFAULT_RESTARTS = 5
BUDGET_MESSAGE =re.compile(r"carry_memory has (\d+) (words|characters); limit is (\d+)")
ANCHOR_MESSAGE = re.compile(r"(?i)quote is not (?:exact text|an exact substring)")
AUDIT_KEYS = {"candidate_id", "proposed_wording", "original", "verdict", "checks", "notes", "auditor"}
# Keys the controller fills in after the auditor answers; an auditor that echoes them is not refused.
AUDIT_CONTROLLER_KEYS = {"auditor", "audit_round", "hedges_in_original", "hedge_patterns"}
AUDIT_CHECKS = ("claim_strength", "quantifiers", "assumptions", "population", "measured_outcome", "time_horizon", "numerical_values", "uncertainty", "causal_vs_associative", "added_qualification")
AUDIT_VERDICTS = {"preserved", "changed", "undetermined"}
AUDIT_CHECK_VALUES = {"same", "changed", "not_applicable"}
# The hedge and condition phrases listed as hedges_in_original; the list lives in review_packets so
# that review_steps.py can require the same phrases in the recorded audit.
HEDGE_PATTERNS = rp.HEDGE_PATTERNS
CODE_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(.*?)\n\s*```\s*\Z", re.S)


class DispatchError(ValueError):
    """A usage or environment error; nothing was dispatched for it."""


class Failure(Exception):
    """Why an attempt was not accepted.

    kind is `budget` (the helper named the note budget; correctable), `anchor` (a quoted
    anchor is not exact text of its block; correctable), `format` (no single JSON object or
    wrong shape; correctable), or `rejected` (any other helper rejection; not re-dispatched).
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


class ClaudeRuntime:
    """The local `claude -p` command with everything disabled.

    Both runtimes return the same shape from call(): started_at, seconds, record (written
    as <label>.cli.json), raw (the result bytes, written as <label>.response.raw), error
    (a transport or exit failure that makes the attempt unusable, or None), and provenance
    (runtime-specific fields merged into <label>.provenance.json; always holds session_id
    and cost_usd).
    """

    name = "claude"
    isolation = ISOLATION_NOTE

    def __init__(self, claude: str | None, model: str | None, effort: str | None, timeout: float) -> None:
        self.claude = claude or os.environ.get("CLAUDE_BIN") or shutil.which("claude")
        if not self.claude:
            raise DispatchError("claude executable not found; pass --claude or put claude on PATH.")
        if not model:
            raise DispatchError(MODEL_REQUIRED.format(cli="claude"))
        self.model = None if model == CLI_DEFAULT_MODEL else model
        self.effort = effort
        self.timeout = timeout

    def command(self, role: str) -> list[str]:
        command = [self.claude, *RUNTIME_FLAGS, "--system-prompt", SYSTEM_PROMPTS[role], "--output-format", "json"]
        if self.model:
            command += ["--model", self.model]
        if self.effort:
            command += ["--effort", self.effort]
        return command

    def agent_id(self, session_id: str | None) -> str:
        return "claude-p-" + (session_id or "unknown")[:8]

    def call(self, role: str, prompt: str) -> dict[str, Any]:
        command = self.command(role)
        started_at = utc_now()
        clock = time.monotonic()
        try:
            run = subprocess.run(command, input=prompt.encode("utf-8"), capture_output=True, timeout=self.timeout)
            stdout, stderr, exit_code, timed_out = run.stdout, run.stderr, run.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, exit_code, timed_out = exc.stdout or b"", exc.stderr or b"", None, True
        seconds = round(time.monotonic() - clock, 1)
        envelope: dict[str, Any] | None
        try:
            envelope = json.loads(stdout.decode("utf-8"))
            if not isinstance(envelope, dict):
                envelope = None
        except ValueError:
            envelope = None
        if envelope is not None:
            record: dict[str, Any] = envelope
            result = envelope.get("result")
            raw = result.encode("utf-8") if isinstance(result, str) else encoded(result)
        else:
            record = {"error": "stdout was not a JSON envelope", "stdout": stdout.decode("utf-8", "replace")[-4000:]}
            raw = stdout
        usage = (envelope or {}).get("usage") or {}
        provenance = {
            "exit_code": exit_code,
            "command": command,
            "model_usage": sorted((envelope or {}).get("modelUsage") or {}),
            "session_id": (envelope or {}).get("session_id"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost_usd": (envelope or {}).get("total_cost_usd"),
            "permission_denials": (envelope or {}).get("permission_denials", []),
            "stderr": stderr.decode("utf-8", "replace")[-4000:],
        }
        error = None if exit_code == 0 else f"claude exited with {exit_code}" + (" (timeout)" if timed_out else "")
        return {"started_at": started_at, "seconds": seconds, "record": record, "raw": raw, "error": error, "provenance": provenance}


class CodexRuntime:
    """The local `codex exec` command with the user's configuration ignored.

    Codex has no system-prompt flag, so the role sentence is prepended to the prompt on
    stdin and recorded in provenance. The JSONL event stream gives the thread id, the
    last agent message (the result), token usage, and any command executions; a call
    with a command execution is returned as an error because the reader must not look
    anything up.
    """

    name = "codex"
    isolation = CODEX_ISOLATION_NOTE

    def __init__(self, codex: str | None, model: str | None, effort: str | None, timeout: float) -> None:
        self.codex = codex or os.environ.get("CODEX_BIN") or shutil.which("codex")
        if not self.codex:
            raise DispatchError("codex executable not found; pass --codex or put codex on PATH.")
        if not model:
            raise DispatchError(MODEL_REQUIRED.format(cli="codex"))
        self.model = None if model == CLI_DEFAULT_MODEL else model
        self.effort = effort
        self.timeout = timeout

    def command(self) -> list[str]:
        command = [self.codex, *CODEX_FLAGS]
        if self.model:
            command += ["--model", self.model]
        if self.effort:
            command += ["-c", "model_reasoning_effort=" + json.dumps(self.effort)]
        return command + ["-"]

    def agent_id(self, session_id: str | None) -> str:
        return "codex-exec-" + (session_id or "unknown")[:8]

    @staticmethod
    def events(stdout: bytes) -> tuple[list[dict[str, Any]], list[str]]:
        events: list[dict[str, Any]] = []
        unparsed: list[str] = []
        for line in stdout.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                unparsed.append(line[-400:])
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                unparsed.append(line[-400:])
        return events, unparsed

    def call(self, role: str, prompt: str) -> dict[str, Any]:
        command = self.command()
        full_prompt = SYSTEM_PROMPTS[role] + "\n\n" + prompt
        started_at = utc_now()
        clock = time.monotonic()
        try:
            run = subprocess.run(command, input=full_prompt.encode("utf-8"), capture_output=True, timeout=self.timeout)
            stdout, stderr, exit_code, timed_out = run.stdout, run.stderr, run.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, exit_code, timed_out = exc.stdout or b"", exc.stderr or b"", None, True
        seconds = round(time.monotonic() - clock, 1)
        events, unparsed = self.events(stdout)
        thread_id = None
        messages: list[str] = []
        commands: list[dict[str, Any]] = []
        errors: list[str] = []
        usage: dict[str, Any] = {}
        for event in events:
            kind = event.get("type")
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if kind == "thread.started" and isinstance(event.get("thread_id"), str):
                thread_id = event["thread_id"]
            elif kind == "item.completed" and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                messages.append(item["text"])
            elif kind == "item.completed" and item.get("type") == "command_execution":
                commands.append({k: item.get(k) for k in ("command", "exit_code", "status")})
            elif kind == "item.completed" and item.get("type") == "error":
                errors.append(str(item.get("message")))
            elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
            elif kind == "turn.failed" or kind == "error":
                errors.append(json.dumps(event, ensure_ascii=False)[-400:])
        record: dict[str, Any] = {"events": events, "unparsed": unparsed, "stderr_tail": stderr.decode("utf-8", "replace")[-4000:]}
        raw = messages[-1].encode("utf-8") if messages else stdout
        error = None
        if exit_code != 0:
            error = f"codex exited with {exit_code}" + (" (timeout)" if timed_out else "")
        elif commands:
            error = f"codex ran {len(commands)} command(s); the call is rejected because the reader must not look anything up"
        elif not messages:
            error = "codex produced no agent_message event"
        provenance = {
            "exit_code": exit_code,
            "command": command,
            "system_prompt": SYSTEM_PROMPTS[role],
            "system_prompt_delivery": "prepended to the stdin prompt, separated by a blank line",
            "thread_id": thread_id,
            "session_id": thread_id,
            "model": self.model,
            "effort": self.effort,
            "agent_messages": len(messages),
            "command_executions": commands,
            "event_errors": errors,
            "usage": usage or None,
            "input_tokens": usage.get("input_tokens"),
            "cached_input_tokens": usage.get("cached_input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
            "cost_usd": None,
            "stderr": stderr.decode("utf-8", "replace")[-4000:],
        }
        return {"started_at": started_at, "seconds": seconds, "record": record, "raw": raw, "error": error, "provenance": provenance, "rejected": bool(commands) and exit_code == 0}


def parse_retry_after(value: str | None) -> float | None:
    """Seconds named by a Retry-After header (delay-seconds or an HTTP date), or None."""
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds())


def message_content(envelope: dict[str, Any]) -> tuple[str | None, Any]:
    """choices[0].message.content as text (string or text parts), plus finish_reason."""
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, None
    finish_reason = choices[0].get("finish_reason")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None, finish_reason
    content = message.get("content")
    if isinstance(content, str):
        return content, finish_reason
    if isinstance(content, list):
        parts = [p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)]
        if parts:
            return "".join(parts), finish_reason
    return None, finish_reason


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: following one would send the Authorization header elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None


def cleartext_key_warning(url: str, api_key: str | None) -> str | None:
    """A warning when an API key would travel over plain http to a host other than loopback."""
    parts = urllib.parse.urlsplit(url)
    if api_key and parts.scheme == "http" and (parts.hostname or "") not in LOOPBACK_HOSTS:
        return f"API key from the environment is sent in cleartext to {parts.hostname}: base_url uses http://; use https:// or keep the endpoint on this machine."
    return None


class HttpRuntime:
    """One OpenAI-compatible chat-completions request per call, with no tools.

    The config file names the endpoint, the model, the request parameters, and the
    environment variable that holds the API key. The key itself never comes from the file
    or the command line and is never written to any output file. Redirects are refused so
    the key cannot be forwarded to another origin.
    """

    name = "http"
    isolation = HTTP_ISOLATION_NOTE

    def __init__(self, config_path: Path, model: str | None, timeout: float | None) -> None:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise DispatchError(f"Cannot read --config {config_path}: {exc}") from exc
        except ValueError as exc:
            raise DispatchError(f"--config {config_path} is not valid JSON: {exc}") from exc
        if not isinstance(config, dict):
            raise DispatchError(f"--config {config_path} must hold one JSON object.")
        if "api_key" in config:
            raise DispatchError("The config file must not hold an API key; name an environment variable in api_key_env and export the key there.")
        unknown = sorted(set(config) - set(HTTP_CONFIG_KEYS))
        if unknown:
            raise DispatchError(f"Unknown key(s) in --config: {', '.join(unknown)}. Allowed: {', '.join(HTTP_CONFIG_KEYS)}.")
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise DispatchError("--config needs base_url, an http:// or https:// URL such as http://host:8000/v1.")
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"
        self.model = model or config.get("model")
        if not isinstance(self.model, str) or not self.model:
            raise DispatchError("--config needs model (a non-empty string), or pass --model.")
        self.api_key_env = config.get("api_key_env")
        self.api_key: str | None = None
        if self.api_key_env is not None:
            if not isinstance(self.api_key_env, str) or not self.api_key_env:
                raise DispatchError("api_key_env must be the name of an environment variable.")
            self.api_key = os.environ.get(self.api_key_env)
            if not self.api_key:
                raise DispatchError(f"Environment variable {self.api_key_env} (named by api_key_env in {config_path}) is not set; export the API key there. The key is never read from the config file or the command line.")
        self.temperature = config.get("temperature", 0)
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)):
            raise DispatchError("temperature must be a number.")
        self.max_tokens = config.get("max_tokens")
        if self.max_tokens is not None and (isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens <= 0):
            raise DispatchError("max_tokens must be a positive integer or absent.")
        self.json_mode = config.get("json_mode", False)
        if not isinstance(self.json_mode, bool):
            raise DispatchError("json_mode must be true or false.")
        configured_timeout = config.get("timeout_seconds", 300)
        if isinstance(configured_timeout, bool) or not isinstance(configured_timeout, (int, float)) or configured_timeout <= 0:
            raise DispatchError("timeout_seconds must be a positive number.")
        self.timeout = float(timeout if timeout is not None else configured_timeout)
        self.extra_headers = config.get("extra_headers") or {}
        if not isinstance(self.extra_headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.extra_headers.items()):
            raise DispatchError("extra_headers must map header names to strings.")
        if any(k.lower() == "authorization" for k in self.extra_headers):
            raise DispatchError("extra_headers must not set Authorization; name an environment variable in api_key_env instead.")
        self.backoff = config.get("retry_backoff_seconds", 1.0)
        if isinstance(self.backoff, bool) or not isinstance(self.backoff, (int, float)) or self.backoff < 0:
            raise DispatchError("retry_backoff_seconds must be a non-negative number.")
        self.prices = config.get("price_per_token")
        if self.prices is not None and (not isinstance(self.prices, dict) or set(self.prices) != {"input", "output"} or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in self.prices.values())):
            raise DispatchError('price_per_token must be {"input": USD per input token, "output": USD per output token} or absent.')
        ca_file = config.get("ca_file")
        if ca_file is not None and not isinstance(ca_file, str):
            raise DispatchError("ca_file must be a path string or absent.")
        try:
            self.ssl_context = ssl.create_default_context(cafile=ca_file)
        except (OSError, ssl.SSLError) as exc:
            raise DispatchError(f"Cannot load ca_file {ca_file}: {exc}") from exc
        self.opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=self.ssl_context))
        self.transport_warning = cleartext_key_warning(self.url, self.api_key)
        if self.transport_warning:
            print(f"warning: {self.transport_warning}", file=sys.stderr)

    def agent_id(self, session_id: str | None) -> str:
        short = session_id or "unknown"
        if short.startswith("chatcmpl-"):
            short = short[len("chatcmpl-"):]
        return "http-" + short[:12]

    def request_body(self, role: str, prompt: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPTS[role]}, {"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def post(self, data: bytes) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(self.url, data=data, headers=self.headers(), method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()

    def call(self, role: str, prompt: str) -> dict[str, Any]:
        body = self.request_body(role, prompt)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        started_at = utc_now()
        clock = time.monotonic()
        attempts: list[dict[str, Any]] = []
        status: int | None = None
        headers: dict[str, str] = {}
        payload = b""
        for number in range(1, HTTP_ATTEMPTS + 1):
            attempt: dict[str, Any] = {"attempt": number, "started_at": utc_now(), "seconds": None, "status": None, "error": None, "retry_after": None, "waited_seconds": None}
            tick = time.monotonic()
            try:
                status, headers, payload = self.post(data)
            except (OSError, http.client.HTTPException) as exc:
                status, headers, payload = None, {}, b""
                attempt["error"] = f"{type(exc).__name__}: {exc}"
            attempt["seconds"] = round(time.monotonic() - tick, 1)
            attempt["status"] = status
            attempts.append(attempt)
            retryable = status is None or status == 429 or status >= 500
            if not retryable or number == HTTP_ATTEMPTS:
                break
            retry_after = parse_retry_after(headers.get("retry-after"))
            wait = self.backoff * 2 ** (number - 1) if retry_after is None else min(retry_after, RETRY_AFTER_CAP_SECONDS)
            attempt["retry_after"] = retry_after
            attempt["waited_seconds"] = wait
            time.sleep(wait)
        seconds = round(time.monotonic() - clock, 1)
        succeeded = status is not None and 200 <= status < 300
        error: str | None = None
        if not succeeded:
            error = (f"HTTP {status}" if status is not None else attempts[-1]["error"]) + (f" after {len(attempts)} attempts" if len(attempts) > 1 else "")
            if status is not None and 300 <= status < 400:
                error += " (redirect refused: the request is never resent to another location)"
        envelope: dict[str, Any] | None = None
        if succeeded:
            try:
                envelope = json.loads(payload.decode("utf-8"))
                if not isinstance(envelope, dict):
                    envelope = None
            except ValueError:
                envelope = None
            if envelope is None:
                error = "response body was not a chat-completions JSON object"
        content, finish_reason = message_content(envelope) if envelope is not None else (None, None)
        if envelope is not None:
            record: dict[str, Any] = envelope
            if content is None:
                error = "response has no choices[0].message.content"
                raw = encoded(envelope)
            else:
                raw = content.encode("utf-8")
        else:
            record = {"error": error, "status": status, "body": payload.decode("utf-8", "replace")[-4000:], "attempts": attempts}
            raw = payload
        usage = (envelope or {}).get("usage")
        usage = usage if isinstance(usage, dict) else None
        input_tokens = (usage or {}).get("prompt_tokens")
        output_tokens = (usage or {}).get("completion_tokens")
        cost_usd = None
        if self.prices is not None and isinstance(input_tokens, int) and isinstance(output_tokens, int):
            cost_usd = round(input_tokens * self.prices["input"] + output_tokens * self.prices["output"], 6)
        response_id = (envelope or {}).get("id")
        provenance = {
            "base_url": self.base_url,
            "url": self.url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "request": {"temperature": self.temperature, "max_tokens": self.max_tokens, "response_format": body.get("response_format"), "timeout_seconds": self.timeout},
            "transport_warning": self.transport_warning,
            "http_status": status,
            "attempt_count": len(attempts),
            "attempts": attempts,
            "response_id": response_id if isinstance(response_id, str) else None,
            "response_model": (envelope or {}).get("model"),
            "finish_reason": finish_reason,
            "session_id": response_id if isinstance(response_id, str) else None,
            "usage": usage,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "error": error,
        }
        return {"started_at": started_at, "seconds": seconds, "record": record, "raw": raw, "error": error, "provenance": provenance}


Runtime = ClaudeRuntime | CodexRuntime | HttpRuntime


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
        write_new(self.path(label, "cli.json"), encoded(run["record"]))
        raw: bytes = run["raw"]
        write_new(self.path(label, "response.raw"), raw)
        parsed, parse_error, fence_stripped = parse_result(raw.decode("utf-8", "replace"))
        if run["error"]:
            parsed, parse_error = None, run["error"]
        response_path = self.path(label, "response.raw")
        if parsed is not None:
            write_new(self.path(label, "response.json"), encoded(parsed))
            if fence_stripped:
                response_path = self.path(label, "response.json")
        provenance = {
            "label": label,
            "corrects": corrects,
            "runtime": runtime.name,
            "started_at": run["started_at"],
            "seconds": run["seconds"],
            **run["provenance"],
            "packet_file": str(packet_file),
            "packet_sha256": sha256_bytes(packet_file.read_bytes()) if packet_file.is_file() else None,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "parse_error": parse_error,
            "fence_stripped": fence_stripped,
            "isolation": runtime.isolation,
        }
        write_new(self.path(label, "provenance.json"), encoded(provenance))
        call = {"label": label, "status": "pending", "seconds": run["seconds"], "cost_usd": provenance["cost_usd"], "session_id": provenance["session_id"], "exit_code": provenance.get("exit_code"), "parsed": parsed, "parse_error": parse_error, "response_path": response_path, "provenance": provenance, "rejected_by_runtime": bool(run.get("rejected"))}
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


def anchor_note(errors: list[str]) -> str:
    return (
        "Anchor note from the controller: a previous attempt at this same input quoted text that is not an exact copy of the block it names "
        f"({'; '.join(errors)}). Every anchor quote must be copied character for character from the named block as shown in the packet, without "
        "paraphrase, ellipsis, or changed punctuation; quote a shorter fragment you can copy exactly, and keep every other part of the response as before."
    )


def classify_helper_error(exc: BaseException) -> Failure:
    """budget and anchor failures are correctable with the same packet; anything else is rejected."""
    message = str(exc)
    if BUDGET_MESSAGE.search(message):
        return Failure("budget", message)
    if ANCHOR_MESSAGE.search(message):
        return Failure("anchor", message)
    return Failure("rejected", message)


def attempt_chain(events: Events, runtime: Runtime, role: str, base: str, generic: str, payload: bytes, packet_file: Path, accept: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any] | None, Any, Failure | None]:
    """Dispatch, validate, and correct up to MAX_CORRECTIONS times. Returns (call, accepted value, failure)."""
    budget_sizes: list[int] = []
    format_errors: list[str] = []
    anchor_errors: list[str] = []
    note: str | None = None
    for number, suffix in enumerate(ATTEMPT_SUFFIXES):
        label = base + suffix
        call = events.dispatch(runtime, role, label, generic, payload, packet_file, note, base if suffix else None)
        if call["parsed"] is None:
            failure = Failure("rejected" if call.get("rejected_by_runtime") else "format", call["parse_error"] or "no response")
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
        elif failure.kind == "anchor":
            anchor_errors.append(str(failure))
            note = anchor_note(anchor_errors)
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


def load_recall_questions(path: str | None, plan_id: str | None, block_ids: set[str]) -> list[dict[str, Any]]:
    """Recall questions from a JSON array, or from the checkpoint_plan of a checks notes file.

    With a checks file, the masked-sequential plan entries that carry recall_questions are
    used (all of them, or the one named by plan_id). The answers travel with the controller
    only; masked_reader keeps them out of every packet.
    """
    if path is None:
        return []
    value = rp.load_json(path)
    if isinstance(value, dict) and isinstance(value.get("checkpoint_plan"), list):
        entries = [e for e in value["checkpoint_plan"] if isinstance(e, dict) and e.get("recall_questions") is not None]
        if plan_id is not None:
            entries = [e for e in entries if e.get("id") == plan_id]
            if not entries:
                raise DispatchError(f"--recall-plan-id {plan_id}: no checkpoint_plan entry with that id carries recall_questions.")
        if not entries:
            raise DispatchError("--recall-questions names a checks file whose checkpoint_plan has no entry with recall_questions.")
        questions: list[dict[str, Any]] = []
        for entry in entries:
            questions.extend(entry["recall_questions"] if isinstance(entry["recall_questions"], list) else [entry["recall_questions"]])
    elif isinstance(value, list):
        if plan_id is not None:
            raise DispatchError("--recall-plan-id applies only when --recall-questions is a checks notes file.")
        questions = value
    else:
        raise DispatchError("--recall-questions must be a JSON array of questions or a checks notes file with a checkpoint_plan.")
    try:
        mr.validate_recall_questions(questions, block_ids)
    except mr.Invalid as exc:
        raise DispatchError(f"--recall-questions: {exc}") from exc
    return questions


def recall_due(run: Path, every: int, questions: list[dict[str, Any]], case: dict[str, Any], state: dict[str, Any], manifest: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """The questions to ask now: not yet asked, their after_block seen, and `every` windows recorded since the last recall (or the run is complete)."""
    if not every or not questions or not state["events"] or state["pending_lookup"] is not None:
        return []
    recalls = mr.load_recalls(run, manifest, profile, state)
    asked = {q["id"] for r in recalls for q in r["questions"]}
    last_after = max((r["after_sequence"] for r in recalls), default=0)
    windows_since = sum(1 for e in state["events"] if e["sequence"] > last_after and e["packet"]["mode"] == "window")
    complete = state["cursor"] >= len(case["windows"])
    if windows_since < every and not complete:
        return []
    return [q for q in questions if q["id"] not in asked and q["after_block"] in state["seen"]]


PID_FILE = "dispatch.pid"


def claim_run(run: Path, events_dir: Path) -> Path:
    """Write <run>/dispatch.pid for this process; refuse while another live process holds it."""
    pid_file = run / PID_FILE
    if pid_file.exists():
        try:
            holder = json.loads(pid_file.read_text(encoding="utf-8"))
            pid = int(holder["pid"])
        except (ValueError, KeyError, TypeError):
            pid = None
        if pid is not None and pid != os.getpid() and process_alive(pid):
            raise DispatchError(f"Another dispatch is driving this run (pid {pid}, see {pid_file}); wait for it to finish or remove the pid file if that process is gone.")
        pid_file.unlink()
    pid_file.write_text(json.dumps({"pid": os.getpid(), "started_at": utc_now(), "events_dir": str(events_dir)}) + "\n", encoding="utf-8")
    return pid_file


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def masked_segment(args: argparse.Namespace, events: Events, runtime: Runtime, run: Path, generic: str, questions: list[dict[str, Any]], loop: list[dict[str, Any]]) -> tuple[str, bool, Failure | None]:
    """Drive the run until it completes or stops; returns (outcome, ok, the failure that stopped it or None)."""
    stall = 0
    every = getattr(args, "recall_every", 0) or 0
    while True:
        _run, manifest, case, profile, state, seq, _previous = mr.load_run(run)
        due = recall_due(run, every, questions, case, state, manifest, profile)
        if due:
            if len(events.calls) >= args.max_events:
                return f"Stopped: --max-events {args.max_events} reached with {len(state['events'])} recorded event(s) and a recall due.", False, None
            rseq = len(mr.load_recalls(run, manifest, profile, state)) + 1
            label = f"R{rseq:02d}"
            if events.path(label, "packet.json").exists():
                resume = 1
                while events.path(f"{label}r{resume}", "packet.json").exists():
                    resume += 1
                label = f"{label}r{resume}"
            packet_file = events.path(label, "packet.json")
            issued = mr.recall(run, due, packet_file)

            def accept_recall(call: dict[str, Any], packet_file: Path = packet_file) -> dict[str, Any]:
                try:
                    return mr.recall_record(run, packet_file, call["response_path"], agent_id=call["session_id"])
                except (mr.Invalid, KeyError, TypeError, ValueError) as exc:
                    raise Failure("format", str(exc)) from exc

            call, record, failure = attempt_chain(events, runtime, "reader", label, generic, packet_file.read_bytes(), packet_file, accept_recall)
            entry = {"sequence": issued["after_sequence"], "mode": "recall", "recall_sequence": issued["recall_sequence"], "label": call["label"], "status": call["status"], "question_ids": [q["id"] for q in due]}
            if failure is not None:
                entry["error"] = str(failure)
                loop.append(entry)
                return stop_message(label, failure), False, failure
            entry["record"] = {k: v for k, v in record.items() if k != "ok"}
            loop.append(entry)
            continue
        if state["cursor"] >= len(case["windows"]):
            return f"Run complete after {len(state['events'])} recorded event(s).", True, None
        if len(events.calls) >= args.max_events:
            return f"Stopped: --max-events {args.max_events} reached with {len(state['events'])} recorded event(s).", False, None
        label = f"E{seq:02d}"
        if events.path(label, "packet.json").exists():
            # Resuming after a stop at this sequence: the earlier attempt's files stay; this attempt gets a resume label.
            resume = 1
            while events.path(f"{label}r{resume}", "packet.json").exists():
                resume += 1
            label = f"{label}r{resume}"
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
            return stop_message(label, failure), False, failure
        entry["record"] = {k: v for k, v in record.items() if k != "ok"}
        if packet["mode"] == "lookup":
            new_ids = sorted({m["id"] for m in packet["materials"]} - seen_before)
            entry["new_source_ids"] = new_ids
            stall = 0 if new_ids else stall + 1
        elif record["next_mode"] != "lookup":
            stall = 0
        loop.append(entry)
        if stall >= LOOKUP_STALL_LIMIT:
            return f"Stopped: {LOOKUP_STALL_LIMIT} consecutive lookups brought no new source ids (execution limit, not a reader defect).", False, None


def run_masked(args: argparse.Namespace, events: Events, runtime: Runtime) -> dict[str, Any]:
    run = Path(args.run).resolve()
    if not run.exists():
        if not args.case or not args.profile:
            raise DispatchError("masked needs an existing --run, or --case and --profile to start one at --run.")
        mr.start(args.case, args.profile, run)
    every = getattr(args, "recall_every", 0) or 0
    if not isinstance(every, int) or every < 0:
        raise DispatchError("--recall-every must be 0 (off) or a positive number of windows.")
    questions_file = getattr(args, "recall_questions", None)
    if every and not questions_file:
        raise DispatchError("--recall-every needs --recall-questions (a JSON array, or a checks notes file whose checkpoint_plan carries recall_questions).")
    if questions_file and not every:
        raise DispatchError("--recall-questions needs --recall-every N (how many windows between recall events).")
    case = mr.decode((run / "case.snapshot.json").read_bytes())
    questions = load_recall_questions(questions_file, getattr(args, "recall_plan_id", None), {b["id"] for b in case["blocks"]})
    generic = events.generic_prompt("masked")
    loop: list[dict[str, Any]] = []
    until_complete = bool(getattr(args, "until_complete", False))
    max_restarts = getattr(args, "max_restarts", None)
    max_restarts = MASKED_DEFAULT_RESTARTS if max_restarts is None else max_restarts
    if not isinstance(max_restarts, int) or max_restarts < 0:
        raise DispatchError("--max-restarts must be a non-negative integer.")
    restarts = 0
    pid_file = claim_run(run, events.dir)
    try:
        while True:
            outcome, ok, failure = masked_segment(args, events, runtime, run, generic, questions, loop)
            if ok or not until_complete or failure is None or failure.kind == "rejected" or restarts >= max_restarts:
                break
            restarts += 1
            loop.append({"mode": "restart", "restart": restarts, "after": outcome})
    finally:
        if pid_file.exists():
            pid_file.unlink()
    if until_complete and not ok and restarts >= max_restarts and failure is not None and failure.kind != "rejected":
        outcome += f" Stopped after {restarts} automatic restart(s) (--max-restarts {max_restarts})."
    recalls = [{k: e[k] for k in ("recall_sequence", "sequence", "label", "status", "question_ids")} | {"wrong": e["record"]["wrong"], "idk": e["record"]["idk"]} for e in loop if e.get("mode") == "recall" and "record" in e]
    return events.finish("masked", outcome, ok, {"run": str(run), "loop": loop, "restarts": restarts, "recalls": recalls})


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


def check_hedges_shape(hedges: Any, checks: dict[str, Any], occurrences: list[dict[str, Any]], proposed_wording: Any, extra_patterns: list[str] = ()) -> None:
    """The same rules review_steps.py applies when the audit is recorded (review_packets.check_hedge_report), so a bad report is corrected now."""
    try:
        rp.check_hedge_report(occurrences, hedges, checks, proposed_wording if isinstance(proposed_wording, str) else "", extra_patterns)
    except rp.ReviewDataError as exc:
        raise Failure("format", str(exc)) from exc


def check_audit_shape(audit: Any, request: dict[str, Any], extra_patterns: list[str] = ()) -> None:
    if not isinstance(audit, dict) or set(audit) - AUDIT_CONTROLLER_KEYS != AUDIT_KEYS - {"auditor"}:
        raise Failure("format", "audit must have exactly candidate_id, proposed_wording, original, verdict, checks, notes, auditor")
    if audit["candidate_id"] != request["candidate_id"]:
        raise Failure("format", "candidate_id must be copied from the input")
    if audit["proposed_wording"] != request["proposed_wording"]:
        raise Failure("format", "proposed_wording must be copied from the input exactly")
    if audit["original"] != request["original"]:
        raise Failure("format", "original must be copied from the input exactly")
    if not isinstance(audit["verdict"], str) or audit["verdict"] not in AUDIT_VERDICTS:
        raise Failure("format", "verdict must be preserved, changed, or undetermined")
    checks = audit["checks"]
    if not isinstance(checks, dict) or set(checks) != set(AUDIT_CHECKS) | {"hedges"} or any(not isinstance(checks[k], str) or checks[k] not in AUDIT_CHECK_VALUES for k in AUDIT_CHECKS):
        raise Failure("format", "checks must map each of the ten properties to same, changed, or not_applicable and carry a hedges array")
    check_hedges_shape(checks["hedges"], checks, request["hedges_in_original"], request.get("proposed_wording"), extra_patterns)
    if not isinstance(audit["notes"], str):
        raise Failure("format", "notes must be a string")


def audit_request_with_hedges(request: dict[str, Any], extra_patterns: list[str]) -> tuple[dict[str, Any], list[str]]:
    """The audit input as sent, with hedges_in_original listing every hedge occurrence in the original quotes, and the extra patterns used.

    Phrases the caller lists in hedges_in_original join the --hedge-list patterns; each must
    occur in the original. The returned patterns are recorded with the audit so that
    review_steps.py can find the same occurrences again.
    """
    quotes = [a.get("quote") for a in request["original"] if isinstance(a, dict) and isinstance(a.get("quote"), str)] if isinstance(request["original"], list) else []
    given = request.get("hedges_in_original")
    if given is not None and (not isinstance(given, list) or any(not isinstance(t, str) or not t.strip() for t in given)):
        raise DispatchError("hedges_in_original in --input must be an array of nonempty strings when present.")
    patterns = list(extra_patterns)
    for term in given or []:
        pattern = rp.term_pattern(term)
        if not any(re.search(pattern, q, re.IGNORECASE) for q in quotes):
            raise DispatchError(f"hedges_in_original in --input lists {term!r}, which does not occur in the original quotes.")
        if pattern not in patterns:
            patterns.append(pattern)
    return {**request, "hedges_in_original": rp.find_hedge_occurrences(quotes, patterns)}, patterns


def load_hedge_list(path: str | None) -> list[str]:
    if path is None:
        return []
    patterns = rp.load_json(path)
    if not isinstance(patterns, list) or any(not isinstance(p, str) or not p.strip() for p in patterns):
        raise DispatchError("--hedge-list must be a JSON array of regular-expression strings.")
    for pattern in patterns:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise DispatchError(f"--hedge-list pattern {pattern!r} does not compile: {exc}") from exc
        if compiled.match("") is not None:
            raise DispatchError(f"--hedge-list pattern {pattern!r} matches the empty string; every pattern must require at least one character.")
    try:
        re.compile("|".join((*rp.HEDGE_PATTERNS, *patterns)), re.IGNORECASE)
    except re.error as exc:
        raise DispatchError(f"--hedge-list patterns do not combine with the built-in list (no inline flags such as (?i) are allowed): {exc}") from exc
    return patterns


def run_audit(args: argparse.Namespace, events: Events, runtime: Runtime) -> dict[str, Any]:
    input_file = Path(args.input)
    request = rp.load_json(input_file)
    if not isinstance(request, dict) or not all(k in request for k in ("candidate_id", "original", "proposed_wording", "basis")):
        raise DispatchError("--input must be a JSON object with candidate_id, original, proposed_wording, and basis.")
    if not isinstance(args.round, int) or args.round < 1:
        raise DispatchError("--round must be a positive integer (1 for the first wording of a candidate).")
    request, extra_patterns = audit_request_with_hedges(request, load_hedge_list(args.hedge_list))
    label = args.label or str(request["candidate_id"])
    out = Path(args.out)
    if out.exists():
        raise DispatchError(f"Refusing to overwrite: {out}")

    def accept(call: dict[str, Any]) -> dict[str, Any]:
        audit = call["parsed"]
        check_audit_shape(audit, request, extra_patterns)
        audit = {k: audit[k] for k in ("candidate_id", "proposed_wording", "original", "verdict", "checks", "notes")}
        audit["audit_round"] = args.round
        audit["hedges_in_original"] = request["hedges_in_original"]
        audit["hedge_patterns"] = extra_patterns
        audit["auditor"] = {"mode": "bounded-request", "agent_id": runtime.agent_id(call["session_id"])}
        write_new(out, encoded(audit))
        return audit

    call, audit, failure = attempt_chain(events, runtime, "auditor", label, events.generic_prompt("audit"), encoded(request), input_file, accept)
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
        command.add_argument("--runtime", choices=("claude", "codex", "http"), default="claude", help="claude: the local claude -p command (default). codex: the local codex exec command. http: an OpenAI-compatible chat-completions endpoint described by --config.")
        command.add_argument("--config", help=f"JSON file for --runtime http with {', '.join(HTTP_CONFIG_KEYS)}; the API key is read from the environment variable named by api_key_env.")
        command.add_argument("--claude", help="claude executable (default $CLAUDE_BIN, then PATH).")
        command.add_argument("--codex", help="codex executable (default $CODEX_BIN, then PATH).")
        command.add_argument("--model", help=f"Required for claude and codex: the model name passed through as --model, or {CLI_DEFAULT_MODEL!r} to pass none and use the CLI's configured default. There is no built-in model; ask the user. http: overrides the config's model.")
        command.add_argument("--effort", help="claude: passed through as --effort. codex: sets model_reasoning_effort. Not for http.")
        command.add_argument("--timeout", type=float, help="Seconds allowed per call (default 900 for claude and codex; the config's timeout_seconds, default 300, for http).")
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
    masked.add_argument("--until-complete", action="store_true", help="After a stop caused by a format, budget, or anchor failure that used up its corrections, resume the same run in this process (resume labels E06r1, ...) until it reports Run complete, --max-events, or --max-restarts. A helper rejection still stops.")
    masked.add_argument("--max-restarts", type=int, default=MASKED_DEFAULT_RESTARTS, help=f"Automatic restarts allowed with --until-complete (default {MASKED_DEFAULT_RESTARTS}).")
    masked.add_argument("--recall-every", type=int, default=0, help="Every N recorded windows, send the reader a recall packet: its carry_memory and the due questions, no manuscript text (default 0, off). Questions become due once their after_block has been seen; a final recall covers the rest when the run completes.")
    masked.add_argument("--recall-questions", help="JSON array of {id, after_block, question, options, answer} (options include one 'I don't know'), or a checks notes file whose checkpoint_plan entries carry recall_questions. Answers never enter a packet.")
    masked.add_argument("--recall-plan-id", help="With a checks notes file: use only the checkpoint_plan entry with this id.")

    branch = commands.add_parser("branch", help="Ask one question from a recorded display event and record the answer.")
    common(branch)
    branch.add_argument("--run", required=True)
    branch.add_argument("--from-sequence", required=True, type=int)
    branch.add_argument("--question", required=True)

    audit = commands.add_parser("audit", help="Run the independent fidelity audit for one proposed wording.")
    common(audit)
    audit.add_argument("--input", required=True, help="JSON with candidate_id, original, proposed_wording, basis.")
    audit.add_argument("--out", required=True, help="Audit file to write (shape of assets/notes/fidelity-audit.json).")
    audit.add_argument("--round", type=int, default=1, help="Which wording of this candidate is audited: 1 for the first, 2 for a revision after a failed audit, and so on. Every round is audited against the manuscript text, never against the previous wording (default 1).")
    audit.add_argument("--hedge-list", help="JSON array of regular expressions added to the built-in hedge patterns when listing hedges_in_original.")
    return parser


def print_summary(summary: dict[str, Any]) -> None:
    totals = summary["totals"]
    print(f"{summary['command']}: {totals['calls']} call(s), {totals['seconds']} s, {totals['cost_usd']} USD. {summary['outcome']}")
    for row in summary["calls"]:
        cost = "-" if row["cost_usd"] is None else f"{row['cost_usd']:.5f}"
        print(f"  {row['label']:<8} {row['status']:<15} {row['seconds']:>7} s  {cost:>9} USD  session {row['session_id'] or '-'}")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("calls", "loop")}, ensure_ascii=False))


def build_runtime(args: argparse.Namespace) -> Runtime:
    if args.runtime == "http":
        if not args.config:
            raise DispatchError("--runtime http needs --config PATH (a JSON file with base_url, model, and api_key_env).")
        if args.effort:
            raise DispatchError("--effort applies to the claude and codex runtimes only.")
        return HttpRuntime(Path(args.config), None if args.model == CLI_DEFAULT_MODEL else args.model, args.timeout)
    if args.config:
        raise DispatchError("--config applies to --runtime http only.")
    if args.runtime == "codex":
        return CodexRuntime(args.codex, args.model, args.effort, CODEX_DEFAULT_TIMEOUT if args.timeout is None else args.timeout)
    return ClaudeRuntime(args.claude, args.model, args.effort, CLAUDE_DEFAULT_TIMEOUT if args.timeout is None else args.timeout)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        events = Events(Path(args.events), Path(args.prompts_dir) if args.prompts_dir else PROMPTS_DIR)
        runtime = build_runtime(args)
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
