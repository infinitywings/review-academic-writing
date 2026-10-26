#!/usr/bin/env python3
"""Editor-side client for an OpenAI-compatible chat-completions endpoint.

Python standard library only. The config file is the one `scripts/dispatch_reader.py
--runtime http` reads (base_url, model, api_key_env, temperature, max_tokens, json_mode,
timeout_seconds, and the optional keys it allows); the transport, the retries on 429/5xx
and connection failures, and the refusal to follow redirects are HttpRuntime's. This
module adds the editor system prompt and writes one labeled file set per call under a
calls directory: <label>.prompt.txt, <label>.cli.json, <label>.response.raw,
<label>.response.json (when the content parses as one JSON object), and
<label>.provenance.json. The API key is read from the environment variable the config
names and is never written to any file.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import dispatch_reader as dr  # noqa: E402

EDITOR_SYSTEM_PROMPT = ("You are the editor described in the user's message, working through one recorded step of an academic writing review. "
                        "Return only the JSON the message asks for, with no code fence and no commentary.")
EDITOR_ISOLATION_NOTE = "bare chat-completions request with no tools; the editor sees the manuscript text and its own earlier records by request content, not by sandbox"


class EditorRuntime(dr.HttpRuntime):
    """HttpRuntime with the editor system prompt; everything else is inherited."""

    name = "http-editor"
    isolation = EDITOR_ISOLATION_NOTE

    def request_body(self, role: str, prompt: str) -> dict[str, Any]:
        body = super().request_body("reader", prompt)
        body["messages"][0]["content"] = EDITOR_SYSTEM_PROMPT
        return body


class Call:
    """One completed editor call: what was sent, what came back, and whether it parsed."""

    def __init__(self, label: str, parsed: Any, parse_error: str | None, provenance: dict[str, Any], paths: dict[str, Path]) -> None:
        self.label = label
        self.parsed = parsed
        self.parse_error = parse_error
        self.provenance = provenance
        self.paths = paths
        self.status = "pending"

    @property
    def ok(self) -> bool:
        return self.parsed is not None

    def row(self) -> dict[str, Any]:
        return {"label": self.label, "status": self.status, "seconds": self.provenance["seconds"],
                "input_tokens": self.provenance.get("input_tokens"), "output_tokens": self.provenance.get("output_tokens"),
                "cost_usd": self.provenance.get("cost_usd"), "response_id": self.provenance.get("response_id"), "parse_error": self.parse_error}


class Client:
    """Sends editor prompts and keeps every byte of every call."""

    def __init__(self, config_path: Path, calls_dir: Path, model: str | None = None, timeout: float | None = None, runtime: EditorRuntime | None = None) -> None:
        self.config_path = Path(config_path)
        self.runtime = runtime or EditorRuntime(self.config_path, model, timeout)
        self.dir = Path(calls_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[Call] = []

    def path(self, label: str, suffix: str) -> Path:
        return self.dir / f"{label}.{suffix}"

    def call(self, label: str, prompt: str) -> Call:
        paths = {"prompt": self.path(label, "prompt.txt"), "cli": self.path(label, "cli.json"), "raw": self.path(label, "response.raw"),
                 "json": self.path(label, "response.json"), "provenance": self.path(label, "provenance.json")}
        dr.write_new(paths["prompt"], prompt.encode("utf-8"))
        run = self.runtime.call("editor", prompt)
        dr.write_new(paths["cli"], dr.encoded(run["record"]))
        raw: bytes = run["raw"]
        dr.write_new(paths["raw"], raw)
        parsed, parse_error, fence_stripped = dr.parse_result(raw.decode("utf-8", "replace"))
        if run["error"]:
            parsed, parse_error = None, run["error"]
        if parsed is not None:
            dr.write_new(paths["json"], dr.encoded(parsed))
        provenance = {"label": label, "runtime": self.runtime.name, "started_at": run["started_at"], "seconds": run["seconds"], **run["provenance"],
                      "prompt_sha256": dr.sha256_bytes(prompt.encode("utf-8")), "parse_error": parse_error, "fence_stripped": fence_stripped, "isolation": self.runtime.isolation}
        dr.write_new(paths["provenance"], dr.encoded(provenance))
        call = Call(label, parsed, parse_error, provenance, paths)
        self.calls.append(call)
        return call

    def totals(self) -> dict[str, Any]:
        rows = [c.row() for c in self.calls]
        return {"calls": len(rows), "seconds": round(sum(r["seconds"] or 0 for r in rows), 1),
                "input_tokens": sum(r["input_tokens"] or 0 for r in rows), "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
                "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 5)}


def load_config(path: Path) -> dict[str, Any]:
    """The config as a dict, for recording which endpoint and model a run used (never the key)."""
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise dr.DispatchError(f"{path} must hold one JSON object.")
    return {k: v for k, v in config.items() if k != "api_key"}
