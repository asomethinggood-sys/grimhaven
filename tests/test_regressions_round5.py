"""Round-5 regressions: stale dock keyboards and world-state persistence.

Both bugs reported from the live bot:
  * players tapped the persistent dock and got NO reply at all — Telegram keeps
    a reply keyboard on the client, so a label renamed in a later release
    stopped matching and `on_reply_button` silently returned;
  * `actions/artifacts` was empty, i.e. the SQLite world state was never being
    saved, so every runner restart wiped every player's progress while the run
    still reported success.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grimhaven.bot.handlers import commands as cmds  # noqa: E402
from grimhaven.bot.handlers.common import Ctx  # noqa: E402
from grimhaven.bot.keyboards import REPLY_TO_ACTION, resolve_dock_action  # noqa: E402
from grimhaven.core.data_loader import bootstrap as bootstrap_data  # noqa: E402
from grimhaven.core.middleware import guard_update  # noqa: E402
from grimhaven.db.storage import Storage, bootstrap_world  # noqa: E402
from grimhaven.engine.models import ensure_v2, new_user_doc  # noqa: E402

# The exact labels on the player's phone in the bug report — from an older
# release than HEAD, so none of them are in REPLY_TO_ACTION any more.
STALE_LABELS = {
    "🧘 مدیتیشن و تهدید": "cultivate:view:hub",
    "⚡️ اقدام به شکست سد": "breakthrough:view:prep",
    "🎒 کوله‌پیشی و گنجینه": "bag:tab:gear:1",
    "📜 لوح سرنوشت (پروفایل)": "profile:view:main",
    "🗺 نقشه و شکار": "map:view:world",
    "🏛 پاویون تجارت": "shop:view:hub",
    "⛩ فرقه": "sect:view:main",
    "⚙️ تنظیمات": "settings:view:main",
}


# ── dock resolution ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,expected", sorted(STALE_LABELS.items()))
def test_stale_dock_labels_still_resolve(label: str, expected: str) -> None:
    """A keyboard left over from an older release must not become a dead tap."""
    assert resolve_dock_action(label) == expected


def test_stale_labels_really_are_not_in_the_exact_table() -> None:
    """Guards the premise: these labels are NOT plain REPLY_TO_ACTION keys."""
    drifted = [lb for lb in STALE_LABELS if lb not in REPLY_TO_ACTION]
    assert drifted, "expected at least one drifted label to prove the fix matters"


def test_current_dock_labels_resolve() -> None:
    """Every label we actually ship must resolve to its own action."""
    from grimhaven.bot.keyboards import DOCK_ROWS
    for lang, rows in DOCK_ROWS.items():
        for row in rows:
            for label in row:
                assert resolve_dock_action(label) == REPLY_TO_ACTION[label], (
                    f"{lang}: {label!r}"
                )


def test_resolution_survives_cosmetic_noise() -> None:
    """Emoji presentation selectors and Arabic letterforms must fold away."""
    assert resolve_dock_action("⚡️ اقدام به شکست سد") == "breakthrough:view:prep"
    assert resolve_dock_action("⚡ اقدام به شکست سد") == "breakthrough:view:prep"
    # Arabic ي/ك instead of Persian ی/ک, plus doubled whitespace
    assert resolve_dock_action("⚙️  تنظيمات") == "settings:view:main"


def test_non_dock_text_does_not_resolve() -> None:
    """Ordinary chat must not be mistaken for a button."""
    assert resolve_dock_action("سلام") is None
    assert resolve_dock_action("") is None
    assert resolve_dock_action(None) is None


@pytest.mark.parametrize("chat", [
    "I want to shop for a new map",     # 'shop' and 'map' are dock keywords
    "section 3 of the manual",          # 'sect' is a prefix of a keyword
    "my bag is empty",
    "shop around",
    "let me hunt tomorrow",
    "در نقشه جدید بگرد",               # Persian chat mentioning نقشه
    "میخواهم یک کوله بخرم",             # Persian chat mentioning کوله
])
def test_keyword_pass_never_hijacks_real_chat(chat: str) -> None:
    """The drift fallback must not fire on sentences, only on button-shaped text.

    Guards the gate in `_looks_like_dock_button`: without it the keyword list
    would turn ordinary English/Persian chat into random navigation.
    """
    assert resolve_dock_action(chat) is None


# ── the tap is never swallowed ───────────────────────────────────────────────

from tests.telegram_fakes import FakeContext, FakeUpdate as _Upd


def _ctx_wrap(ctx):
    """A context that looks like PTB's: bot_data *and* bot."""
    return FakeContext(ctx)


