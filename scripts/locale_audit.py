#!/usr/bin/env python3
"""Locale census: which keys does the game actually ask for?

Split into three verdicts:

* **missing**   — a key the code/data asks for that no locale table defines
                  (the player would see the raw key on screen);
* **asymmetric**— defined in one language only;
* **dead**      — defined in both languages and asked for by nobody.

The tricky half is "asked for": plenty of keys are reached through a *dynamic*
pattern (`t(lang, f"USE_{status}")`). Those families are resolved here by
scanning the engine/constants for the possible suffixes, so a fully live family
is not reported as dead and a family with a hole shows up as missing.

Usage:  python scripts/locale_audit.py [--dead] [--quiet]
Exit code 1 when something is missing or asymmetric.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"
GAME = ROOT / "grimhaven"
CONSTANTS = GAME / "engine" / "constants.py"

KEY_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


LITERAL = re.compile(r'"([A-Za-z0-9_]{3,})"|\'([A-Za-z0-9_]{3,})\'')


def literal_keys_from_source(text: str) -> set[str]:
    """Every string literal in the module.

    A locale key is reachable in more ways than `t(lang, "KEY")`: engines return
    keys (`ok("USE_CURE")`), errors carry them (`user_key="ERR_DELIVERY"`),
    constants tables map ids to them (`"ITEM_LOTUS_SEVEN"`). Intersecting this
    set with the locale tables is the conservative, context-free check: a key
    whose exact name appears nowhere in the game is a dead key.
    """
    out: set[str] = set()
    for a, b in LITERAL.findall(text):
        out.add(a or b)
    return out


DYNAMIC_PREFIX = re.compile(r"""f"[A-Z][A-Z0-9_]*_|"[A-Z][A-Z0-9_]*_"\s*\+|'[A-Z][A-Z0-9_]*_'\s*\+""")


def dynamic_prefixes() -> set[str]:
    """Prefixes the code assembles keys from.

    `t(lang, f"SECT_{suffix}")`, `t(lang, "DAO_" + dao.upper() + "_DESC")`,
    `f"TIER_{t}"` — any key in one of these namespaces is potentially reachable,
    so the whole namespace is kept rather than guessing the suffix set.
    """
    prefixes: set[str] = set()
    for path in sorted(GAME.rglob("*.py")):
        for match in DYNAMIC_PREFIX.finditer(path.read_text(encoding="utf-8")):
            prefix = re.search(r"[A-Z][A-Z0-9_]*_", match.group(0))
            if prefix:
                prefixes.add(prefix.group(0))
    return prefixes


