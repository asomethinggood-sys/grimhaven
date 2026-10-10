"""Part 1 §4 — modular data engine: a thread-safe Pydantic-validated loader
that ingests data/*.json into memory at startup with O(1) indexed lookups.

No game content is hardcoded in application logic; everything (50 idle events,
30 manuals / 90 techniques, equipment, consumables, enemies) lives in JSON.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("DataLoader")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


# ── offline events ───────────────────────────────────────────────────────────
class OfflineEventEffect(BaseModel):
    bonus_qi: int = 0
    spirit_stones: int = 0
    dao_heart: int = 0
    hp: int = 0
    item_id: Optional[str] = None
    item_name_fa: Optional[str] = None


class OfflineEvent(BaseModel):
    id: int
    type: str
    icon: str = "✨"
    text: str
    text_en: str = ""
    effect: OfflineEventEffect = Field(default_factory=OfflineEventEffect)

    @field_validator("type")
    @classmethod
    def _type_ok(cls, v: str) -> str:
        allowed = {"combat", "encounter", "hazard", "nature", "insight"}
        if v not in allowed:
            raise ValueError(f"offline event type must be one of {allowed}, got {v!r}")
        return v

    def text_for(self, lang: str) -> str:
        return self.text_en if lang == "en" and self.text_en else self.text


# ── martial arts ──────────────────────────────────────────────────────────────
class TechniqueDescriptions(BaseModel):
    normal: str
    crit: str
    grazed: str


class Technique(BaseModel):
    id: str
    name: str
    name_en: str = ""
    qi_cost: int
    cooldown: int = 0
    base_damage_multiplier: float = 1.0
    scaling_stat: str = "spiritual_atk"
    lifesteal_pct: float = 0.0
    status_inflict: Optional[Dict[str, Any]] = None
    descriptions: TechniqueDescriptions
    descriptions_en: Optional[TechniqueDescriptions] = None


class MartialArt(BaseModel):
    art_id: str
    name: str
    name_en: str = ""
    element: str = ""
    element_en: str = ""
    family: str = "sword"
    required_realm: int = 1
    price_stones: int = 0
    techniques: List[Technique]

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name


# ── equipment & consumables ─────────────────────────────────────────────────
class EquipmentItem(BaseModel):
    item_id: str
    name: str
    name_en: str = ""
    slot: str
    tier: str
    spiritual_atk_bonus: int = 0
    physical_atk_bonus: int = 0
    physical_def_bonus: int = 0
    spiritual_def_bonus: int = 0
    crit_rate_bonus: float = 0.0
    speed_bonus: int = 0
    hp_bonus: int = 0
    capacity_bonus: int = 0
    tribulation_mitigation_percent: int = 0
    special_effect: Optional[str] = None
    price_stones: Optional[int] = None
    sell_price: int = 0

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name


class ConsumableItem(BaseModel):
    item_id: str
    name: str
    name_en: str = ""
    category: str  # pill | herb | material | disposable_weapon | relic
    action: str
    value: int = 0
    fixed_damage: int = 0
    shield_value: int = 0
    stun_chance_percent: int = 0
    burn_on_use: bool = False
    bypass_defense_percent: int = 0
    atk_cut_percent: int = 0
    turns: int = 0
    price_stones: Optional[int] = None
    sell_price: int = 0

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name


# ── enemies (doc mainPrompt2 §P4: narrative-driven schema) ──────────────────
class EnemyNarration(BaseModel):
    hit: str = ""
    crit: str = ""
    miss: str = ""


class EnemyAction(BaseModel):
    action_id: str
    name: str
    name_en: str = ""
    damage_multiplier: float = 1.0
    descriptions: EnemyNarration = Field(default_factory=EnemyNarration)
    descriptions_en: Optional[EnemyNarration] = None

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name

    def desc_for(self, lang: str) -> EnemyNarration:
        if lang == "en" and self.descriptions_en:
            return self.descriptions_en
        return self.descriptions


class EnemyStats(BaseModel):
    max_hp: int = 100
    spiritual_atk: int = 10
    defense: int = 5
    sdef: int = 0
    speed: int = 10


class LootDrop(BaseModel):
    item_id: str
    name: str = ""
    name_en: str = ""
    chance_percent: float = 10.0

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name


class EnemyRewards(BaseModel):
    exp_qi: int = 0
    spirit_stones: int = 0
    loot_drops: List[LootDrop] = Field(default_factory=list)


class Enemy(BaseModel):
    """Unified enemy record: spec-schema beasts + normalized legacy blocks.

    ``hp/atk/def_/sdef/speed`` stay exposed as properties so older call-sites
    keep working while the combat pipeline migrates to ``stats``.
    """
    enemy_id: str
    zone_id: Optional[str] = None
    name: str
    name_en: str = ""
    tier_title: str = ""
    tier_title_en: str = ""
    icon: str = "🐾"
    lore: str = ""
    lore_en: str = ""
    stats: EnemyStats = Field(default_factory=EnemyStats)
    rewards: EnemyRewards = Field(default_factory=EnemyRewards)
    actions: List[EnemyAction] = Field(default_factory=list)
    guard_level: int = 1
    special: Optional[Dict[str, Any]] = None
    loot: Dict[str, Any] = Field(default_factory=dict)

    @property
    def hp(self) -> int:
        return self.stats.max_hp

    @property
    def atk(self) -> int:
        return self.stats.spiritual_atk

    @property
    def def_(self) -> int:
        return self.stats.defense

    @property
    def sdef(self) -> int:
        return self.stats.sdef

    @property
    def speed(self) -> int:
        return self.stats.speed

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name

    def tier_for(self, lang: str) -> str:
        return self.tier_title_en if lang == "en" and self.tier_title_en else self.tier_title

    def lore_for(self, lang: str) -> str:
        return self.lore_en if lang == "en" and self.lore_en else self.lore


# ── zones & methods (mainPrompt2 §P3) ────────────────────────────────────────
class GatherDef(BaseModel):
    qi_cost: int = 5
    common_item_id: str = ""
    common_name: str = ""
    common_name_en: str = ""
    uncommon_item_id: str = ""
    uncommon_name: str = ""
    uncommon_name_en: str = ""


class ZoneDef(BaseModel):
    zone_id: str
    order: int = 0
    icon: str = "🟢"
    name: str = ""
    name_en: str = ""
    density: float = 1.0
    min_realm: int = 1
    guard: int = 1
    danger_badge: str = "🟢 ایمن"
    danger_badge_en: str = "🟢 Safe"
    recommended_realm: str = ""
    recommended_realm_en: str = ""
    lore: str = ""
    lore_en: str = ""
    beasts: str = ""
    beasts_en: str = ""
    materials: str = ""
    materials_en: str = ""
    gather: GatherDef = Field(default_factory=GatherDef)
    enemies: List[str] = Field(default_factory=list)

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name

    def danger_for(self, lang: str) -> str:
        return self.danger_badge_en if lang == "en" and self.danger_badge_en else self.danger_badge

    def lore_for(self, lang: str) -> str:
        return self.lore_en if lang == "en" and self.lore_en else self.lore

    def beasts_for(self, lang: str) -> str:
        return self.beasts_en if lang == "en" and self.beasts_en else self.beasts

    def materials_for(self, lang: str) -> str:
        return self.materials_en if lang == "en" and self.materials_en else self.materials

    def rec_for(self, lang: str) -> str:
        return self.recommended_realm_en if lang == "en" and self.recommended_realm_en else self.recommended_realm


class CultivationMethod(BaseModel):
    method_id: str
    tier: str = "mortal"
    tech_mult: float = 1.0
    qi_mult: float = 1.0
    key: str = ""
    name: str = ""
    name_en: str = ""
    lore: str = ""
    lore_en: str = ""

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name

    def lore_for(self, lang: str) -> str:
        return self.lore_en if lang == "en" and self.lore_en else self.lore


# ── singleton registry ───────────────────────────────────────────────────────
class GameDataRegistry:
    _instance: Optional["GameDataRegistry"] = None
    _lock = threading.RLock()

    def __new__(cls) -> "GameDataRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.data_dir = DATA_DIR
        self.offline_events: Dict[int, OfflineEvent] = {}
        self.martial_arts: Dict[str, MartialArt] = {}
        self.techniques_index: Dict[str, Technique] = {}
        self.tech_owner_art: Dict[str, str] = {}
        self.equipment: Dict[str, EquipmentItem] = {}
        self.consumables: Dict[str, ConsumableItem] = {}
        self.beasts: Dict[str, Enemy] = {}
        self.guardians: Dict[int, Enemy] = {}
        self.rival: Optional[Enemy] = None
        self.enemies_by_zone: Dict[str, List[Enemy]] = {}
        self.zones: Dict[str, ZoneDef] = {}
        self.methods: Dict[str, CultivationMethod] = {}
        self._initialized = True

    # loaders
    def load_all(self, custom_data_dir: Optional[Path] = None) -> None:
        if custom_data_dir:
            self.data_dir = Path(custom_data_dir)
        logger.info("Loading game datasets from: %s", self.data_dir.resolve())
        self._load_offline_events()
        self._load_martial_arts()
        self._load_equipment()
        self._load_consumables()
        self._load_zones()
        self._load_methods()
        self._load_enemies()
        logger.info("All game datasets loaded and indexed.")

    def _load_json_file(self, filename: str) -> Any:
        path = self.data_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing essential game data file: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_offline_events(self):
        self.offline_events.clear()
        for raw in self._load_json_file("offline_events.json"):
            ev = OfflineEvent(**raw)
            self.offline_events[ev.id] = ev
        logger.info("Loaded %d offline event scenarios.", len(self.offline_events))

    def _load_martial_arts(self):
        self.martial_arts.clear()
        self.techniques_index.clear()
        self.tech_owner_art.clear()
        for raw in self._load_json_file("martial_arts.json"):
            art = MartialArt(**raw)
            self.martial_arts[art.art_id] = art
            for tech in art.techniques:
                self.techniques_index[tech.id] = tech
                self.tech_owner_art[tech.id] = art.art_id
        logger.info("Loaded %d manuals with %d techniques.",
                    len(self.martial_arts), len(self.techniques_index))

    def _load_equipment(self):
        self.equipment.clear()
        for raw in self._load_json_file("equipment.json"):
            it = EquipmentItem(**raw)
            self.equipment[it.item_id] = it
        logger.info("Loaded %d equipment items.", len(self.equipment))

    def _load_consumables(self):
        self.consumables.clear()
        for raw in self._load_json_file("consumables.json"):
            it = ConsumableItem(**raw)
            self.consumables[it.item_id] = it
        logger.info("Loaded %d consumables & talismans.", len(self.consumables))

    def _load_enemies(self):
        self.beasts.clear()
        self.guardians.clear()
        self.enemies_by_zone.clear()
        data = self._load_json_file("enemies.json")

        def _norm(raw: Dict[str, Any], default_guard: int = 1) -> Enemy:
            raw = dict(raw)
            if "stats" not in raw:  # legacy flat block → unified schema
                loot = raw.get("loot") or {}
                qi_rng, st_rng = loot.get("qi") or [0, 0], loot.get("stones") or [0, 0]
                drops = [{"item_id": k, "name": k, "chance_percent": round(v * 100, 1)}
                         for k, v in (loot.get("drop") or {}).items()]
                raw["stats"] = {"max_hp": raw.pop("hp", 100), "spiritual_atk": raw.pop("atk", 10),
                                "defense": raw.pop("def", 5), "sdef": raw.pop("sdef", 0),
                                "speed": raw.pop("speed", 10)}
                raw["rewards"] = {"exp_qi": (qi_rng[0] + qi_rng[1]) // 2,
                                  "spirit_stones": (st_rng[0] + st_rng[1]) // 2,
                                  "loot_drops": drops}
                raw.pop("loot_drops", None)
            raw.setdefault("guard_level", default_guard)
            raw.pop("def", None)
            return Enemy(**raw)

        for raw in data.get("beasts", []):
            e = _norm(raw)
            if e.zone_id and e.zone_id in self.zones:
                e.guard_level = self.zones[e.zone_id].guard
            self.beasts[e.enemy_id] = e
            if e.zone_id:
                self.enemies_by_zone.setdefault(e.zone_id, []).append(e)
        for raw in data.get("guardians", []):
            g = _norm(raw)
            self.guardians[g.guard_level] = g
        if data.get("rival"):
            self.rival = _norm(data["rival"])
        # keep zone rosters pointing at the loaded objects
        for zid, zdef in self.zones.items():
            for eid in zdef.enemies:
                if eid not in self.beasts:
                    logger.warning("Zone %s references unknown beast %s", zid, eid)
        logger.info("Loaded %d beasts across %d zones, %d guardians.",
                    len(self.beasts), len(self.enemies_by_zone), len(self.guardians))

    def _load_zones(self):
        self.zones.clear()
        data = self._load_json_file("zones.json")
        for zid, raw in data.items():
            z = ZoneDef(zone_id=zid, **raw)
            self.zones[zid] = z
        logger.info("Loaded %d world zones.", len(self.zones))

    def _load_methods(self):
        self.methods.clear()
        data = self._load_json_file("cultivation_methods.json")
        for mid, raw in data.items():
            self.methods[mid] = CultivationMethod(method_id=mid, **raw)
        logger.info("Loaded %d cultivation methods.", len(self.methods))

    # accessors
    def get_event(self, event_id: int) -> Optional[OfflineEvent]:
        return self.offline_events.get(event_id)

    def get_all_events(self) -> List[OfflineEvent]:
        return list(self.offline_events.values())

    def get_martial_art(self, art_id: str) -> Optional[MartialArt]:
        return self.martial_arts.get(art_id)

    def get_technique(self, tech_id: str) -> Optional[Technique]:
        return self.techniques_index.get(tech_id)

    def get_equipment(self, item_id: str) -> Optional[EquipmentItem]:
        return self.equipment.get(item_id)

    def get_consumable(self, item_id: str) -> Optional[ConsumableItem]:
        return self.consumables.get(item_id)

    def get_item(self, item_id: str):
        """Any catalog record for an item id (gear or consumable/material)."""
        return self.get_equipment(item_id) or self.get_consumable(item_id)

    def get_beast(self, enemy_id: str) -> Optional[Enemy]:
        return self.beasts.get(enemy_id)

    def get_guardian(self, guard_level: int) -> Optional[Enemy]:
        return self.guardians.get(guard_level)

    def is_loaded(self) -> bool:
        """True once the shipped data sets are actually in memory.

        Anything that *rewrites* a stored id because it looks unknown has to ask
        this first: with an empty registry every zone and every technique is
        "unknown", so a migration run before :func:`bootstrap` (or after a failed
        one) would happily wipe real player loadouts and teleport everyone to the
        starting valley.
        """
        return bool(self._initialized and (self.zones or self.techniques_index))

    def get_zone(self, zone_id: str) -> Optional[ZoneDef]:
        return self.zones.get(zone_id)

    def zones_ordered(self) -> List[ZoneDef]:
        return sorted(self.zones.values(), key=lambda z: z.order)

    def get_method(self, method_id: str) -> Optional[CultivationMethod]:
        return self.methods.get(method_id)

    def enemies_by_zone_list(self, zone_id: str) -> List[Enemy]:
        z = self.zones.get(zone_id)
        if z and z.enemies:
            picked = [self.beasts[e] for e in z.enemies if e in self.beasts]
            if picked:
                return picked
        return list(self.enemies_by_zone.get(zone_id, []))

    def get_random_enemy_by_zone(self, zone_id: str) -> Optional[Enemy]:
        import random
        candidates = self.enemies_by_zone_list(zone_id)
        return random.choice(candidates) if candidates else None

    def beasts_for_guard(self, guard_level: int) -> List[Enemy]:
        return [b for b in self.beasts.values() if b.guard_level == guard_level]

    def item_name(self, item_id: str, lang: str = "en") -> str:
        obj = self.get_equipment(item_id) or self.get_consumable(item_id)
        if obj:
            return obj.name_for(lang)
        beast = self.beasts.get(item_id)
        if beast:
            return beast.name_for(lang)
        for e in self.beasts.values():  # loot drops carry display names too
            for d in e.rewards.loot_drops:
                if d.item_id == item_id:
                    return d.name_for(lang)
        return item_id


data_registry = GameDataRegistry()


def bootstrap() -> GameDataRegistry:
    """Idempotent startup hook — call once from app entry points / fixtures."""
    if not data_registry.techniques_index:
        data_registry.load_all()
    return data_registry
