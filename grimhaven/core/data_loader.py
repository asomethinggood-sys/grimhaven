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


class Enemy(BaseModel):
    enemy_id: str
    name: str
    name_en: str = ""
    guard_level: int = 1
    hp: int
    atk: int
    def_: int = Field(alias="def")
    sdef: int = 0
    speed: int = 10
    loot: Dict[str, Any] = Field(default_factory=dict)
    special: Optional[Dict[str, Any]] = None

    model_config = {"populate_by_name": True}

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" and self.name_en else self.name


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
        data = self._load_json_file("enemies.json")
        for raw in data.get("beasts", []):
            e = Enemy(**raw)
            self.beasts[e.enemy_id] = e
        for raw in data.get("guardians", []):
            g = Enemy(**raw)
            self.guardians[g.guard_level] = g
        if data.get("rival"):
            self.rival = Enemy(**data["rival"])
        logger.info("Loaded %d beasts, %d guardians.", len(self.beasts), len(self.guardians))

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

    def get_beast(self, enemy_id: str) -> Optional[Enemy]:
        return self.beasts.get(enemy_id)

    def get_guardian(self, guard_level: int) -> Optional[Enemy]:
        return self.guardians.get(guard_level)

    def beasts_for_guard(self, guard_level: int) -> List[Enemy]:
        return [b for b in self.beasts.values() if b.guard_level == guard_level]

    def item_name(self, item_id: str, lang: str = "en") -> str:
        obj = self.get_equipment(item_id) or self.get_consumable(item_id)
        if obj:
            return obj.name_for(lang)
        return item_id


data_registry = GameDataRegistry()


def bootstrap() -> GameDataRegistry:
    """Idempotent startup hook — call once from app entry points / fixtures."""
    if not data_registry.techniques_index:
        data_registry.load_all()
    return data_registry
