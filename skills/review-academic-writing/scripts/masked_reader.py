#!/usr/bin/env python3
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
    require(case.get("asset_policy") in ASSET_POLICIES, "Case requires asset_policy: strict-prefix (an asset opens only at its layout position) or reference-jump (an explicit reference also opens it)")


FORMAT_VERSION = 5
ASSET_POLICIES = ("strict-prefix", "reference-jump")
BASIS_KINDS = ("explicit", "inferred", "background")
MEMORY_UNITS = ("words", "characters")
NOTE_KINDS = ("established", "tentative", "open_question", "qualification")
SUPPORT_KINDS = ("stated", "inferred", "background", "undetermined")


def memory_limit(profile):
    return profile.get("memory_limit", profile.get("memory_word_limit"))


def memory_unit(profile):
    return profile.get("memory_unit", "words")


def validate_profile(profile):
    require(isinstance(profile, dict), "Profile must be an object")
    task = profile.get("task", profile.get("reader_task"))
    require(nonempty(task), "Profile requires task or reader_task")
    limit = memory_limit(profile)
    require(isinstance(limit, int) and not isinstance(limit, bool) and limit > 0, "memory_limit must be a positive integer")
    require(memory_unit(profile) in MEMORY_UNITS, "memory_unit must be words or characters")


ANCHOR_FORMAT = {"source_id": "an exposed source id", "quote": "optional exact quotation from that text block"}
RECOGNITION_FORMAT = {"recognized": "boolean", "description": "string; empty is allowed if not recognized"}
RESPONSE_FORMAT = {
    "understanding": "A brief current interpretation, as a string.",
    "understood": [{"claim": "string", "basis": "explicit (the text states it), inferred (a short inference from the text), or background (relies on the declared reader background)", "anchor": ANCHOR_FORMAT}],
    "uncertain": [{"question": "string", "needed_now": "boolean", "anchor": ANCHOR_FORMAT}],
    "resolved": [{"question": "the earlier question, as a string", "change": "what changed, as a string"}],
    "next_action": {"kind": "advance or lookup", "target": "null for advance; an available id for lookup", "reason": "brief string"},
    "carry_memory": {
        "gist": "string",
        "notes": [{"id": "stable id; keep a carried note's id", "kind": "established, tentative, open_question or qualification", "text": "string", "sources": ["ids of seen text blocks or viewed assets the note rests on"], "since": "integer: this packet's sequence for a new note; copied unchanged for a carried note"}],
        "dropped": [{"id": "id of a carried note that is not kept", "reason": "brief string"}],
    },
    "source_recognition": RECOGNITION_FORMAT,
}
BRANCH_RESPONSE_FORMAT = {
    "answer": "string",
    "support": "stated, inferred, background or undetermined",
    "anchors": [ANCHOR_FORMAT],
    "inference": "how the visible text supports the answer; required when support is inferred, else empty",
    "background_used": "which declared background the answer relies on; required when support is background, else empty",
    "undetermined": "the condition or relation the visible text leaves open; required when support is undetermined, else empty",
    "source_recognition": RECOGNITION_FORMAT,
}


def empty_memory():
    return {"gist": "", "notes": []}


def memory_size(memory, profile):
    parts = [memory["gist"]] + [note["text"] for note in memory["notes"]]
    if memory_unit(profile) == "characters":
        return sum(sum(not ch.isspace() for ch in part) for part in parts)
    return sum(len(part.split()) for part in parts)


