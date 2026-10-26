import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rs = load("review_steps")
rp, mr = rs.rp, rs.mr

TEXT = """# Measuring agreement between two records

We measured how often two independent records agreed. Each record was produced by a separate observer.

Agreement fell sharply when the MSW exceeded two seconds, so we restricted the analysis to pairs within that window.

We call the time between consecutive measurements the Measurement Separation Window (MSW). We set the MSW to two seconds.

The restricted analysis included 412 pairs. Agreement in this set was 91 percent.
"""
PROFILE = {"audience": "A trained researcher new to this project"}
INCLUDED = ["B0002", "B0003", "B0004", "B0005"]
DEFINITION = {"block_id": "B0004", "quote": "We call the time between consecutive measurements the Measurement Separation Window (MSW)."}
CRITICAL = {"block_id": "B0003", "quote": "the MSW exceeded two seconds"}
ORIGINAL = {"block_id": "B0003", "quote": "Agreement fell sharply when the MSW exceeded two seconds"}
WORDING = "Agreement fell sharply when consecutive measurements were more than two seconds apart."
STAGE_FILES = {stage: [f"0{n}-{stage}.notes.json", f"0{n}-{stage}.receipt.json"]
               for n, stage in enumerate(("reading", "checks", "reconcile", "decisions", "repairs"), 1)}