def _ctx(tmp_path: pathlib.Path) -> Ctx:
    storage = Storage(tmp_path / "tap.db")
    bootstrap_world(storage)
    bootstrap_data()
    ctx = Ctx(storage, admin_ids=set())
    storage.save_user(ensure_v2(new_user_doc(777, "tapper")))
    return ctx


@pytest.mark.asyncio
async def test_unknown_dock_text_still_gets_a_reply(tmp_path: pathlib.Path) -> None:
    """The regression itself: an unrecognised tap used to return silently."""
    ctx = _ctx(tmp_path)

    update = _Upd(text="⚡ اقدام به شکست سد", user_id=777)
    await guard_update(ctx.storage.get_user, cmds.on_reply_button)(update, _ctx_wrap(ctx))
    assert update.message.replies, "player tapped and the bot said nothing"
    assert "لوح سرنوشت" in update.message.replies[0]


@pytest.mark.asyncio
async def test_stale_dock_tap_reaches_its_screen(tmp_path: pathlib.Path) -> None:
    """The stale meditation label must open the meditation hub, not the HUD."""
    ctx = _ctx(tmp_path)

    update = _Upd(text="🧘 مدیتیشن و تهدید", user_id=777)
    await guard_update(ctx.storage.get_user, cmds.on_reply_button)(update, _ctx_wrap(ctx))
    assert update.message.replies
    assert "مدیتیشن" in update.message.replies[0] or "خلوت" in update.message.replies[0]


# ── world-state persistence ──────────────────────────────────────────────────

def _load_artifact_module():
    spec = importlib.util.spec_from_file_location(
        "gha_artifact", ROOT / "deploy" / "gha_artifact.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _token(scope: str) -> str:
    import base64
    import json
    payload = base64.urlsafe_b64encode(
        json.dumps({"scp": scope}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_backend_ids_parsed_from_runtime_token() -> None:
    mod = _load_artifact_module()
    assert mod._backend_ids(_token("a b Actions.Results:run-1:job-1")) == ("run-1", "job-1")


def test_backend_ids_reject_token_without_results_scope() -> None:
    """A token without the scope must fail loudly, not silently skip the save."""
    mod = _load_artifact_module()
    with pytest.raises(mod.ArtifactError, match="Actions.Results"):
        mod._backend_ids(_token("repo:read"))


def test_missing_env_is_reported_clearly() -> None:
    mod = _load_artifact_module()
    with pytest.raises(mod.ArtifactError, match="ACTIONS_RUNTIME_TOKEN"):
        mod._require_env.__wrapped__() if hasattr(mod._require_env, "__wrapped__") \
            else mod._require_env()


def test_verify_fails_when_no_artifact_was_ever_stored(monkeypatch) -> None:
    """The silent data-loss guard: an empty store must raise, not pass."""
    mod = _load_artifact_module()
    import urllib.request

    class _Resp:
        def read(self):
            import json
            return json.dumps({"total_count": 0, "artifacts": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    with pytest.raises(mod.ArtifactError, match="NOT being"):
        mod.verify("grimhaven-db")


def test_verify_accepts_a_live_artifact(monkeypatch) -> None:
    mod = _load_artifact_module()
    import urllib.request

    class _Resp:
        def read(self):
            import json
            return json.dumps({"artifacts": [{
                "id": 42, "name": "grimhaven-db", "size_in_bytes": 10,
                "created_at": "2026-10-10T00:00:00Z", "expired": False,
            }]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    mod.verify("grimhaven-db")  # must not raise


def test_verify_ignores_expired_artifacts(monkeypatch) -> None:
    mod = _load_artifact_module()
    import urllib.request

    class _Resp:
        def read(self):
            import json
            return json.dumps({"artifacts": [{
                "id": 1, "name": "grimhaven-db", "size_in_bytes": 10,
                "created_at": "2026-01-01T00:00:00Z", "expired": True,
            }]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    with pytest.raises(mod.ArtifactError):
        mod.verify("grimhaven-db")


def test_store_reports_http_failure_with_detail(monkeypatch, tmp_path) -> None:
    """A rejected CreateArtifact must name the reason, not swallow it."""
    mod = _load_artifact_module()
    import urllib.error

    def _boom(*a, **k):
        raise urllib.error.HTTPError(
            "u", 403, "Forbidden", {},
            __import__("io").BytesIO(b'{"message":"quota exceeded"}'),
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", _boom)
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", _token("Actions.Results:r:j"))
    monkeypatch.setenv("ACTIONS_RESULTS_URL", "https://results.example")
    zip_path = tmp_path / "s.zip"
    zip_path.write_bytes(b"PK\x03\x04payload")
    with pytest.raises(mod.ArtifactError, match="403"):
        mod.store(zip_path, "grimhaven-db", attempts=1)