def validate_memory(memory, carried, exposed, sequence, profile):
    """Check each note's lineage and sources; return what this event added, dropped and revised."""
    require(isinstance(memory, dict) and set(memory) == {"gist", "notes", "dropped"}, "carry_memory requires exactly gist, notes, dropped")
    require(isinstance(memory["gist"], str), "Memory gist must be a string")
    require(isinstance(memory["notes"], list) and isinstance(memory["dropped"], list), "Memory notes and dropped must be lists")
    carried_notes = {note["id"]: note for note in carried["notes"]}
    kept = {}
    for note in memory["notes"]:
        require(isinstance(note, dict) and set(note) == {"id", "kind", "text", "sources", "since"}, "Each note requires exactly id, kind, text, sources, since")
        require(nonempty(note["id"]) and note["id"] not in kept, "Note ids must be nonempty and unique")
        require(note["kind"] in NOTE_KINDS, f"Note {note['id']} kind must be one of {', '.join(NOTE_KINDS)}")
        require(nonempty(note["text"]), f"Note {note['id']} requires text")
        require(isinstance(note["sources"], list) and note["sources"] and all(isinstance(s, str) for s in note["sources"]), f"Note {note['id']} requires a nonempty list of source ids")
        require(isinstance(note["since"], int) and not isinstance(note["since"], bool), f"Note {note['id']} since must be an integer")
        if note["id"] in carried_notes:
            origin = carried_notes[note["id"]]
            require(note["since"] == origin["since"], f"Carried note {note['id']} must keep since={origin['since']}")
            allowed = set(origin["sources"]) | set(exposed)
        else:
            require(note["since"] == sequence, f"New note {note['id']} must have since={sequence}")
            allowed = set(exposed)
        require(set(note["sources"]) <= allowed, f"Note {note['id']} cites a source the reader has not seen")
        kept[note["id"]] = note
    dropped = []
    for item in memory["dropped"]:
        require(isinstance(item, dict) and set(item) == {"id", "reason"} and nonempty(item["reason"]), "Each dropped entry requires exactly id and reason")
        require(item["id"] in carried_notes and item["id"] not in kept and item["id"] not in dropped, "dropped must name a carried note that is not kept, once each")
        dropped.append(item["id"])
    missing = sorted(set(carried_notes) - set(kept) - set(dropped))
    require(not missing, f"Carried notes must be kept or explicitly dropped: {', '.join(missing)}")
    size = memory_size(memory, profile)
    require(size <= memory_limit(profile), f"carry_memory has {size} {memory_unit(profile)}; limit is {memory_limit(profile)}")
    revised = [i for i in kept if i in carried_notes and any(kept[i][k] != carried_notes[i][k] for k in ("kind", "text", "sources"))]
    return {"memory_size": size, "notes_added": [i for i in kept if i not in carried_notes], "notes_dropped": dropped, "notes_revised": revised}


def initial_state():
    return {"cursor": 0, "seen": set(), "seen_assets": set(), "pending_lookup": None, "memory": empty_memory(), "events": []}


def allowed_targets(case, seen):
    """Assets open at their layout position; under reference-jump an explicit reference opens them too."""
    def opens(asset):
        ids = set(asset.get("encountered_by", []))
        if case["asset_policy"] == "reference-jump":
            ids |= set(asset["referenced_by"])
        return ids.intersection(seen)
    return set(seen) | {a["id"] for a in case.get("assets", []) if opens(a)}


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
    last_window = mode == "window" and state["cursor"] == len(case["windows"]) - 1
    return {
        "packet_id": f"{manifest['run_id']}:{seq}",
        "sequence": seq,
        "mode": mode,
        "asset_policy": case["asset_policy"],
        "end_of_supplied_text": last_window,
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
               if last_window else "")
            + "Return the documented response JSON. carry_memory is the only response content passed to the next reader: keep each carried note under its id with its since value, or list it under dropped with a reason; give a new note since equal to this packet's sequence and cite only sources you have seen."
        ),
    }


def validate_response(response, packet, profile, exposed):
    require(isinstance(response, dict), "Response must be an object")
    require(nonempty(response.get("understanding")), "Response requires a brief understanding string")
    for name in ("understood", "uncertain", "resolved"):
        require(isinstance(response.get(name), list), f"Response requires {name} list")
    for item in response["understood"]:
        require(isinstance(item, dict) and nonempty(item.get("claim")), "Understood item requires claim")
        require(item.get("basis") in BASIS_KINDS, f"basis must be one of {', '.join(BASIS_KINDS)}")
        validate_anchor(item.get("anchor"), exposed)
    for item in response["uncertain"]:
        require(isinstance(item, dict) and nonempty(item.get("question")) and isinstance(item.get("needed_now"), bool), "Uncertain item requires question and needed_now boolean")
        validate_anchor(item.get("anchor"), exposed)
    for item in response["resolved"]:
        require(isinstance(item, dict) and nonempty(item.get("question")) and nonempty(item.get("change")), "Resolved item requires question and change")
    stats = validate_memory(response.get("carry_memory"), packet["carry_memory"], exposed, packet["sequence"], profile)
    validate_recognition(response.get("source_recognition"))
    action = response.get("next_action")
    require(isinstance(action, dict) and nonempty(action.get("reason")), "next_action requires reason")
    require(action.get("kind") in ("advance", "lookup"), "next_action.kind must be advance or lookup")
    if action["kind"] == "advance":
        require(action.get("target") is None, "advance target must be null")
    else:
        require(packet["mode"] == "window", "One lookup at a time: finish the lookup and return to the text before requesting another")
        require(action.get("target") in packet["available_lookup_ids"], "Lookup target is unseen or has not been referenced")
    return stats


