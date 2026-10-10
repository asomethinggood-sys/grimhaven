"""Round 7 — the boot gate, the reset tool and the CI validator.

The outage was only half an application bug: the pipeline happily hosted a bot
that refused to serve anyone, and the "restore" step counted *unpacking a zip* as
a successful restore. These tests cover the three pieces that now stand between
a broken artifact and a player-facing failure:

* ``deploy/validate_db.py``  — is this file a usable, seeded world database?
* ``scripts/reset_world.py`` — wipe the mutable state, rebuild, *verify*, and
  capture the evidence before anything is destroyed
* ``run_bot.py``             — logging first, integrity report at boot, and a
  refusal to boot against a vault that already failed its check
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from grimhaven.db.storage import Storage, bootstrap_world  # noqa: E402
from grimhaven.engine.models import new_user_doc  # noqa: E402


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_db", ROOT / "deploy" / "validate_db.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


validate_db = _load_validator()


def make_world_db(path: Path, users: int = 0) -> Path:
    storage = Storage(path)
    bootstrap_world(storage)
    for i in range(users):
        storage.save_user(new_user_doc(i + 1, f"p{i}", "fa"))
    storage.close()
    return path


# ── the CI validator ─────────────────────────────────────────────────────────

def test_validator_accepts_a_seeded_world(tmp_path):
    report = validate_db.inspect_db(make_world_db(tmp_path / "world.db", users=3))
    assert report["ok"], report
    assert report["counts"]["users"] == 3 and report["counts"]["zones"] == 7


def test_validator_flags_a_world_with_no_zones(tmp_path):
    """An empty vault is technically intact and completely useless: every map,
    travel and hunt screen would come back blank."""
    # Storage() alone = the right schema with no world in it (bootstrap_world
    # is what seeds zones/sects), which is what a half-restored artifact looks like
    path = Storage(tmp_path / "empty.db").path
    Storage(path).close()
    report = validate_db.inspect_db(path)
    assert not report["ok"]
    assert any("no zones seeded" in problem for problem in report["problems"])


@pytest.mark.parametrize("content", [
    b"",                                        # truncated download
    b"not a database at all",                  # a zip renamed to .db
    os.urandom(4096),                           # binary garbage
    b"SQLite format 3\x00" + b"\x00" * 512,     # right header, no content
])
def test_validator_rejects_anything_that_is_not_a_database(tmp_path, content):
    path = tmp_path / "bad.db"
    path.write_bytes(content)
    report = validate_db.inspect_db(path)
    assert not report["ok"], f"{content[:24]!r} was accepted as a world database"
    assert report["problems"], "a rejection must say why"


def test_validator_reports_quarantined_documents(tmp_path):
    path = make_world_db(tmp_path / "world.db")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO corrupt_users(user_id, raw, reason) "
                     "VALUES(9,'{oops','not a JSON object')")
    report = validate_db.inspect_db(path)
    assert report["ok"], "quarantined rows do not make the file unusable"
    assert report["counts"]["corrupt_users"] == 1
    assert any("quarantined" in warn for warn in report.get("warnings", []))


def test_validator_accepts_a_wal_database_whose_sidecars_are_gone(tmp_path):
    """The restore step unpacks only the .db. Rejecting a healthy WAL file for
    missing sidecars would turn every deploy into a 'fresh world'."""
    path = make_world_db(tmp_path / "world.db", users=2)
    for sidecar in ("-wal", "-shm"):
        (tmp_path / f"world.db{sidecar}").unlink(missing_ok=True)
    assert validate_db.inspect_db(path)["ok"]


def test_validator_exit_codes(tmp_path):
    good = make_world_db(tmp_path / "world.db")
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"garbage")
    def run(*args):
        return subprocess.run([sys.executable, str(ROOT / "deploy" / "validate_db.py"),
                               *args], capture_output=True, text=True, cwd=ROOT)
    assert run(str(good)).returncode == 0
    assert run(str(bad)).returncode == 1
    assert run(str(tmp_path / "nothing.db")).returncode == 2
    payload = json.loads(run(str(good), "--json").stdout)
    assert payload["ok"] and payload["counts"]["zones"] == 7


# ── the reset tool ───────────────────────────────────────────────────────────

def _reset(*args, env=None):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "reset_world.py"),
                           *args], capture_output=True, text=True, cwd=ROOT,
                          env={**os.environ, **(env or {})})
    return proc


def test_reset_is_a_no_unless_you_say_yes(tmp_path):
    path = make_world_db(tmp_path / "world.db", users=2)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO users(user_id, doc) VALUES(501,'{broken')")
    proc = _reset("--db", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DRY RUN" in proc.stdout
    storage = Storage(path)
    try:
        assert storage.count("users") == 3, "a dry run must not wipe anything"
        assert storage.get_user(1) is not None and storage.get_user(501) is None
    finally:
        storage.close()


def test_reset_wipes_rebuilds_and_verifies(tmp_path):
    path = make_world_db(tmp_path / "world.db", users=2)
    with sqlite3.connect(path) as conn:                      # poison one row
        conn.execute("INSERT INTO users(user_id, doc) VALUES(999,'{broken')")
    audit = tmp_path / "audit.json"
    proc = _reset("--yes", "--db", str(path), "--keep-audit", str(audit))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "world rebuilt and verified" in proc.stdout
    report = json.loads(audit.read_text(encoding="utf-8"))
    assert report["dry_run"] is False
    assert report["scan"]["quarantined"] == 1, "the damage must be captured first"
    assert report["before"]["counts"]["users"] == 3
    assert report["report"]["after"]["counts"]["users"] == 0
    assert report["report"]["after"]["counts"]["zones"] == 7
    assert report["problems"] == []
    # the mutable state is gone but the world is playable
    storage = Storage(path)
    try:
        assert storage.count("zones") == 7 and storage.count("sects") == 3
        assert storage.count("corrupt_users") == 0
    finally:
        storage.close()


def test_reset_inspect_lists_the_evidence(tmp_path):
    path = make_world_db(tmp_path / "world.db")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO corrupt_users(user_id, raw, reason) "
                     "VALUES(42,'prefix-bytes','not a JSON object')")
    proc = _reset("--inspect", "--db", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "user_id=42" in proc.stdout and "prefix-bytes" in proc.stdout


def test_reset_never_touches_static_game_content(tmp_path):
    """The user's rule: data/*.json and locales/* are the rebuild source, not
    part of the mutable state."""
    world = ROOT / "data"
    locales = ROOT / "locales"
    fingerprint = {p: p.stat().st_mtime_ns for p in list(world.glob("*.json"))
                   + list(locales.glob("*.json"))}
    path = make_world_db(tmp_path / "world.db", users=1)
    proc = _reset("--yes", "--db", str(path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert {p: p.stat().st_mtime_ns for p in fingerprint} == fingerprint


# ── the boot gate ────────────────────────────────────────────────────────────

_BOOT = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, {root!r})
    import grimhaven.bot.app as app_mod

    class FakeApp:
        def run_polling(self, **kwargs):
            print("POLLED:", sorted(kwargs))
        def run_webhook(self, **kwargs):
            print("WEBHOOK")

    app_mod.build_application = lambda settings, storage: FakeApp()
    import run_bot
    sys.exit(run_bot.main())
''')


def _boot(db: Path, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _BOOT.format(root=str(ROOT))],
        capture_output=True, text=True, cwd=ROOT,
        env={**os.environ, "TELEGRAM_BOT_TOKEN": "123:test",
             "DATABASE_PATH": str(db), **env})


def test_boot_refuses_a_corrupt_database_and_says_how_to_fix_it(tmp_path):
    db = tmp_path / "corrupt.db"
    make_world_db(db, users=400)
    size = db.stat().st_size
    with open(db, "r+b") as fh:                  # blank the middle of the file
        fh.seek(size // 2)
        fh.write(b"\x00" * (size // 8))
    proc = _boot(db)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "refusing to boot" in proc.stdout
    assert "GRIMHAVEN_RESET_WORLD=1 python run_bot.py" in proc.stdout
    assert "database disk image is malformed" in proc.stdout + proc.stderr
    # the boot log must already carry the structured report before the refusal
    assert "database:" in proc.stdout and "journal=" in proc.stdout


def test_boot_reports_integrity_then_serves(tmp_path):
    db = make_world_db(tmp_path / "world.db", users=3)
    proc = _boot(db)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "world ready — 3 cultivator(s)" in proc.stdout
    assert "POLLED: ['drop_pending_updates']" in proc.stdout
    assert "boot: db=" in proc.stdout and "log_level=INFO" in proc.stdout


def test_boot_reset_switch_rebuilds_before_serving(tmp_path):
    db = tmp_path / "world.db"
    make_world_db(db, users=5)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO corrupt_users(user_id, raw, reason) "
                     "VALUES(7,'x','not a JSON object')")
    proc = _boot(db, GRIMHAVEN_RESET_WORLD="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "world reset" in proc.stdout
    assert "world ready — 0 cultivator(s)" in proc.stdout
    storage = Storage(db)
    try:
        assert storage.count("users") == 0 and storage.count("zones") == 7
    finally:
        storage.close()


def test_logging_setup_makes_the_handler_levels_visible(tmp_path):
    """python-telegram-bot v20 stopped calling basicConfig for you. Without our
    own setup, the incident line lands on logging.lastResort: no timestamp, no
    level, and everything below WARNING simply disappears from the Actions log."""
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, logging
            sys.path.insert(0, {str(ROOT)!r})
            import run_bot
            run_bot._configure_logging("DEBUG")
            log = logging.getLogger("grimhaven.bot.handlers.reporting")
            log.debug("probe-debug")
            log.error("probe-error")
            run_bot._configure_logging("WARNING")
            log.debug("probe-after")
        """)], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "probe-debug" in proc.stdout, "DEBUG must reach the app loggers"
    assert "probe-after" not in proc.stdout, "the level must actually be applied"
    line = [ln for ln in proc.stdout.splitlines() if "probe-error" in ln][0]
    assert "ERROR" in line and "grimhaven.bot.handlers.reporting" in line
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", line), \
        f"a log line without a timestamp cannot be matched to a screenshot: {line!r}"


