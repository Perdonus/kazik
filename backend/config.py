from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"

RARITIES = [
    {"id": "consumer", "label": "Ширпотреб", "color": "#94a3b8", "weight": 45},
    {"id": "industrial", "label": "Промышленное", "color": "#0f766e", "weight": 22},
    {"id": "milspec", "label": "Армейское", "color": "#2563eb", "weight": 16},
    {"id": "restricted", "label": "Запрещенное", "color": "#f59e0b", "weight": 8},
    {"id": "classified", "label": "Засекреченное", "color": "#f97316", "weight": 4},
    {"id": "covert", "label": "Тайное", "color": "#ef4444", "weight": 1.5},
    {"id": "extraordinary", "label": "Экстраординарное", "color": "#facc15", "weight": 0.5},
]

CASE_CATEGORY_MAP = {
    "Revolution": "Прайм",
    "Kilowatt": "Прайм",
    "Dreams & Nightmares": "Прайм",
    "Recoil": "Прайм",
    "Snakebite": "Прайм",
    "Fracture": "Прайм",
    "Clutch": "Прайм",
    "Prisma 2": "Прайм",
    "Prisma": "Прайм",
    "Spectrum 2": "Прайм",
    "Spectrum": "Прайм",
    "Horizon": "Прайм",
    "Danger Zone": "Прайм",
    "Chroma 3": "Неон",
    "Chroma 2": "Неон",
    "Chroma": "Неон",
    "Gamma 2": "Неон",
    "Gamma": "Неон",
    "Shadow": "Операции",
    "Falchion": "Операции",
    "Glove": "Операции",
    "Wildfire": "Операции",
    "Phoenix": "Операции",
    "Vanguard": "Операции",
    "Breakout": "Операции",
    "Bravo": "Операции",
    "Operation Riptide": "Операции",
    "Operation Broken Fang": "Операции",
    "Operation Shattered Web": "Операции",
    "Hydra": "Операции",
    "Esports 2013": "Турнирные",
    "Esports 2013 Winter": "Турнирные",
    "Esports 2014 Summer": "Турнирные",
    "Weapon Case": "Классика",
    "Weapon Case 2": "Классика",
    "Weapon Case 3": "Классика",
    "Winter Offensive": "Классика",
    "Huntsman": "Классика",
    "Cobblestone": "Коллекции",
    "Cache": "Коллекции",
    "Dust 2": "Коллекции",
    "Mirage": "Коллекции",
    "Inferno": "Коллекции",
    "Nuke": "Коллекции",
    "Overpass": "Коллекции",
    "Vertigo": "Коллекции",
    "Anubis": "Коллекции",
    "Ancient": "Коллекции",
    "Train": "Коллекции",
    "Lake": "Коллекции",
}


@dataclass
class CaseDef:
    id: str
    name: str
    category: str
    price: int
    image_slug: str


@dataclass
class WeaponDef:
    id: str
    name: str
    rarity: str
    price: int
    stattrak: bool
    cases: List[str]


@dataclass
class Config:
    cases: List[CaseDef]
    cases_by_id: Dict[str, CaseDef]
    weapons: List[WeaponDef]
    weapons_by_id: Dict[str, WeaponDef]
    weapons_by_case: Dict[str, List[WeaponDef]]
    categories: List[str]
    rarities: List[dict]


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9\-]", "", value)
    value = re.sub(r"-+", "-", value)
    return value


def normalize_name(value: str) -> str:
    value = re.sub(r"[_-]+", " ", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_key(value: str) -> str:
    return normalize_name(value).lower()


def parse_env(path: Path) -> Config:
    if not path.exists():
        return Config(
            cases=[],
            cases_by_id={},
            weapons=[],
            weapons_by_id={},
            weapons_by_case={},
            categories=[],
            rarities=RARITIES,
        )

    lines = path.read_text(encoding="utf-8").splitlines()
    cases: List[CaseDef] = []
    weapons: List[WeaponDef] = []

    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("CASE:"):
            payload = raw[5:].strip()
            if "=" in payload:
                name, price_raw = payload.split("=", 1)
                name = name.strip()
                try:
                    price = int(price_raw.strip())
                except ValueError:
                    price = 0
                category = CASE_CATEGORY_MAP.get(name, "Прочее")
                case_def = CaseDef(
                    id=slugify(name),
                    name=name,
                    category=category,
                    price=price,
                    image_slug=slugify(name),
                )
                cases.append(case_def)
        elif raw.startswith("WEAPON:"):
            payload = raw[7:].strip()
            parts = [part.strip() for part in payload.split("|")]
            if len(parts) < 5:
                continue
            name, stattrak_raw, rarity, price_raw, cases_raw = parts[:5]
            stattrak = stattrak_raw.lower() in {"true", "yes", "1", "stattrak"}
            try:
                price = int(price_raw)
            except ValueError:
                price = 0
            if stattrak:
                price = round(price * 1.3)
            case_names = [normalize_name(item) for item in cases_raw.split(",") if item.strip()]
            weapons.append(
                WeaponDef(
                    id="",
                    name=name.strip(),
                    rarity=rarity.strip(),
                    price=price,
                    stattrak=stattrak,
                    cases=case_names,
                )
            )

    categories = sorted({case.category for case in cases})

    case_name_map = {normalize_key(case.name): case.id for case in cases}
    weapons_by_case: Dict[str, List[WeaponDef]] = {case.id: [] for case in cases}
    weapons_by_id: Dict[str, WeaponDef] = {}

    for idx, weapon in enumerate(weapons):
        weapon_id = f"{slugify(weapon.name)}-{weapon.rarity}-{weapon.price}-{int(weapon.stattrak)}-{idx}"
        weapon.id = weapon_id
        weapons_by_id[weapon_id] = weapon
        for case_name in weapon.cases:
            case_id = case_name_map.get(normalize_key(case_name))
            if case_id:
                weapons_by_case.setdefault(case_id, []).append(weapon)

    cases_by_id = {case.id: case for case in cases}

    return Config(
        cases=cases,
        cases_by_id=cases_by_id,
        weapons=weapons,
        weapons_by_id=weapons_by_id,
        weapons_by_case=weapons_by_case,
        categories=categories,
        rarities=RARITIES,
    )


_config_cache: dict = {"mtime": None, "config": None}


def get_config() -> Config:
    mtime = ENV_PATH.stat().st_mtime if ENV_PATH.exists() else None
    if _config_cache["config"] is None or mtime != _config_cache["mtime"]:
        _config_cache["config"] = parse_env(ENV_PATH)
        _config_cache["mtime"] = mtime
    return _config_cache["config"]