def validate_recognition(recognition):
    require(isinstance(recognition, dict) and isinstance(recognition.get("recognized"), bool) and isinstance(recognition.get("description"), str), "source_recognition requires recognized boolean and description string")


def exposed_sources(case, state, packet):
    """Every source id the reader could cite at this event, mapped to its text (None for an asset)."""
    text = {b["id"]: b["text"] for b in case["blocks"]}
    ids = state["seen"] | state["seen_assets"] | {m["id"] for m in packet["materials"]}
    return {i: text.get(i) for i in ids}


def validate_anchor(anchor, exposed):
    # Anchors locate an exposed source, not proof that a claim is entailed by it.
    if isinstance(anchor, str):
        require(anchor in exposed, "String anchor must be an exposed source id")
        return
    require(isinstance(anchor, dict) and anchor.get("source_id") in exposed, "Anchor requires exposed source_id")
    quote = anchor.get("quote", "")
    require(isinstance(quote, str), "Anchor quote must be a string")
    text = exposed[anchor["source_id"]]
    require(not quote or text is None or quote in text, f"Anchor quote is not exact text of {anchor['source_id']}")


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
    memory = response["carry_memory"]
    state["memory"] = {"gist": memory["gist"], "notes": memory["notes"]}


def load_run(run):
    """Replay the main reading path. Branch records are never read here, so they cannot alter it."""
    run = Path(run).resolve()
    manifest_bytes = (run / "manifest.json").read_bytes()
    manifest = decode(manifest_bytes)
    require(manifest.get("format_version") == FORMAT_VERSION, f"Use the original controller for earlier run formats; this controller creates and reads version-{FORMAT_VERSION} runs")
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
        exposed = exposed_sources(case, state, packet)
        stats = validate_response(response, packet, profile, exposed)
        require(all(receipt.get(k) == v for k, v in stats.items()), "Receipt memory record changed")
        state["events"].append({"sequence": seq, "packet": packet, "exposed": exposed})
        update_state(case, state, packet, response)
        previous = digest(raw_receipt)
    return run, manifest, case, profile, state, len(entries) + 1, previous


def verify_visible_assets(packet):
    for item in packet["materials"]:
        if item["kind"] == "asset":
            require(digest(Path(item["path"]).read_bytes()) == item["sha256"], "Visible asset changed")


def publish(run, target, raw_packet, raw_response, receipt):
    """Construct a complete record off to the side, then publish it by rename."""
    require(not target.exists(), "Record already exists; refusing overwrite")
    temporary = run / f"pending-record-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        write_new(temporary / "packet.json", raw_packet)
        write_new(temporary / "response.json", raw_response)
        write_new(temporary / "receipt.json", encoded(receipt))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


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
    manifest = {"format_version": FORMAT_VERSION, "run_id": str(uuid.uuid4()), "case": {"source_path": str(case_path), "sha256": digest(case_raw)}, "profile": {"source_path": str(profile_path), "sha256": digest(profile_raw)}, "assets": assets}
    run.mkdir(parents=True)
    (run / "records").mkdir()
    (run / "packets").mkdir()
    write_new(run / "case.snapshot.json", case_raw)
    write_new(run / "profile.snapshot.json", profile_raw)
    for aid, raw in asset_data.items():
        write_new(run / assets[aid]["snapshot"], raw)
    write_new(run / "manifest.json", encoded(manifest))
    return {"ok": True, "run": str(run), "memory_limit": memory_limit(profile), "memory_unit": memory_unit(profile)}


def export_packet(run, internal, out, raw, pending_message):
    out = Path(out).resolve()
    require(out != internal, "Export path must differ from the internal packet path")
    require(not out.exists(), "Packet export path exists; refusing overwrite")
    if internal.exists():
        require(internal.read_bytes() == raw, pending_message)
    else:
        write_new(internal, raw)
    write_new(out, raw)
    return out