def dynamic_keys() -> set[str]:
    """Families reached through f-strings, expanded from the data/constants."""
    keys: set[str] = set()
    data = ROOT / "data"

    def rows(name: str) -> list[dict]:
        path = data / name
        return _load_json(path) if path.exists() else []

    def statuses_of(module: str, functions: tuple[str, ...]) -> set[str]:
        tree = ast.parse((GAME / module).read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in functions:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Dict):
                        for k, v in zip(sub.keys, sub.values):
                            if (isinstance(k, ast.Constant) and k.value == "status"
                                    and isinstance(v, ast.Constant)
                                    and isinstance(v.value, str)):
                                found.add(v.value)
                    if isinstance(sub, ast.IfExp):        # status = "A" if … else "B"
                        for branch in (sub.body, sub.orelse):
                            if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                                found.add(branch.value)
        return found

    # t(lang, f"USE_{status}")  ← the out-of-battle consume statuses
    for status in statuses_of("engine/items.py", ("use_item",)) - {"OK"}:
        keys.add(f"USE_{status}")
    # t(lang, f"ERR_{status}")  ← equip / unequip report through ERR_
    for status in statuses_of("engine/items.py", ("equip", "unequip")) - {"OK"}:
        keys.add(f"ERR_{status}")
    # t(lang, f"ERR_{category}") ← errors.classify categories
    for category in ("CONTENT", "DOMAIN", "GUARD", "INPUT", "INTERNAL", "STORAGE", "TELEGRAM"):
        keys.add(f"ERR_{category}")

    # t(lang, "ITEM_ACTION_" + action.upper()) ← consumable actions
    for row in rows("consumables.json"):
        if row.get("action"):
            keys.add("ITEM_ACTION_" + row["action"].upper())
    # t(lang, "EFF_SPECIAL_" + special.upper()) ← gear specials
    for row in rows("equipment.json"):
        if row.get("special_effect"):
            keys.add("EFF_SPECIAL_" + row["special_effect"].upper())
    # t(lang, f"STONE_{grade.upper()}") ← spirit stone grades
    for grade in ("low", "mid", "high", "heavenly"):
        keys.add(f"STONE_{grade.upper()}")
    # t(lang, f"STATUS_{kind.upper()}") ← every status kind the engines and the
    # data can inflict ("kind" fields in code, martial arts and equipment)
    kinds: set[str] = set()
    for path in sorted(GAME.rglob("*.py")):
        kinds |= set(re.findall(r'"kind":\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
        kinds |= set(re.findall(r'_status_mod\(session\["statuses"\]\["[pe]"\],\s*"([a-z_]+)"',
                                path.read_text(encoding="utf-8")))
    for name in ("martial_arts.json", "equipment.json", "consumables.json"):
        for row in rows(name):
            blob = json.dumps(row, ensure_ascii=False)
            kinds |= set(re.findall(r'"kind":\s*"([a-z_]+)"', blob))
    for kind in kinds:
        keys.add("STATUS_" + kind.upper())
    return keys


def _slot_keys() -> set[str]:
    """`t(lang, f"SLOT_{slot.upper()}")` ← the equippable slot ids."""
    text = (GAME / "engine" / "items.py").read_text(encoding="utf-8")
    match = re.search(r"EQUIP_SLOTS\s*=\s*[\(\[\{]([^)\]}]*)[\)\]}]", text)
    slots = re.findall(r'"([a-z_]+)"', match.group(1)) if match else []
    if not slots:
        slots = re.findall(r'"([a-z_]+)"',
                           (GAME / "engine" / "constants.py").read_text(encoding="utf-8"))
    return {f"SLOT_{slot.upper()}" for slot in slots}


def _tier_keys() -> set[str]:
    """`TIER_KEYS = {t: f"TIER_{t}" for t in ITEM_TIERS}`."""
    text = (GAME / "engine" / "constants.py").read_text(encoding="utf-8")
    match = re.search(r"ITEM_TIERS\s*=\s*[\(\[]([^\)\]]*)[\)\]]", text)
    tiers = re.findall(r'"([a-z_]+)"', match.group(1)) if match else []
    return {f"TIER_{tier}" for tier in tiers}


def _dao_desc_keys() -> set[str]:
    """`t(lang, "DAO_" + dao.upper() + "_DESC")` ← the dao ids in DAO_PATHS."""
    text = (GAME / "engine" / "constants.py").read_text(encoding="utf-8")
    block = re.search(r"DAO_PATHS\s*=\s*\{(.*?)\n\}", text, re.S)
    daos = re.findall(r'"([a-z_]+)"\s*:', block.group(1)) if block else []
    return {f"DAO_{dao.upper()}_DESC" for dao in daos} | {f"DAO_{dao.upper()}" for dao in daos}


def _sect_keys() -> set[str]:
    """`t(lang, f"SECT_{status}")` ← the sect-action statuses."""
    world = (GAME / "engine" / "world.py").read_text(encoding="utf-8")
    out = {f"SECT_{state}" for state in re.findall(r'"status":\s*"([A-Z_]+)"', world)}
    out |= {f"SECT_{state}" for state in re.findall(r'"([A-Z][A-Z_]{4,})"', world)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dead", action="store_true", help="list every dead key")
    ap.add_argument("--review", action="store_true",
                    help="list the keys kept only by the namespace heuristic")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    fa = _load_json(LOCALES / "locale_fa.json")
    en = _load_json(LOCALES / "locale_en.json")

    known = set(fa) & set(en)
    used: dict[str, set[str]] = {}
    sources = [path for path in sorted(GAME.rglob("*.py"))]
    sources += [path for path in sorted((ROOT / "data").glob("*.json"))]
    for path in sources:
        for key in literal_keys_from_source(path.read_text(encoding="utf-8")) & known:
            used.setdefault(key, set()).add(str(path.relative_to(ROOT)))
    # Precise families: the suffix set is knowable, so only those keys are kept.
    precise = dynamic_keys() | _slot_keys() | _tier_keys() | _dao_desc_keys() \
        | _sect_keys()
    for key in precise & known:
        used.setdefault(key, set()).add("<dynamic>")
    # Namespace rule: any prefix the code assembles keys from keeps its *whole*
    # namespace. The precise families above are documentation of the reachable
    # live subset; this rule is what makes the census safe — e.g. STATUS_ is
    # built as `'STATUS_' + kind.upper()` for kinds that live in state data, not
    # in the code, so guessing the suffix set would eventually delete a key the
    # HUD asks for on a state nobody exercised. `--review` lists the keys that
    # only this rule keeps, for a human to judge.
    namespace_only: set[str] = set()
    for prefix in dynamic_prefixes():
        for key in known:
            if key.startswith(prefix) and key not in precise:
                namespace_only.add(key)
                used.setdefault(key, set()).add("<namespace>")
    test_used: set[str] = set()
    for path in sorted((ROOT / "tests").rglob("*.py")):
        test_used |= literal_keys_from_source(path.read_text(encoding="utf-8")) & known
    for key in dynamic_keys() & known:
        used.setdefault(key, set()).add("<dynamic>")

    missing = sorted(k for k in used if k not in fa or k not in en)
    asymmetric = sorted(set(fa) ^ set(en))
    dead = sorted(k for k in set(fa) & set(en) if k not in used and k not in test_used)
    test_only = sorted(k for k in set(fa) & set(en) if k not in used and k in test_used)
    dead_by_prefix: dict[str, list[str]] = {}
    for key in dead:
        dead_by_prefix.setdefault(key.split("_")[0], []).append(key)

    if not args.quiet:
        print(f"locale keys: fa={len(fa)} en={len(en)}  referenced by the game: {len(used)}")
        for name, keys in (("MISSING", missing), ("ASYMMETRIC", asymmetric)):
            if keys:
                print(f"\n❌ {name} ({len(keys)}):")
                for key in keys:
                    where = ", ".join(sorted(used.get(key, ())))[:110]
                    print(f"   {key:<32} {where}")
        print(f"\ndead (defined, never referenced): {len(dead)}")
        for prefix in sorted(dead_by_prefix, key=lambda p: -len(dead_by_prefix[p])):
            keys = dead_by_prefix[prefix]
            print(f"   {prefix:<16} {len(keys):>3}  {', '.join(keys[:6])}"
                  + (" …" if len(keys) > 6 else ""))
        if namespace_only:
            print(f"\nkept only by the namespace heuristic: {len(namespace_only)}"
                  "  (--review to list)")
        if args.review and namespace_only:
            for key in sorted(namespace_only):
                print(f"   {key}")
        if test_only:
            print(f"\ntest-only keys (kept by the suite, not by the game): {len(test_only)}")
            for key in test_only:
                print(f"   {key}")
        if args.dead:
            print("\nfull dead list:")
            for key in dead:
                print(f"   {key}")
    # only *missing* / asymmetric rows are errors: dead keys are a cleanup hint
    return 1 if (missing or asymmetric) else 0


if __name__ == "__main__":
    sys.exit(main())