def A(block_id, quote):
    return {"block_id": block_id, "quote": quote}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReviewStepsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.index = rp.build_index(TEXT)
        self.sid = self.index["snapshot_id"]
        self.text = {b["block_id"]: b["text"] for b in self.index["blocks"]}
        self.journal = self.root / "journal"
        rs.start_run(self.index, PROFILE, self.scope(), self.journal)
        self.audit_path = self.write_audit(self.audit())

    def tearDown(self):
        self.tmp.cleanup()

    def scope(self, omissions=None):
        return {"task": "review", "included_block_ids": INCLUDED, "reading_path": "linear", "extraction_notes": "plain",
                "omissions": [{"block_id": "B0001", "reason": "title"}] if omissions is None else omissions}

    def notes(self, stage):
        base = {"snapshot_id": self.sid, "summary": stage + " notes"}
        if stage == "reading":
            return {**base, "paragraphs": [{"anchor": A(b, self.text[b][:20]), "paraphrase": "p", "role": "r",
                                            "required_context": [], "new_information": [], "open_questions": []} for b in INCLUDED]}
        if stage == "checks":
            return {**base, "concepts": [], "no_concepts_reason": "none questioned",
                    "checkpoint_plan": [{"id": "P1", "anchor": CRITICAL, "mode": "fresh-prefix", "reason": "first critical use of MSW"}],
                    "passages": [{"block_id": b, "structure_check": "s", "wording_check": "w", "evidence": [A(b, self.text[b][:15])]} for b in INCLUDED],
                    "candidates": [{"id": "C01", "anchor": A("B0003", "the MSW exceeded two seconds"), "question": "Is MSW defined first?", "evidence": [DEFINITION]},
                                   {"id": "C02", "anchor": A("B0005", "412 pairs"), "question": "How were pairs counted?", "evidence": []},
                                   {"id": "C03", "anchor": A("B0002", "a separate observer"), "question": "Who observed?", "evidence": []},
                                   {"id": "C04", "anchor": A("B0003", "MSW"), "question": "Same symptom as C01?", "evidence": []}]}
        if stage == "reconcile":
            return {**base, "checkpoints": [], "no_checkpoints_reason": "none run", "branches": [], "observations": [],
                    "candidates": [{"id": "C05", "anchor": A("B0005", "91 percent"), "question": "Is 91 percent compared with anything?", "evidence": []}]}
        if stage == "decisions":
            common = {"verification": "v", "countercheck": "c", "evidence": []}
            return {**base, "decisions": [
                {**common, "candidate_id": "C01", "status": "accepted", "evidence": [DEFINITION], "category": "late_prerequisite",
                 "diagnosis": "MSW is used in B0003 before B0004 defines it.", "severity": "major", "confidence": "high"},
                {**common, "candidate_id": "C02", "status": "needs_author", "category": "unsupported_claim",
                 "diagnosis": "The pair count has no stated source.", "severity": "minor", "confidence": "medium"},
                {**common, "candidate_id": "C03", "status": "rejected"},
                {**common, "candidate_id": "C04", "status": "merged", "merged_into": "C01"},
                {**common, "candidate_id": "C05", "status": "accepted", "category": "unsupported_claim",
                 "diagnosis": "The agreement rate has no comparison.", "severity": "minor", "confidence": "medium"}]}
        return {**base, "repairs": [
            {"candidate_id": "C01", "kind": "move", "action": "Move the definition ahead of B0003.", "proposed_wording": WORDING,
             "scientific_fidelity": "threshold preserved", "new_terms_check": "drops MSW", "affected_block_ids": ["B0003", "B0004"], "author_question": None,
             "audit_path": str(self.root / "audit-C01.json")},
            {"candidate_id": "C02", "kind": "author_question", "action": "Ask how pairs were counted.", "proposed_wording": None,
             "scientific_fidelity": "no change", "new_terms_check": "none", "affected_block_ids": ["B0005"], "author_question": "How were the 412 pairs selected?", "audit_path": None},
            {"candidate_id": "C05", "kind": "edit", "action": "State the comparison rate.", "proposed_wording": None,
             "scientific_fidelity": "rate preserved", "new_terms_check": "none", "affected_block_ids": ["B0005"], "author_question": None}]}

    def audit(self, **change):
        checks = {name: "same" for name in ("claim_strength", "quantifiers", "assumptions", "population", "measured_outcome", "numerical_values", "uncertainty", "causal_vs_associative")}
        return {"candidate_id": "C01", "proposed_wording": WORDING, "original": [ORIGINAL], "verdict": "preserved", "checks": {**checks, "time_horizon": "not_applicable"},
                "notes": "Threshold and direction kept; MSW spelled out from B0004.", "auditor": {"mode": "bounded-request", "agent_id": "auditor-1"}, **change}

    def write_audit(self, value, name="audit-C01.json"):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def audited_repairs(self, value, name):
        repairs = self.notes("repairs")
        repairs["repairs"][0]["audit_path"] = str(self.write_audit(value, name))
        return repairs

    def record(self, stage, value=None, journal=None):
        raw = json.dumps(self.notes(stage) if value is None else value).encode("utf-8")
        return rs.record_stage(journal or self.journal, self.index, stage, raw)

    def record_through(self, last, journal=None):
        for stage in rs.STAGES[:rs.STAGES.index(last) + 1]:
            self.record(stage, journal=journal)

    def names(self, journal=None):
        return sorted(p.name for p in (journal or self.journal).iterdir())

    def final_review(self):
        decisions = {d["candidate_id"]: d for d in self.notes("decisions")["decisions"]}
        repairs = {r["candidate_id"]: r for r in self.notes("repairs")["repairs"]}
        anchors = {"C01": CRITICAL, "C02": A("B0005", "412 pairs"), "C05": A("B0005", "91 percent")}
        issues = []
        for n, key in enumerate(("C01", "C02", "C05"), 1):
            d, r = decisions[key], repairs[key]
            issues.append({"id": f"W0{n}", "candidate_id": key, "decision_status": d["status"], "anchor": anchors[key],
                           **{f: d[f] for f in ("category", "diagnosis", "severity", "confidence")},
                           **{f: r[f] for f in ("action", "scientific_fidelity", "proposed_wording", "author_question")}})
        issues[0]["evidence"] = [DEFINITION]
        return {"snapshot_id": self.sid, "summary": "Three findings.", "issues": issues}

    def assertRefused(self, message, call, *args):
        before = self.names()
        with self.assertRaisesRegex(rp.ReviewDataError, message):
            call(*args)
        self.assertEqual(self.names(), before)

    def test_happy_path_journal_files_and_chain(self):
        expected = ["00-start.json"]
        self.assertEqual(self.names(), expected)
        for stage in rs.STAGES:
            result = self.record(stage)
            expected = sorted(expected + STAGE_FILES[stage])
            self.assertEqual(self.names(), expected)
            self.assertEqual(result["notes_sha256"], sha(self.journal / STAGE_FILES[stage][0]))
        self.assertEqual(result["next_stage"], "ready")
        receipts = [json.loads((self.journal / STAGE_FILES[s][1]).read_text()) for s in rs.STAGES]
        self.assertEqual([r["previous_receipt_sha256"] for r in receipts[1:]], [r["receipt_sha256"] for r in receipts[:-1]])
        self.assertEqual(receipts[4]["attachments"], [{"path": str(self.root / "audit-C01.json"), "sha256": sha(self.root / "audit-C01.json")}])
        self.assertEqual(json.loads((self.journal / "00-start.json").read_text())["schema_version"], 3)
        self.assertEqual(rs.ready_run(self.journal, self.index)["ready_to_draft"], True)
        self.assertEqual(self.names(), ["00-start.json", "01-reading.notes.json", "01-reading.receipt.json", "02-checks.notes.json", "02-checks.receipt.json",
                                        "03-reconcile.notes.json", "03-reconcile.receipt.json", "04-decisions.notes.json", "04-decisions.receipt.json",
                                        "05-repairs.notes.json", "05-repairs.receipt.json", "06-ready.json"])
        review = self.final_review()
        self.assertEqual(review["issues"][0]["proposed_wording"], "Agreement fell sharply when consecutive measurements were more than two seconds apart.")
        result = rs.check_final(self.journal, self.index, review)
        self.assertEqual((result["command"], result["issue_count"]), ("check-final", 3))
        dropped = self.final_review()
        del dropped["issues"][2]
        self.assertRefused("Final issues must match all accepted/needs_author candidates exactly", rs.check_final, self.journal, self.index, dropped)

    def test_order_is_enforced(self):
        self.assertRefused("Next operation is reading; cannot record checks now.", self.record, "checks")
        self.record("reading")
        self.assertRefused("Next operation is checks; cannot record reading now.", self.record, "reading")
        self.assertRefused("missing stages: checks, reconcile, decisions, repairs\\.", rs.ready_run, self.journal, self.index)
        self.record("checks")
        self.assertRefused("Next operation is reconcile; cannot record decisions now.", self.record, "decisions")
        for stage in ("reconcile", "decisions", "repairs"):
            self.record(stage)
        self.assertRefused("Next operation is ready; cannot record repairs now.", self.record, "repairs")
        self.assertRefused("Run ready successfully before drafting", rs.check_final, self.journal, self.index, self.final_review())

    def test_coverage(self):
        reading = self.notes("reading")
        del reading["paragraphs"][3]
        self.assertRefused("Reading notes must cover each included block exactly once", self.record, "reading", reading)
        with self.assertRaisesRegex(rp.ReviewDataError, "Scope must assign every indexed block exactly once"):
            rs.start_run(self.index, PROFILE, self.scope(omissions=[]), self.root / "other")
        self.assertFalse((self.root / "other").exists())
        self.record("reading")
        checks = self.notes("checks")
        checks["candidates"][2]["anchor"] = A("B0001", "Measuring agreement")
        self.assertRefused("candidate.anchor is outside the declared review scope", self.record, "checks", checks)

    def test_decisions(self):
        self.record_through("reconcile")
        cases = []
        no_reconciled = self.notes("decisions")
        del no_reconciled["decisions"][4]
        cases.append((no_reconciled, "Decisions must account for every candidate exactly once"))
        missing = self.notes("decisions")
        del missing["decisions"][2]
        cases.append((missing, "Decisions must account for every candidate exactly once"))
        to_rejected = self.notes("decisions")
        to_rejected["decisions"][3]["merged_into"] = "C03"
        cases.append((to_rejected, "A merged candidate must point directly to an accepted or needs_author candidate"))
        for field in ("category", "diagnosis", "severity", "confidence"):
            bare = self.notes("decisions")
            del bare["decisions"][0][field]
            cases.append((bare, f"accepted decision is missing fields: {field}\\."))
        for value, message in cases:
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "decisions", value)

    def test_repairs(self):
        self.record_through("decisions")
        only_one = self.notes("repairs")
        del only_one["repairs"][1]
        with_rejected = self.notes("repairs")
        with_rejected["repairs"].append({**with_rejected["repairs"][0], "candidate_id": "C03"})
        wording = self.notes("repairs")
        wording["repairs"][1]["proposed_wording"] = "The 412 pairs were all pairs."
        no_question = self.notes("repairs")
        no_question["repairs"][0].update(kind="author_question", proposed_wording=None)
        for value, message in ((only_one, "Repairs must correspond exactly to accepted and needs_author candidates"),
                               (with_rejected, "Repairs must correspond exactly to accepted and needs_author candidates"),
                               (wording, "needs_author requires an author question, not a definitive replacement"),
                               (no_question, "repair.author_question must be a nonempty string")):
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "repairs", value)

    def test_check_final_links_issues_to_notes(self):
        self.record_through("repairs")
        rs.ready_run(self.journal, self.index)
        diagnosis = self.final_review()
        diagnosis["issues"][0]["diagnosis"] = "MSW is undefined."
        evidence = self.final_review()
        evidence["issues"][0]["evidence"].append(A("B0005", "Agreement in this set"))
        extra = self.final_review()
        extra["issues"].append({**extra["issues"][0], "id": "W09", "candidate_id": "C99"})
        rejected = self.final_review()
        rejected["issues"].append({**rejected["issues"][0], "id": "W04", "candidate_id": "C03", "anchor": A("B0002", "a separate observer")})
        unaudited = self.final_review()
        unaudited["issues"][0]["proposed_wording"] = "Agreement fell when measurements were apart."
        for value, message in ((diagnosis, "Final issue C01.diagnosis differs from the saved decision"),
                               (unaudited, "Final issue C01 changes or omits the saved proposed wording"),
                               (evidence, "Final issue C01 adds evidence not recorded during checking"),
                               (extra, "Final issues must match all accepted/needs_author candidates exactly"),
                               (rejected, "Final issues must match all accepted/needs_author candidates exactly")):
            with self.subTest(message=message):
                self.assertRefused(message, rs.check_final, self.journal, self.index, value)

    def test_tampering_is_detected(self):
        self.record_through("repairs")
        notes = self.journal / "02-checks.notes.json"
        notes.write_bytes(notes.read_bytes() + b" ")
        self.assertRefused("Saved checks notes or their preceding record have changed", rs.ready_run, self.journal, self.index)
        other = self.root / "other"
        rs.start_run(self.index, PROFILE, self.scope(), other)
        self.record("reading", journal=other)
        receipt_path = other / "01-reading.receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["recorded_at"] = "2000-01-01T00:00:00+00:00"
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(rp.ReviewDataError, "reading receipt checksum mismatch; preserve this run"):
            rs.load_run(other, self.index)

    def test_checks_stage_refuses_reader_evidence_and_bad_plans(self):
        self.record("reading")
        cases = [({**self.notes("checks"), key: []}, "Reader observations belong to the reconcile stage") for key in ("checkpoints", "branches", "observations")]
        for anchor, mode, message in ((A("B0001", "Measuring agreement"), "fresh-prefix", "checkpoint_plan.anchor is outside the declared review scope"),
                                      (CRITICAL, "masked", "checkpoint_plan.mode must be one of: bounded-request, fresh-prefix, full-context, masked-sequential\\.")):
            checks = self.notes("checks")
            checks["checkpoint_plan"][0].update(anchor=anchor, mode=mode)
            cases.append((checks, message))
        for value, message in cases:
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "checks", value)

    def test_start_receipt_schema_version(self):
        path = self.journal / "00-start.json"
        original = json.loads(path.read_text())
        path.write_text(json.dumps({**original, "schema_version": 2}))
        self.assertRefused("start receipt checksum mismatch; preserve this run and start a new run", self.record, "reading")
        path.write_text(json.dumps(rs.sealed({**{k: v for k, v in original.items() if k != "receipt_sha256"}, "schema_version": 2})))
        self.assertRefused("Unsupported journal schema; use the helper version that wrote this journal", self.record, "reading")

    def prefix_reconcile(self, profile=PROFILE, index=None, **change):
        packet = rp.build_packet(index or self.index, profile, stop_after_text="so we restricted the analysis to pairs within that window.")
        observation = {k: packet[k] for k in ("snapshot_id", "checkpoint_id", "prefix_sha256", "packet_sha256")}
        observation.update(understanding="MSW is undefined.", issues=[])
        packet_path, obs_path = self.root / "packet.json", self.root / "obs.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        obs_path.write_text(json.dumps(observation), encoding="utf-8")
        reconcile = self.notes("reconcile")
        reconcile["checkpoints"] = [{"id": "K1", "plan_id": "P1", "anchor": CRITICAL, "mode": "fresh-prefix", "result": "asked what MSW is",
                                     "limitation": "none", "packet_path": str(packet_path), "observation_path": str(obs_path), "figure_policy": "text-only", **change}]
        return reconcile, packet_path, obs_path

    def test_fresh_prefix_checkpoint_cites_its_plan(self):
        self.record_through("checks")
        unplanned = "A prefix checkpoint must cite a checks-stage plan entry with the same anchor and mode"
        for change, message in (({"profile": {"audience": "someone else"}}, "Checkpoint packet uses a different reader profile"),
                                ({"index": rp.build_index(TEXT.replace("independent records", "separate records"))}, "Checkpoint packet is not an exact prefix of this journal's source"),
                                ({"plan_id": None}, "checkpoint.plan_id must be a nonempty string"), ({"plan_id": "P9"}, unplanned),
                                ({"anchor": A("B0003", "two seconds")}, unplanned), ({"mode": "bounded-request"}, unplanned)):
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "reconcile", self.prefix_reconcile(**change)[0])
        absent, _, _ = self.prefix_reconcile()
        del absent["checkpoints"][0]["plan_id"]
        self.assertRefused("checkpoint.plan_id must be a nonempty string", self.record, "reconcile", absent)
        reconcile, packet_path, obs_path = self.prefix_reconcile()
        self.record("reconcile", reconcile)
        receipt = json.loads((self.journal / "03-reconcile.receipt.json").read_text())
        self.assertEqual(receipt["attachments"], [{"path": str(packet_path), "sha256": sha(packet_path)},
                                                  {"path": str(obs_path), "sha256": sha(obs_path)}])

    def masked_run(self, name="mrun", texts=None, branch=False, policy="reference-jump"):
        texts = texts or [self.text[b] for b in INCLUDED]
        ids = [f"M{n}" for n in range(2, 2 + len(texts))]
        case = {"id": "m", "blocks": [{"id": i, "text": t, "page": 1, "kind": "paragraph"} for i, t in zip(ids, texts)],
                "windows": [[ids[0]], [ids[1]], ids[2:]], "asset_policy": policy}
        (self.root / f"{name}-case.json").write_bytes(mr.encoded(case))
        (self.root / f"{name}-profile.json").write_bytes(mr.encoded({"task": "Read in order.", "memory_limit": 180}))
        run = self.root / name
        mr.start(self.root / f"{name}-case.json", self.root / f"{name}-profile.json", run)
        recognition = {"recognized": False, "description": ""}
        for seq in (1, 2):
            response = {"understanding": f"window {seq}", "understood": [], "uncertain": [], "resolved": [], "source_recognition": recognition,
                        "next_action": {"kind": "advance", "target": None, "reason": "go on"}, "carry_memory": {"gist": f"window {seq}", "notes": [], "dropped": []}}
            mr.packet(run, self.root / f"{name}-packet-{seq}.json")
            (self.root / f"{name}-response-{seq}.json").write_bytes(mr.encoded(response))
            mr.record(run, self.root / f"{name}-packet-{seq}.json", self.root / f"{name}-response-{seq}.json", f"w{seq}")
        if branch:
            (self.root / f"{name}-question.json").write_bytes(mr.encoded({"id": "Q1", "question": "What is the MSW?", "selected_with_full_text": True, "reason": "B0004 defines it later."}))
            mr.branch(run, 2, self.root / f"{name}-question.json", self.root / f"{name}-branch.json")
            (self.root / f"{name}-answer.json").write_bytes(mr.encoded({"answer": "Only a threshold is given.", "support": "undetermined", "anchors": [{"source_id": "M3", "quote": "the MSW exceeded two seconds"}],
                                                                         "inference": "", "background_used": "", "undetermined": "what the MSW measures", "source_recognition": recognition}))
            mr.branch_record(run, self.root / f"{name}-branch.json", self.root / f"{name}-answer.json", "b1")
        return run

    def masked_reconcile(self, run, quote_anchor=CRITICAL, sequence=2, **change):
        reconcile = self.notes("reconcile")
        reconcile["checkpoints"] = [{"id": "K2", "plan_id": None, "anchor": quote_anchor, "mode": "masked-sequential", "result": "carried an undefined MSW",
                                     "limitation": "none", "run_path": str(run), "record_sequence": sequence, "figure_policy": "reference-jump", **change}]
        return reconcile

    def test_masked_sequential_attachment(self):
        run = self.masked_run()
        self.record_through("checks")
        self.assertRefused("not text the masked reader had seen by that display event", self.record, "reconcile", self.masked_reconcile(run, A("B0004", "Measurement Separation Window")))
        self.assertRefused("had seen", self.record, "reconcile", self.masked_reconcile(run, A("B0004", "two seconds")))
        self.assertRefused("record_sequence must name a recorded display event, 1 to 2\\.", self.record, "reconcile", self.masked_reconcile(run, sequence=3))
        self.assertRefused("A prefix checkpoint must cite a checks-stage plan entry", self.record, "reconcile", self.masked_reconcile(run, plan_id="P1"))
        foreign = self.masked_run("foreign", ["An unrelated opening.", "the MSW exceeded two seconds", "Other text."])
        self.assertRefused("Masked case block M2 is not text of this journal's source", self.record, "reconcile", self.masked_reconcile(foreign))
        self.record("reconcile", self.masked_reconcile(run))
        receipt = json.loads((self.journal / "03-reconcile.receipt.json").read_text())
        files = [run / "manifest.json"] + [run / "records" / "000002" / n for n in ("packet.json", "response.json", "receipt.json")]
        self.assertEqual(receipt["attachments"], [{"path": str(p), "sha256": sha(p)} for p in files])
        response = run / "records" / "000002" / "response.json"
        response.write_bytes(response.read_bytes() + b" ")
        self.assertRefused("Masked run does not validate: Recorded input or response changed", self.record, "decisions")

    def test_checkpoint_figure_policy_matches_what_the_reader_could_see(self):
        run = self.masked_run()
        strict = self.masked_run("strict", policy="strict-prefix")
        self.record_through("checks")
        missing = self.masked_reconcile(run)
        del missing["checkpoints"][0]["figure_policy"]
        text_only = "A prefix checkpoint carries no figures; its figure_policy must be text-only\\."
        for value, message in ((missing, "checkpoint is missing fields: figure_policy\\."),
                               (self.masked_reconcile(run, figure_policy="all"), "checkpoint\\.figure_policy must be one of: reference-jump, strict-prefix, text-only\\."),
                               (self.prefix_reconcile(figure_policy="reference-jump")[0], text_only),
                               (self.prefix_reconcile(figure_policy="strict-prefix")[0], text_only),
                               (self.masked_reconcile(run, figure_policy="text-only"), "checkpoint\\.figure_policy must equal the masked run's asset_policy \\(reference-jump\\)\\."),
                               (self.masked_reconcile(strict, figure_policy="reference-jump"), "checkpoint\\.figure_policy must equal the masked run's asset_policy \\(strict-prefix\\)\\.")):
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "reconcile", value)
        bounded = self.prefix_reconcile(mode="bounded-request", figure_policy="reference-jump")[0]
        self.assertRefused(text_only, self.record, "reconcile", bounded)
        self.record("reconcile", self.masked_reconcile(strict, figure_policy="strict-prefix"))
        receipt = json.loads((self.journal / "03-reconcile.receipt.json").read_text())
        files = [strict / "manifest.json"] + [strict / "records" / "000002" / n for n in ("packet.json", "response.json", "receipt.json")]
        self.assertEqual(receipt["attachments"], [{"path": str(p), "sha256": sha(p)} for p in files])

    def test_full_context_checkpoint_may_declare_any_figure_policy(self):
        for policy in ("strict-prefix", "reference-jump", "text-only"):
            with self.subTest(policy=policy):
                journal = self.root / f"full-{policy}"
                rs.start_run(self.index, PROFILE, self.scope(), journal)
                self.record_through("checks", journal)
                reconcile = self.notes("reconcile")
                reconcile["checkpoints"] = [{"id": "K3", "anchor": CRITICAL, "mode": "full-context", "result": "editor reread with figures", "limitation": "not a fresh reader", "figure_policy": policy}]
                self.assertEqual(self.record("reconcile", reconcile, journal)["next_stage"], "decisions")
                saved = json.loads((journal / "03-reconcile.notes.json").read_text())
                receipt = json.loads((journal / "03-reconcile.receipt.json").read_text())
                self.assertEqual((saved["checkpoints"][0]["figure_policy"], receipt["attachments"]), (policy, []))

    def test_repair_wording_requires_a_preserving_fidelity_audit(self):
        self.record_through("decisions")
        checks = self.audit()["checks"]
        refused = "^Repair C01 cannot carry proposed wording: the fidelity audit verdict is "
        cases = [
            (self.audit(candidate_id="C05"), "Fidelity audit at audit-1\\.json is for 'C05', not C01\\."),
            (self.audit(proposed_wording=WORDING + " "), "Fidelity audit for C01 audited different wording than the repair proposes\\."),
            (self.audit(original=[]), "audit\\.original must quote the source text the wording replaces\\."),
            (self.audit(original=[A("B0003", "Agreement fell slowly")]), "audit\\.original\\[0\\]\\.quote is not an exact substring of its supplied block\\."),
            (self.audit(original=[ORIGINAL, A("B0005", "412 pairs")]), "audit\\.original\\[1\\] quotes a block outside the repair's affected_block_ids\\."),
            (self.audit(verdict="probably"), "audit\\.verdict must be one of: changed, preserved, undetermined\\."),
            (self.audit(checks={k: v for k, v in checks.items() if k != "uncertainty"}), "audit\\.checks is missing fields: uncertainty\\."),
            (self.audit(checks={**checks, "tone": "same"}), "audit\\.checks has unsupported fields: tone\\."),
            (self.audit(checks={**checks, "quantifiers": "similar"}), "audit\\.checks\\.quantifiers must be one of: changed, not_applicable, same\\."),
            (self.audit(notes=" "), "audit\\.notes must be a nonempty string\\."),
            (self.audit(auditor={"mode": "masked-sequential", "agent_id": "auditor-1"}), "audit\\.auditor\\.mode must be one of: bounded-request, fresh-prefix, full-context\\."),
            (self.audit(auditor={"mode": "fresh-prefix", "agent_id": ""}), "audit\\.auditor\\.agent_id must be a nonempty string\\."),
            ({k: v for k, v in self.audit().items() if k != "auditor"}, "fidelity audit is missing fields: auditor\\."),
            (self.audit(verdict="changed"), refused + "changed\\. Revise the wording and audit again, or set proposed_wording to null and ask the author\\.$"),
            (self.audit(verdict="undetermined"), refused + "undetermined\\. Revise"),
            (self.audit(checks={**checks, "numerical_values": "changed", "population": "changed"}), refused + "preserved with changed population, numerical_values\\. Revise"),
        ]
        for n, (value, message) in enumerate(cases, 1):
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "repairs", self.audited_repairs(value, f"audit-{n}.json"))
        unaudited = self.notes("repairs")
        del unaudited["repairs"][0]["audit_path"]
        idle = self.notes("repairs")
        idle["repairs"][1]["audit_path"] = str(self.audit_path)
        for value, message in ((unaudited, "repair\\.audit_path must be a nonempty string\\."),
                               (idle, "A repair without proposed wording has nothing to audit; leave audit_path null\\.")):
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "repairs", value)
        self.assertEqual(self.record("repairs")["next_stage"], "ready")

    def test_fidelity_audit_with_an_unlisted_field_is_refused(self):
        self.record_through("decisions")
        repairs = self.audited_repairs(self.audit(reviewer_comment="looks fine"), "audit-extra.json")
        self.assertRefused("fidelity audit has unsupported fields: reviewer_comment\\.", self.record, "repairs", repairs)

    def test_audit_changed_after_recording_is_detected(self):
        self.record_through("repairs")
        self.write_audit(self.audit(notes="Rewritten after the repairs were saved."))
        self.assertRefused("A saved checkpoint attachment has changed\\.", rs.ready_run, self.journal, self.index)
        self.write_audit(self.audit(verdict="changed"))
        self.assertRefused("^Repair C01 cannot carry proposed wording: the fidelity audit verdict is changed\\.", rs.ready_run, self.journal, self.index)

    def branch_reconcile(self, run):
        reconcile = self.notes("reconcile")
        reconcile["branches"] = [{"id": "R1", "run_path": str(run), "branch_sequence": 1, "result": "undetermined at event 2", "limitation": "question chosen with full text"}]
        observation = {"id": "O1", "source_id": "R1", "reader_question": "What is the MSW?", "classification": "absent_at_critical_use",
                       "editor_check": "B0004 defines it after B0003.", "evidence": [DEFINITION], "candidate_id": "C01"}
        reconcile["observations"] = [observation, {**observation, "id": "O2", "classification": "adequate", "evidence": [], "candidate_id": "C05"}]
        return reconcile

    def test_branch_attachment_and_observations(self):
        run = self.masked_run(branch=True)
        self.record_through("checks")
        for edit, message in ((lambda r: r["branches"][0].update(branch_sequence=2), "branch.branch_sequence must name a recorded branch, 1 to 1\\."),
                              (lambda r: r["observations"][0].update(source_id="K9"), "observation.source_id must name a checkpoint or branch recorded in this stage"),
                              (lambda r: r["observations"][1].update(classification="confusing"), "observation.classification must be one of: absent_at_critical_use, adequate, asset_lookup, inferred_connection, interpretation_changed, lost_in_notes, needed_again, resolved_later\\."),
                              (lambda r: r["observations"][1].update(candidate_id="C99"), "observation.candidate_id must name a candidate from checks or this stage"),
                              (lambda r: r["candidates"][0].update(id="C01"), "Reconcile candidates must use ids not already used in checks"),
                              (lambda r: r.update(checkpoints=self.masked_reconcile(run, id="R1")["checkpoints"]), "Checkpoint and branch ids must not collide")):
            reconcile = self.branch_reconcile(run)
            edit(reconcile)
            with self.subTest(message=message):
                self.assertRefused(message, self.record, "reconcile", reconcile)
        self.record("reconcile", self.branch_reconcile(run))
        receipt = json.loads((self.journal / "03-reconcile.receipt.json").read_text())
        files = [run / "manifest.json"] + [run / "branches" / "000001" / n for n in ("packet.json", "response.json", "receipt.json")]
        self.assertEqual(receipt["attachments"], [{"path": str(p), "sha256": sha(p)} for p in files])
        response = run / "branches" / "000001" / "response.json"
        response.write_bytes(response.read_bytes() + b" ")
        self.assertRefused("Masked run branches do not validate: Branch record changed", self.record, "decisions")

    def test_isolated_cli_start_from_other_cwd(self):
        for name, value in (("index.json", self.index), ("profile.json", PROFILE), ("scope.json", self.scope())):
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")
        cwd = self.root / "elsewhere"
        cwd.mkdir()
        out = self.root / "cli-journal"
        done = subprocess.run([sys.executable, "-B", "-I", str(SCRIPTS / "review_steps.py"), "start", "--index", str(self.root / "index.json"),
                               "--profile", str(self.root / "profile.json"), "--scope", str(self.root / "scope.json"), "--out", str(out)],
                              cwd=cwd, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"ok": True, "command": "start", "snapshot_id": self.sid, "next_stage": "reading", "journal": str(out)})
        self.assertEqual(self.names(out), ["00-start.json"])
        self.assertEqual(sorted(p.name for p in cwd.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