def packet(run_path, out, lookup=None):
    run, manifest, case, profile, state, seq, _ = load_run(run_path)
    require(lookup is None or lookup == state["pending_lookup"], "--lookup must match the reader's recorded lookup request")
    value = packet_value(run, manifest, case, profile, state, seq)
    raw = encoded(value)
    for material in value["materials"]:
        if material["kind"] == "asset":
            target = Path(material["path"])
            info = manifest["assets"][material["id"]]
            if target.exists():
                require(digest(target.read_bytes()) == info["sha256"], "Visible asset changed")
            else:
                write_new(target, (run / info["snapshot"]).read_bytes())
    out = export_packet(run, run / "packets" / f"{seq:06d}.json", out, raw, "Pending packet changed")
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
    stats = validate_response(response, value, profile, exposed_sources(case, state, value))
    receipt = {"sequence": seq, "packet_id": value["packet_id"], "packet_sha256": digest(raw_packet), "response_sha256": digest(raw_response), "previous_receipt_sha256": previous, "agent_id": agent_id, "visible_source_ids": [m["id"] for m in value["materials"]], "mode": value["mode"], **stats}
    publish(run, run / "records" / f"{seq:06d}", raw_packet, raw_response, receipt)
    update_state(case, state, value, response)
    complete = state["cursor"] == len(case["windows"])
    return {"ok": True, "sequence": seq, **stats, "complete": complete, "next_mode": "done" if complete else ("lookup" if state["pending_lookup"] else "window")}


def validate_question(question):
    require(isinstance(question, dict) and set(question) == {"id", "question", "selected_with_full_text", "reason"}, "Question requires exactly id, question, selected_with_full_text, reason")
    require(all(nonempty(question[k]) for k in ("id", "question", "reason")), "Question id, question and reason must be nonempty strings")
    require(isinstance(question["selected_with_full_text"], bool), "selected_with_full_text must be a boolean")


def branch_value(manifest, profile, state, origin, question, branch_seq):
    """A one-shot packet that shows exactly what the reader had at a recorded display event, plus one question."""
    validate_question(question)
    require(isinstance(origin, int) and not isinstance(origin, bool) and 1 <= origin <= len(state["events"]), "A branch must start from a recorded display event")
    packet = state["events"][origin - 1]["packet"]
    return {
        "packet_id": f"{manifest['run_id']}:branch:{branch_seq}",
        "mode": "branch",
        "branch_sequence": branch_seq,
        "branched_from_sequence": origin,
        "question": question,
        "reader_profile": profile,
        "carry_memory": packet["carry_memory"],
        "materials": packet["materials"],
        "response_format": BRANCH_RESPONSE_FORMAT,
        "renderer": packet["renderer"],
        "instructions": (
            "Use a new context. Answer only the question from these materials, carry_memory and the profile's declared background. "
            "State whether the visible text states the answer, supports it by inference, needs declared background, or leaves it undetermined; quote exact text in anchors. "
            "Do not propose edits and do not continue reading. Nothing from this branch is passed to the main reading."
        ),
    }


def validate_branch_response(response, exposed):
    keys = {"answer", "support", "anchors", "inference", "background_used", "undetermined", "source_recognition"}
    require(isinstance(response, dict) and set(response) == keys, "Branch response requires exactly answer, support, anchors, inference, background_used, undetermined, source_recognition")
    require(nonempty(response["answer"]), "Branch response requires an answer")
    require(response["support"] in SUPPORT_KINDS, f"support must be one of {', '.join(SUPPORT_KINDS)}")
    require(isinstance(response["anchors"], list), "anchors must be a list")
    for anchor in response["anchors"]:
        validate_anchor(anchor, exposed)
    for key in ("inference", "background_used", "undetermined"):
        require(isinstance(response[key], str), f"{key} must be a string")
    needed = {"stated": None, "inferred": "inference", "background": "background_used", "undetermined": "undetermined"}[response["support"]]
    require(needed is None or nonempty(response[needed]), f"support={response['support']} requires {needed}")
    require(response["support"] not in ("stated", "inferred") or response["anchors"], f"support={response['support']} requires at least one anchor")
    validate_recognition(response["source_recognition"])