def test_boot_still_rejects_a_missing_token(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT.format(root=str(ROOT))],
        capture_output=True, text=True, cwd=ROOT,
        env={**os.environ, "TELEGRAM_BOT_TOKEN": "",
             "DATABASE_PATH": str(tmp_path / "none.db")})
    assert proc.returncode == 1
    assert "TELEGRAM_BOT_TOKEN is not set" in proc.stdout


# ── the shell wiring the workflow actually runs ──────────────────────────────

@pytest.mark.parametrize("script", ["deploy/gha_state.sh", "deploy/gha_state.py"])
def test_deploy_scripts_parse(script):
    path = ROOT / script
    if not path.exists():
        pytest.skip(f"{script} is not part of this deployment")
    proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_gha_state_check_reports_the_live_database(tmp_path):
    good = make_world_db(tmp_path / "world.db", users=2)
    ok = subprocess.run(["bash", str(ROOT / "deploy" / "gha_state.sh"), "check",
                         str(good)], capture_output=True, text=True, cwd=ROOT)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "seeded and intact" in ok.stdout

    bad = tmp_path / "bad.db"
    bad.write_bytes(b"PK\x03\x04 this is not a database")
    fail = subprocess.run(["bash", str(ROOT / "deploy" / "gha_state.sh"), "check",
                           str(bad)], capture_output=True, text=True, cwd=ROOT)
    assert fail.returncode == 1, fail.stdout


def test_restore_validates_and_can_be_told_to_fail_loudly(tmp_path):
    """RESET_ON_CORRUPT=0 must fail the step instead of quietly serving a world
    nobody built; the default re-seeds and preserves the evidence."""
    db = tmp_path / "grimhaven.db"
    db.write_bytes(b"garbage that is not sqlite")
    strict = subprocess.run(["bash", str(ROOT / "deploy" / "gha_state.sh"), "validate"],
                            capture_output=True, text=True, cwd=ROOT,
                            env={**os.environ, "DATABASE_PATH": str(db),
                                 "RESET_ON_CORRUPT": "0"})
    assert strict.returncode == 1, strict.stdout
    assert "not usable" in strict.stdout
    assert "failing the run" in strict.stdout, "the operator must see the knob it honoured"
    assert db.exists(), "REFUSE mode must not destroy the file"
