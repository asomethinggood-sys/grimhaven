"""Content-integrity guards for the authored JSON catalogue (data/*.json).

These pin the invariants the deep audit found broken:

1. `mat_wolf_fang` and `mat_thunder_wood` sat in the catalogue with a sell price
   and no acquisition path at all — unreachable content.
2. `scripts/build_data.py` regenerated `consumables.json` with 21 of the 68 live
   entries (and `enemies.json` with 10 legacy-schema beasts), i.e. the
   documented build script deleted two thirds of the catalogue.  The writer now
   refuses to shrink a file unless `--force` is passed.
3. Nothing checked that loot / gather / event tables only reference real items.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from grimhaven.core.data_loader import bootstrap, data_registry

ROOT = Path(__file__).resolve().parent.parent
DATA_FILES = {"equipment.json": "item_id", "consumables.json": "item_id",
              "enemies.json": "enemy_id"}


def _consumable_ids() -> set[str]:
    return set(data_registry.consumables) | set(data_registry.equipment)


def _loot_gather_event_ids() -> dict[str, set[str]]:
    bootstrap()
    loot: set[str] = set()
    for enemy in list(data_registry.beasts.values()) + list(data_registry.guardians.values()):
        loot |= {d.item_id for d in enemy.rewards.loot_drops}
    gather: set[str] = set()
    for zone in data_registry.zones.values():
        for field_name in ("common_item_id", "uncommon_item_id"):
            item_id = getattr(zone.gather, field_name, "")
            if item_id:
                gather.add(item_id)
    events = {ev.effect.item_id for ev in data_registry.get_all_events() if ev.effect.item_id}
    return {"loot": loot, "gather": gather, "events": events}


@pytest.fixture(scope="module")
def reachability():
    return _loot_gather_event_ids()


def test_every_consumable_is_obtainable(reachability):
    """An entry nobody can buy, loot, gather or be gifted is dead weight."""
    from grimhaven.engine.items import BOOTH_CATALOGS

    reachable = reachability["loot"] | reachability["gather"] | reachability["events"]
    booth_items = {iid for ids in BOOTH_CATALOGS.values() for iid in ids}
    orphan = sorted(
        item_id for item_id, obj in data_registry.consumables.items()
        if item_id not in reachable
        and item_id not in booth_items
        and not (obj.price_stones or 0)
    )
    assert not orphan, f"catalogue entries with no acquisition path: {orphan}"


def test_loot_tables_reference_real_items_with_sane_odds():
    known = _consumable_ids()
    bad = []
    for enemy in list(data_registry.beasts.values()) + list(data_registry.guardians.values()):
        for drop in enemy.rewards.loot_drops:
            if drop.item_id not in known:
                bad.append(f"{enemy.enemy_id}→{drop.item_id} (unknown item)")
            if not 0 < drop.chance_percent <= 100:
                bad.append(f"{enemy.enemy_id}→{drop.item_id} (chance {drop.chance_percent})")
    assert not bad, f"loot tables reference unknown items or impossible odds: {bad}"


def test_offline_events_reference_real_items():
    known = _consumable_ids()
    bad = [f"event {ev.id}→{ev.effect.item_id}" for ev in data_registry.get_all_events()
           if ev.effect.item_id and ev.effect.item_id not in known]
    assert not bad, f"offline events promise items that do not exist: {bad}"


def test_every_zone_has_beasts_to_hunt():
    """A zone with no beast makes `hunt` a dead button there.

    Uses ``enemies_by_zone_list`` — the runtime roster, which honours the zone's
    declared ``enemies`` list (Mist Peak shares the bamboo-forest beasts and is
    absent from the raw per-beast ``zone_id`` index).
    """
    empty = sorted(zone_id for zone_id in data_registry.zones
                   if not data_registry.enemies_by_zone_list(zone_id))
    assert not empty, f"zones with no beasts to hunt: {empty}"


def test_every_beast_zone_exists():
    bad = sorted({e.zone_id for e in data_registry.beasts.values()
                  if e.zone_id and e.zone_id not in data_registry.zones})
    assert not bad, f"beasts spawned in unknown zones: {bad}"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_data", ROOT / "scripts" / "build_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_script_never_shrinks_the_catalogue(tmp_path):
    """`build_data.py` regenerates from stale tables — the guard must protect data/."""
    for name in DATA_FILES:
        shutil.copy(ROOT / "data" / name, tmp_path / name)
    builder = _load_builder()
    builder.OUT = tmp_path
    builder.build_equipment()
    builder.build_consumables()
    builder.build_enemies()

    for name, key in DATA_FILES.items():
        committed = json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
        written = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        lost = builder._ids_of(committed, key) - builder._ids_of(written, key)
        assert not lost, f"build_data.py deleted {sorted(lost)} from {name}"


def test_write_guarded_refuses_to_truncate(tmp_path):
    builder = _load_builder()
    builder.OUT = tmp_path
    (tmp_path / "sample.json").write_text(
        json.dumps([{"item_id": "a"}, {"item_id": "b"}]), encoding="utf-8")
    builder.write_guarded("sample.json", [{"item_id": "a"}])
    kept = json.loads((tmp_path / "sample.json").read_text(encoding="utf-8"))
    assert [r["item_id"] for r in kept] == ["a", "b"], "guard let content be deleted"
    builder.write_guarded("sample.json", [{"item_id": "a"}, {"item_id": "b"}, {"item_id": "c"}])
    grown = json.loads((tmp_path / "sample.json").read_text(encoding="utf-8"))
    assert [r["item_id"] for r in grown] == ["a", "b", "c"], "guard blocked a growth write"


def test_build_script_explains_missing_drive_seeds():
    """No Drive folder → a clear sentence, not a FileNotFoundError traceback."""
    builder = _load_builder()
    builder.SRC = Path("/nonexistent/google_drive")
    with pytest.raises(SystemExit) as excinfo:
        builder.build_offline_events()
    message = str(excinfo.value)
    assert "missing source file" in message and "Nothing was written" in message


def test_orphan_materials_now_drop_somewhere(reachability):
    """Regression: the two materials with a price and no source."""
    for item_id in ("mat_wolf_fang", "mat_thunder_wood"):
        assert item_id in reachability["loot"], f"{item_id} became unreachable again"
