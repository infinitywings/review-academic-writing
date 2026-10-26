#!/usr/bin/env python3
# Legacy controller preserved from package 0.8. It creates and reads masked_reader run-format 2 only;
# keep it to replay the 20 October 2026 pilot runs. The current scripts/masked_reader.py writes format 4.
"""Build bounded reading packets and preserve ordered records; never call a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid


class Invalid(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Invalid(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def decode(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise Invalid("Invalid UTF-8 JSON") from exc


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError as exc:
        raise Invalid(f"Refusing to overwrite: {path}") from exc


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_case(case):
    require(isinstance(case, dict) and nonempty(case.get("id")), "Case requires id")
    blocks = case.get("blocks")
    require(isinstance(blocks, list) and blocks, "Case requires nonempty blocks")
    for block in blocks:
        require(isinstance(block, dict), "Each block must be an object")
        require(all(nonempty(block.get(k)) for k in ("id", "text", "kind")), "Block requires id, text, kind")
        require("page" in block, "Block requires page (null is allowed)")
    ids = [b["id"] for b in blocks]
    require(len(ids) == len(set(ids)), "Duplicate block id")
    windows = case.get("windows")
    require(isinstance(windows, list) and windows and all(isinstance(w, list) and w for w in windows), "Windows must be nonempty lists")
    require([bid for window in windows for bid in window] == ids, "Windows must partition all blocks once, in source order")
    assets = case.get("assets", [])
    require(isinstance(assets, list), "Assets must be a list")
    asset_ids = []
    for asset in assets:
        require(isinstance(asset, dict) and nonempty(asset.get("id")) and nonempty(asset.get("path")), "Asset requires id and path")
        require(isinstance(asset.get("caption"), str), "Asset requires caption string")
        refs = asset.get("referenced_by")
        require(isinstance(refs, list) and all(r in ids for r in refs), "Asset referenced_by must contain known block ids")
        encountered = asset.get("encountered_by", [])
        require(isinstance(encountered, list) and all(r in ids for r in encountered), "Asset encountered_by must contain known block ids")
        asset_ids.append(asset["id"])
    require(len(asset_ids) == len(set(asset_ids)) and not set(asset_ids).intersection(ids), "Asset ids must be unique and distinct from block ids")


def validate_profile(profile):
    require(isinstance(profile, dict), "Profile must be an object")
    task = profile.get("task", profile.get("reader_task"))
    require(nonempty(task), "Profile requires task or reader_task")
    limit = profile.get("memory_word_limit")
    require(isinstance(limit, int) and not isinstance(limit, bool) and limit > 0, "memory_word_limit must be a positive integer")


MEMORY_LISTS = ("established", "tentative", "open_questions", "qualifications")
RESPONSE_FORMAT = {
    "understanding": "A brief current interpretation, as a string.",
    "understood": [{"claim": "string", "basis": "explicit or inferred", "anchor": {"source_id": "an exposed source id", "quote": "optional short quotation"}}],
    "uncertain": [{"question": "string", "needed_now": "boolean", "anchor": {"source_id": "an exposed source id", "quote": "optional short quotation"}}],
    "resolved": [{"question": "the earlier question, as a string", "change": "what changed, as a string"}],
    "next_action": {"kind": "advance or lookup", "target": "null for advance; an available id for lookup", "reason": "brief string"},
    "carry_memory": {"gist": "string", "established": ["string"], "tentative": ["string"], "open_questions": ["string"], "qualifications": ["string"]},
    "source_recognition": {"recognized": "boolean", "description": "string; empty is allowed if not recognized"},
}


def empty_memory():
    return {"gist": "", **{k: [] for k in MEMORY_LISTS}}


def memory_words(memory):
    require(isinstance(memory, dict) and set(memory) == {"gist", *MEMORY_LISTS}, "carry_memory requires exactly gist, established, tentative, open_questions, qualifications")
    require(isinstance(memory["gist"], str), "Memory gist must be a string")
    parts = [memory["gist"]]
    for name in MEMORY_LISTS:
        value = memory[name]
        require(isinstance(value, list) and all(isinstance(x, str) for x in value), f"Memory {name} must be a list of strings")
        parts.extend(value)
    return sum(len(part.split()) for part in parts)


def initial_state():
    return {"cursor": 0, "seen": set(), "seen_assets": set(), "pending_lookup": None, "memory": empty_memory()}


def allowed_targets(case, seen):
    return set(seen) | {a["id"] for a in case.get("assets", []) if (set(a["referenced_by"]) | set(a.get("encountered_by", []))).intersection(seen)}


def packet_value(run, manifest, case, profile, state, seq):
    require(state["cursor"] < len(case["windows"]), "Run is complete; there is no next packet")
    block_map = {b["id"]: b for b in case["blocks"]}
    lookup = state["pending_lookup"]
    seen = set(state["seen"])
    if lookup is None:
        ids = case["windows"][state["cursor"]]
        seen.update(ids)
        materials = [{k: block_map[bid][k] for k in ("id", "text", "page", "kind")} for bid in ids]
        mode = "window"
    elif lookup in block_map:
        require(lookup in seen, "Cannot look up unseen text")
        materials = [{k: block_map[lookup][k] for k in ("id", "text", "page", "kind")}]
        mode = "lookup"
    else:
        require(lookup in allowed_targets(case, seen), "Asset has not been referenced by seen text")
        asset = next(a for a in case["assets"] if a["id"] == lookup)
        info = manifest["assets"][lookup]
        asset_path = run / "visible-assets" / f"{seq:06d}" / ("asset" + info["suffix"])
        materials = [{"id": lookup, "kind": "asset", "caption": asset["caption"], "path": str(asset_path), "sha256": info["sha256"]}]
        mode = "lookup"
    return {
        "packet_id": f"{manifest['run_id']}:{seq}",
        "mode": mode,
        "end_of_supplied_text": mode == "window" and state["cursor"] == len(case["windows"]) - 1,
        "reader_profile": profile,
        "carry_memory": state["memory"],
        "materials": materials,
        "available_lookup_ids": sorted(allowed_targets(case, seen)),
        "response_format": RESPONSE_FORMAT,
        "renderer": {"image_paths_to_inspect": [m["path"] for m in materials if m["kind"] == "asset"], "note": "Use the supplied view_image capability for these paths; a path alone is not visual evidence. No other file reading is authorized by this packet."},
        "instructions": (
            "Use a new context for this packet, including after a lookup. Use only these materials and carry_memory. "
            "Keep explicit statements separate from inferences; retain unresolved questions and scientific qualifications. "
            "Do not consult other files, future text, another version, expert comments, or prior full responses. "
            "An asset path must actually be presented as an image or inspected before reporting visual understanding. "
            + ("This is a lookup. Set next_action.kind to advance to return to the original text window in a new context. "
               if mode == "lookup" else "Set next_action to advance, or request one available lookup id with a reason. ")
            + ("This is the last window of the supplied text. After any necessary lookup, record remaining uncertainties without assuming that more text will be supplied. The supplied text may be an excerpt; an unanswered question is not automatically a defect. "
               if mode == "window" and state["cursor"] == len(case["windows"]) - 1 else "")
            + "Return the documented response JSON. carry_memory is the only response content passed to the next reader."
        ),
    }


def validate_response(response, packet, profile, available_ids):
    require(isinstance(response, dict), "Response must be an object")
    require(nonempty(response.get("understanding")), "Response requires a brief understanding string")
    for name in ("understood", "uncertain", "resolved"):
        require(isinstance(response.get(name), list), f"Response requires {name} list")
    for item in response["understood"]:
        require(isinstance(item, dict) and nonempty(item.get("claim")), "Understood item requires claim")
        require(item.get("basis") in ("explicit", "inferred"), "basis must be explicit or inferred")
        validate_anchor(item.get("anchor"), available_ids)
    for item in response["uncertain"]:
        require(isinstance(item, dict) and nonempty(item.get("question")) and isinstance(item.get("needed_now"), bool), "Uncertain item requires question and needed_now boolean")
        validate_anchor(item.get("anchor"), available_ids)
    for item in response["resolved"]:
        require(isinstance(item, dict) and nonempty(item.get("question")) and nonempty(item.get("change")), "Resolved item requires question and change")
    count = memory_words(response.get("carry_memory"))
    require(count <= profile["memory_word_limit"], f"carry_memory has {count} words; limit is {profile['memory_word_limit']}")
    recognition = response.get("source_recognition")
    require(isinstance(recognition, dict) and isinstance(recognition.get("recognized"), bool) and isinstance(recognition.get("description"), str), "source_recognition requires recognized boolean and description string")
    action = response.get("next_action")
    require(isinstance(action, dict) and nonempty(action.get("reason")), "next_action requires reason")
    require(action.get("kind") in ("advance", "lookup"), "next_action.kind must be advance or lookup")
    if action["kind"] == "advance":
        require(action.get("target") is None, "advance target must be null")
    else:
        require(packet["mode"] == "window", "One lookup at a time: finish the lookup and return to the text before requesting another")
        require(action.get("target") in packet["available_lookup_ids"], "Lookup target is unseen or has not been referenced")
    return count


def validate_anchor(anchor, available_ids):
    # Anchors locate an exposed source, not proof that a claim is entailed by it.
    if isinstance(anchor, str):
        require(anchor in available_ids, "String anchor must be an exposed source id")
    else:
        require(isinstance(anchor, dict) and anchor.get("source_id") in available_ids, "Anchor requires exposed source_id")
        require(isinstance(anchor.get("quote", ""), str), "Anchor quote must be a string")


def update_state(case, state, packet, response):
    if packet["mode"] == "window":
        state["seen"].update(case["windows"][state["cursor"]])
        if response["next_action"]["kind"] == "lookup":
            state["pending_lookup"] = response["next_action"]["target"]
        else:
            state["cursor"] += 1
    else:
        target = state["pending_lookup"]
        if target not in state["seen"]:
            state["seen_assets"].add(target)
        state["pending_lookup"] = None
    state["memory"] = response["carry_memory"]


def load_run(run):
    run = Path(run).resolve()
    manifest_bytes = (run / "manifest.json").read_bytes()
    manifest = decode(manifest_bytes)
    require(manifest.get("format_version") == 2, "Use the original controller for earlier run formats; this controller creates and reads version-2 runs")
    source_values = []
    for name in ("case", "profile"):
        info = manifest[name]
        raw = (run / f"{name}.snapshot.json").read_bytes()
        require(digest(raw) == info["sha256"], f"{name} snapshot changed")
        require(digest(Path(info["source_path"]).read_bytes()) == info["sha256"], f"{name} source changed; start a new run")
        source_values.append(decode(raw))
    case, profile = source_values
    validate_case(case)
    validate_profile(profile)
    for info in manifest["assets"].values():
        require(digest((run / info["snapshot"]).read_bytes()) == info["sha256"], "Asset snapshot changed")
        require(digest(Path(info["source_path"]).read_bytes()) == info["sha256"], "Asset source changed; start a new run")
    state = initial_state()
    previous = digest(manifest_bytes)
    entries = sorted((run / "records").iterdir())
    for seq, entry in enumerate(entries, 1):
        require(entry.is_dir() and entry.name == f"{seq:06d}", "Records are incomplete or out of order")
        raw_packet = (entry / "packet.json").read_bytes()
        raw_response = (entry / "response.json").read_bytes()
        raw_receipt = (entry / "receipt.json").read_bytes()
        receipt = decode(raw_receipt)
        packet = decode(raw_packet)
        require(receipt["sequence"] == seq and receipt["previous_receipt_sha256"] == previous, "Receipt chain changed")
        require(receipt["packet_sha256"] == digest(raw_packet) and receipt["response_sha256"] == digest(raw_response), "Recorded input or response changed")
        require((run / "packets" / f"{seq:06d}.json").read_bytes() == raw_packet, "Issued packet changed")
        require(packet == packet_value(run, manifest, case, profile, state, seq), "Recorded packet violates reading order or visibility")
        verify_visible_assets(packet)
        response = decode(raw_response)
        ids = state["seen"] | state["seen_assets"] | {m["id"] for m in packet["materials"]}
        validate_response(response, packet, profile, ids)
        update_state(case, state, packet, response)
        previous = digest(raw_receipt)
    return run, manifest, case, profile, state, len(entries) + 1, previous


def verify_visible_assets(packet):
    for item in packet["materials"]:
        if item["kind"] == "asset":
            require(digest(Path(item["path"]).read_bytes()) == item["sha256"], "Visible asset changed")


def start(case_path, profile_path, out):
    case_path, profile_path, run = map(lambda p: Path(p).resolve(), (case_path, profile_path, out))
    case_raw, profile_raw = case_path.read_bytes(), profile_path.read_bytes()
    case, profile = decode(case_raw), decode(profile_raw)
    validate_case(case)
    validate_profile(profile)
    require(not run.exists(), "Run directory already exists; choose a new directory")
    assets = {}
    asset_data = {}
    for i, asset in enumerate(case.get("assets", []), 1):
        source_path = (case_path.parent / asset["path"]).resolve()
        raw = source_path.read_bytes()
        suffix = source_path.suffix or ".bin"
        assets[asset["id"]] = {"source_path": str(source_path), "sha256": digest(raw), "snapshot": f"asset-snapshots/{i:06d}{suffix}", "suffix": suffix}
        asset_data[asset["id"]] = raw
    manifest = {"format_version": 2, "run_id": str(uuid.uuid4()), "case": {"source_path": str(case_path), "sha256": digest(case_raw)}, "profile": {"source_path": str(profile_path), "sha256": digest(profile_raw)}, "assets": assets}
    run.mkdir(parents=True)
    (run / "records").mkdir()
    (run / "packets").mkdir()
    write_new(run / "case.snapshot.json", case_raw)
    write_new(run / "profile.snapshot.json", profile_raw)
    for aid, raw in asset_data.items():
        write_new(run / assets[aid]["snapshot"], raw)
    write_new(run / "manifest.json", encoded(manifest))
    return {"ok": True, "run": str(run), "memory_word_limit": profile["memory_word_limit"]}


def packet(run_path, out, lookup=None):
    run, manifest, case, profile, state, seq, _ = load_run(run_path)
    require(lookup is None or lookup == state["pending_lookup"], "--lookup must match the reader's recorded lookup request")
    value = packet_value(run, manifest, case, profile, state, seq)
    raw = encoded(value)
    out = Path(out).resolve()
    internal = run / "packets" / f"{seq:06d}.json"
    require(out != internal, "Export path must differ from the internal packet path")
    require(not out.exists(), "Packet export path exists; refusing overwrite")
    for material in value["materials"]:
        if material["kind"] == "asset":
            target = Path(material["path"])
            info = manifest["assets"][material["id"]]
            if target.exists():
                require(digest(target.read_bytes()) == info["sha256"], "Visible asset changed")
            else:
                write_new(target, (run / info["snapshot"]).read_bytes())
    if internal.exists():
        require(internal.read_bytes() == raw, "Pending packet changed")
    else:
        write_new(internal, raw)
    write_new(out, raw)
    return {"ok": True, "packet": str(out), "sequence": seq, "mode": value["mode"], "sha256": digest(raw)}


def record(run_path, packet_path, response_path, agent_id=None):
    run, manifest, case, profile, state, seq, previous = load_run(run_path)
    issued = run / "packets" / f"{seq:06d}.json"
    require(issued.is_file(), "No packet has been issued for the next record")
    raw_packet = Path(packet_path).read_bytes()
    require(raw_packet == issued.read_bytes(), "Packet is stale, changed, or out of order")
    value = decode(raw_packet)
    require(value == packet_value(run, manifest, case, profile, state, seq), "Packet violates current visibility")
    verify_visible_assets(value)
    raw_response = Path(response_path).read_bytes()
    response = decode(raw_response)
    ids = state["seen"] | state["seen_assets"] | {m["id"] for m in value["materials"]}
    count = validate_response(response, value, profile, ids)
    receipt = {"sequence": seq, "packet_id": value["packet_id"], "packet_sha256": digest(raw_packet), "response_sha256": digest(raw_response), "previous_receipt_sha256": previous, "agent_id": agent_id, "visible_source_ids": [m["id"] for m in value["materials"]], "mode": value["mode"], "memory_words": count}
    # Construct a complete record off to the side, then publish it by rename.
    target = run / "records" / f"{seq:06d}"
    require(not target.exists(), "Record already exists; refusing overwrite")
    temporary = run / f"pending-record-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        write_new(temporary / "packet.json", raw_packet)
        write_new(temporary / "response.json", raw_response)
        write_new(temporary / "receipt.json", encoded(receipt))
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    update_state(case, state, value, response)
    complete = state["cursor"] == len(case["windows"])
    return {"ok": True, "sequence": seq, "memory_words": count, "complete": complete, "next_mode": "done" if complete else ("lookup" if state["pending_lookup"] else "window")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("start")
    p.add_argument("--case", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p = commands.add_parser("packet")
    p.add_argument("--run", required=True)
    p.add_argument("--lookup")
    p.add_argument("--out", required=True)
    p = commands.add_parser("record")
    p.add_argument("--run", required=True)
    p.add_argument("--packet", required=True)
    p.add_argument("--response", required=True)
    p.add_argument("--agent-id")
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = start(args.case, args.profile, args.out)
        elif args.command == "packet":
            result = packet(args.run, args.out, args.lookup)
        else:
            result = record(args.run, args.packet, args.response, args.agent_id)
    except (Invalid, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"masked_reader: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
