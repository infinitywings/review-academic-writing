"""setup_mineru.sh against stub uv / mineru / mineru-kit executables.

The stubs record every call in CALLS and answer from a key=value STATE file that the
mutating commands update, so the tests can assert which commands ran, in which order,
and that the read-only --check mode changes nothing.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_mineru.sh"

STUB = r'''#!/usr/bin/env bash
# $0 is uv, mineru or mineru-kit; STATE and CALLS come from the environment.
name="$(basename "$0")"
echo "$name $*" >> "$CALLS"
get() { sed -n "s/^$1=//p" "$STATE" | head -1; }
set_() { grep -v "^$1=" "$STATE" > "$STATE.tmp"; echo "$1=$2" >> "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; }
case "$name $*" in
  "uv tool install"*)
    mkdir -p "$HOME/.local/bin"
    for n in mineru mineru-kit; do cp "$STUB_SRC" "$HOME/.local/bin/$n"; chmod +x "$HOME/.local/bin/$n"; done
    set_ installed 1 ;;
  "mineru --version") echo "MinerU version: $(get version)" ;;
  "mineru-kit models verify"*) [ "$(get models)" = 1 ] ;;
  "mineru-kit models download"*) set_ models 1 ;;
  "mineru telemetry status") echo "state: $(get telemetry)" ;;
  "mineru telemetry disable") set_ telemetry disabled ;;
  "mineru config get parse_server.local.mode") echo "parse_server.local.mode = $(get mode)  [override]" ;;
  "mineru config set parse_server.local.mode managed") set_ mode managed ;;
  "mineru server start") set_ running 1; set_ healthy 1 ;;
  "mineru server restart") set_ running 1; set_ healthy 1; set_ restarted 1 ;;
  "mineru server status --json")
    r=false; h=false; [ "$(get running)" = 1 ] && r=true; [ "$(get healthy)" = 1 ] && h=true
    printf '{"running": %s, "parse_server": {"local": {"mode": "%s", "healthy": %s, "starting": false}}}\n' "$r" "$(get mode)" "$h" ;;
  *) echo "stub: unexpected $name $*" >&2; exit 9 ;;
esac
'''


class SetupMineruTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"; self.home.mkdir()
        self.bin = root / "bin"; self.bin.mkdir()
        self.state = root / "state"; self.calls = root / "calls"
        self.stub_src = root / "stub.sh"; self.stub_src.write_text(STUB)
        self.stub_src.chmod(self.stub_src.stat().st_mode | stat.S_IXUSR)
        self.calls.write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def install_stub(self, where: Path, *names: str):
        for n in names:
            (where / n).write_text(STUB)
            (where / n).chmod(0o755)

    def write_state(self, **kv):
        self.state.write_text("".join(f"{k}={v}\n" for k, v in kv.items()))

    def run_script(self, *args: str):
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "STATE": str(self.state), "CALLS": str(self.calls), "STUB_SRC": str(self.stub_src),
        }
        return subprocess.run(["bash", str(SCRIPT), "--wait", "0", *args], capture_output=True, text=True, env=env)

    def calls_made(self):
        return [line for line in self.calls.read_text().splitlines() if line]

    def test_missing_uv_exits_2_with_hint_and_runs_nothing(self):
        self.write_state(version="4.0.12")
        result = self.run_script()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("uv: missing", result.stdout)
        self.assertEqual(self.calls_made(), [])

    def test_fresh_machine_installs_downloads_configures_and_starts_in_order(self):
        self.install_stub(self.bin, "uv")
        self.write_state(version="4.0.12", models="0", telemetry="enabled", mode="off", running="0", healthy="0")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls_made()
        self.assertEqual(calls[0], "uv tool install mineru==4.0.12 --python 3.12")
        mutating = [c for c in calls if c in (
            "mineru-kit models download --tier standard", "mineru telemetry disable",
            "mineru config set parse_server.local.mode managed", "mineru server start", "mineru server restart")]
        self.assertEqual(mutating, [
            "mineru-kit models download --tier standard", "mineru telemetry disable",
            "mineru config set parse_server.local.mode managed", "mineru server start"])
        self.assertIn("ready: local MinerU parse server is healthy (setup made changes)", result.stdout)
        self.assertTrue((self.home / ".local" / "bin" / "mineru").exists())
        self.assertNotIn("--remote", self.calls.read_text())

    def test_ready_machine_only_reads_state(self):
        self.install_stub(self.bin, "uv", "mineru", "mineru-kit")
        self.write_state(version="4.0.12", models="1", telemetry="disabled", mode="managed", running="1", healthy="1")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(set(self.calls_made())), [
            "mineru --version", "mineru config get parse_server.local.mode",
            "mineru server status --json", "mineru telemetry status",
            "mineru-kit models verify --tier standard"])
        self.assertIn("(nothing to do)", result.stdout)

    def test_mode_change_restarts_a_running_server(self):
        self.install_stub(self.bin, "uv", "mineru", "mineru-kit")
        self.write_state(version="4.0.12", models="1", telemetry="disabled", mode="off", running="1", healthy="1")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls_made()
        self.assertIn("mineru config set parse_server.local.mode managed", calls)
        self.assertIn("mineru server restart", calls)
        self.assertNotIn("mineru server start", calls)

    def test_check_mode_reports_without_changing_anything(self):
        self.install_stub(self.bin, "uv", "mineru", "mineru-kit")
        self.write_state(version="4.0.12", models="0", telemetry="enabled", mode="off", running="0", healthy="0")
        before = self.state.read_text()
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.state.read_text(), before)
        for line in ("models (standard): missing or incomplete (would run", "telemetry: enabled or unset (would run",
                     "parse_server.local.mode: not managed (would run", "server: stopped", "not ready: server is stopped"):
            self.assertIn(line, result.stdout)
        self.assertFalse(any(c.startswith(("uv tool install", "mineru-kit models download", "mineru telemetry disable",
                                           "mineru config set", "mineru server start", "mineru server restart"))
                             for c in self.calls_made()))

    def test_check_mode_on_a_ready_machine_exits_0(self):
        self.install_stub(self.bin, "uv", "mineru", "mineru-kit")
        self.write_state(version="4.0.12", models="1", telemetry="disabled", mode="managed", running="1", healthy="1")
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ready: local MinerU parse server is healthy", result.stdout)

    def test_other_installed_version_is_reported_not_replaced(self):
        self.install_stub(self.bin, "uv", "mineru", "mineru-kit")
        self.write_state(version="4.1.0", models="1", telemetry="disabled", mode="managed", running="1", healthy="1")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("installed version differs from the tested 4.0.12; leaving it as is", result.stdout)
        self.assertNotIn("uv tool install", self.calls.read_text())


if __name__ == "__main__":
    unittest.main()