def load_branches(run, manifest, profile, state):
    """Replay branch records against the frozen main events."""
    root = run / "branches"
    entries = sorted(root.iterdir()) if root.is_dir() else []
    branches = []
    for bseq, entry in enumerate(entries, 1):
        require(entry.is_dir() and entry.name == f"{bseq:06d}", "Branch records are incomplete or out of order")
        raw_packet = (entry / "packet.json").read_bytes()
        raw_response = (entry / "response.json").read_bytes()
        receipt = decode((entry / "receipt.json").read_bytes())
        packet = decode(raw_packet)
        require(receipt["branch_sequence"] == bseq and receipt["packet_sha256"] == digest(raw_packet) and receipt["response_sha256"] == digest(raw_response), "Branch record changed")
        require((run / "branch-packets" / f"{bseq:06d}.json").read_bytes() == raw_packet, "Issued branch packet changed")
        origin = packet["branched_from_sequence"]
        require(packet == branch_value(manifest, profile, state, origin, packet["question"], bseq), "Branch packet does not match its display event")
        require(receipt["event_receipt_sha256"] == digest((run / "records" / f"{origin:06d}" / "receipt.json").read_bytes()), "Branch is not bound to its display event")
        validate_branch_response(decode(raw_response), state["events"][origin - 1]["exposed"])
        branches.append({"sequence": bseq, "packet": packet, "receipt": receipt})
    return branches


def branch(run_path, origin, question_path, out):
    run, manifest, _case, profile, state, _, _ = load_run(run_path)
    bseq = len(load_branches(run, manifest, profile, state)) + 1
    value = branch_value(manifest, profile, state, origin, decode(Path(question_path).read_bytes()), bseq)
    verify_visible_assets(value)
    raw = encoded(value)
    out = export_packet(run, run / "branch-packets" / f"{bseq:06d}.json", out, raw, "A different branch packet is pending; record it before issuing another")
    return {"ok": True, "packet": str(out), "branch_sequence": bseq, "branched_from_sequence": origin, "sha256": digest(raw)}


def branch_record(run_path, packet_path, response_path, agent_id=None):
    run, manifest, _case, profile, state, _, _ = load_run(run_path)
    bseq = len(load_branches(run, manifest, profile, state)) + 1
    issued = run / "branch-packets" / f"{bseq:06d}.json"
    require(issued.is_file(), "No branch packet has been issued for the next branch record")
    raw_packet = Path(packet_path).read_bytes()
    require(raw_packet == issued.read_bytes(), "Branch packet is stale, changed, or out of order")
    value = decode(raw_packet)
    origin = value["branched_from_sequence"]
    require(value == branch_value(manifest, profile, state, origin, value["question"], bseq), "Branch packet does not match its display event")
    verify_visible_assets(value)
    raw_response = Path(response_path).read_bytes()
    response = decode(raw_response)
    validate_branch_response(response, state["events"][origin - 1]["exposed"])
    receipt = {"branch_sequence": bseq, "packet_id": value["packet_id"], "branched_from_sequence": origin, "packet_sha256": digest(raw_packet), "response_sha256": digest(raw_response), "event_receipt_sha256": digest((run / "records" / f"{origin:06d}" / "receipt.json").read_bytes()), "created_after_sequence": len(state["events"]), "agent_id": agent_id}
    publish(run, run / "branches" / f"{bseq:06d}", raw_packet, raw_response, receipt)
    return {"ok": True, "branch_sequence": bseq, "branched_from_sequence": origin, "support": response["support"]}


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
    p = commands.add_parser("branch", help="Issue a one-shot question packet from a recorded display event.")
    p.add_argument("--run", required=True)
    p.add_argument("--from-sequence", required=True, type=int)
    p.add_argument("--question", required=True)
    p.add_argument("--out", required=True)
    p = commands.add_parser("branch-record", help="Save a branch answer; it never enters the main reading path.")
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
        elif args.command == "record":
            result = record(args.run, args.packet, args.response, args.agent_id)
        elif args.command == "branch":
            result = branch(args.run, args.from_sequence, args.question, args.out)
        else:
            result = branch_record(args.run, args.packet, args.response, args.agent_id)
    except (Invalid, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"masked_reader: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
