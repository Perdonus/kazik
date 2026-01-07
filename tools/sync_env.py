#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"
CASE_DIR = BASE_DIR / "case"
GUNS_DIR = BASE_DIR / "guns"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

CASE_PREFIX = "CASE:"
WEAPON_PREFIX = "WEAPON:"

RARITY_WEIGHTS = [
    ("consumer", 52.0),
    ("industrial", 23.0),
    ("milspec", 14.0),
    ("restricted", 7.0),
    ("classified", 3.0),
    ("covert", 1.0),
    ("extraordinary", 0.3),
]

RARITY_PRICE_RANGES = {
    "consumer": (30, 90),
    "industrial": (70, 140),
    "milspec": (120, 240),
    "restricted": (220, 480),
    "classified": (380, 900),
    "covert": (800, 1600),
    "extraordinary": (1500, 3200),
}

CASE_PRICE_TIERS = [
    ((60, 140), 0.55),
    ((140, 240), 0.25),
    ((240, 360), 0.15),
    ((360, 700), 0.05),
]

WEAPON_CASE_COUNT = [
    (1, 0.55),
    (2, 0.3),
    (3, 0.1),
    (4, 0.05),
]


@dataclass
class CaseEntry:
    name: str
    price: int
    index: int


@dataclass
class WeaponEntry:
    name: str
    stattrak: str
    rarity: str
    price: int
    cases: list[str]


def normalize_name(value: str) -> str:
    value = re.sub(r"[_-]+", " ", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_key(value: str) -> str:
    return normalize_name(value).lower()


def seed_for(value: str) -> int:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()[:8]
    return int(digest, 16)


def weighted_pick(rng: random.Random, items: list[tuple[str, float]]):
    total = sum(weight for _, weight in items)
    roll = rng.random() * total
    for value, weight in items:
        roll -= weight
        if roll <= 0:
            return value
    return items[0][0]


def round_price(value: int) -> int:
    return int(round(value / 10) * 10)


def parse_cases(lines: list[str]) -> dict[str, CaseEntry]:
    cases: dict[str, CaseEntry] = {}
    for idx, line in enumerate(lines):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith(CASE_PREFIX):
            payload = raw[len(CASE_PREFIX) :].strip()
            if "=" in payload:
                name, price_raw = payload.split("=", 1)
                name = name.strip()
                try:
                    price = int(price_raw.strip())
                except ValueError:
                    price = 0
                cases[normalize_key(name)] = CaseEntry(name=name, price=price, index=idx)
    return cases


def parse_weapons(lines: list[str]) -> dict[str, int]:
    weapons: dict[str, int] = {}
    for idx, line in enumerate(lines):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith(WEAPON_PREFIX):
            payload = raw[len(WEAPON_PREFIX) :].strip()
            parts = [part.strip() for part in payload.split("|")]
            if parts:
                name = parts[0]
                weapons[normalize_key(name)] = idx
    return weapons


def parse_weapon_case_usage(lines: list[str]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith(WEAPON_PREFIX):
            payload = raw[len(WEAPON_PREFIX) :].strip()
            parts = [part.strip() for part in payload.split("|")]
            if len(parts) < 5:
                continue
            cases_raw = parts[4]
            for case_name in cases_raw.split(","):
                case_key = normalize_key(case_name)
                if not case_key:
                    continue
                usage[case_key] = usage.get(case_key, 0) + 1
    return usage


def find_case_section_end(lines: list[str]) -> int:
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(WEAPON_PREFIX) or stripped.startswith("# WEAPONS"):
            return idx
    return len(lines)


def find_weapon_insert_at(lines: list[str]) -> int:
    last_weapon = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(WEAPON_PREFIX):
            last_weapon = idx
    if last_weapon is not None:
        return last_weapon + 1
    for idx, line in enumerate(lines):
        if line.strip().startswith("# WEAPONS"):
            return idx + 1
    return len(lines)


def ensure_env_file() -> list[str]:
    if ENV_PATH.exists():
        return ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    scaffold = [
        "# CASES (name=price)\n",
        "\n",
        "# RARITIES: consumer, industrial, milspec, restricted, classified, covert, extraordinary\n",
        "# WEAPONS (name | stattrak | rarity | price | cases)\n",
    ]
    ENV_PATH.write_text("".join(scaffold), encoding="utf-8")
    return scaffold


def ensure_dir(path: Path, label: str) -> None:
    if not path.exists():
        print(f"{label} не найдена, создаю пустую папку.")
        path.mkdir(parents=True, exist_ok=True)


def scan_images(path: Path) -> list[str]:
    ensure_dir(path, f"{path}/")
    names = []
    for file in path.iterdir():
        if file.is_file() and file.suffix.lower() in IMAGE_EXTS:
            names.append(file.stem.strip())
    return names


def generate_case_price(name: str) -> int:
    rng = random.Random(seed_for(f"case:{name}"))
    tier = weighted_pick(rng, CASE_PRICE_TIERS)
    price = rng.randint(tier[0], tier[1])
    return round_price(price)


def generate_weapon_entry(name: str, case_names: list[str]) -> WeaponEntry:
    rng = random.Random(seed_for(f"weapon:{name}"))
    rarity = weighted_pick(rng, RARITY_WEIGHTS)
    price_min, price_max = RARITY_PRICE_RANGES[rarity]
    price = round_price(rng.randint(price_min, price_max))
    case_count = weighted_pick(rng, WEAPON_CASE_COUNT)
    if case_names:
        chosen = rng.sample(case_names, k=min(case_count, len(case_names)))
    else:
        chosen = []
    return WeaponEntry(
        name=name,
        stattrak="none",
        rarity=rarity,
        price=price,
        cases=chosen,
    )


def main() -> None:
    lines = ensure_env_file()
    case_entries = parse_cases(lines)
    weapon_entries = parse_weapons(lines)

    case_names = scan_images(CASE_DIR)
    weapon_names = scan_images(GUNS_DIR)

    existing_case_keys = set(case_entries.keys())
    missing_cases = [name for name in case_names if normalize_key(name) not in existing_case_keys]

    updated_case_prices = []
    for key, entry in case_entries.items():
        if entry.price == 0:
            new_price = generate_case_price(entry.name)
            lines[entry.index] = f"{CASE_PREFIX}{entry.name}={new_price}\n"
            updated_case_prices.append(entry.name)

    if missing_cases:
        insert_at = find_case_section_end(lines)
        new_lines = [f"{CASE_PREFIX}{name}={generate_case_price(name)}\n" for name in missing_cases]
        lines = lines[:insert_at] + new_lines + lines[insert_at:]

    all_case_names = [entry.name for entry in case_entries.values()] + missing_cases
    all_case_names = sorted({name for name in all_case_names}, key=lambda x: x.lower())

    existing_weapon_keys = set(weapon_entries.keys())
    missing_weapons = [name for name in weapon_names if normalize_key(name) not in existing_weapon_keys]

    case_usage = parse_weapon_case_usage(lines)
    for case_name in all_case_names:
        case_usage.setdefault(normalize_key(case_name), 0)

    new_weapon_entries: list[WeaponEntry] = []
    for name in missing_weapons:
        entry = generate_weapon_entry(name, all_case_names)
        new_weapon_entries.append(entry)
        for case_name in entry.cases:
            case_usage[normalize_key(case_name)] = case_usage.get(normalize_key(case_name), 0) + 1

    if all_case_names and new_weapon_entries:
        uncovered = [name for name in all_case_names if case_usage.get(normalize_key(name), 0) == 0]
        if uncovered:
            for idx, case_name in enumerate(uncovered):
                weapon = new_weapon_entries[idx % len(new_weapon_entries)]
                if case_name not in weapon.cases:
                    weapon.cases.append(case_name)

    weapon_insert_at = find_weapon_insert_at(lines)
    weapon_lines = []
    for entry in new_weapon_entries:
        cases_value = ",".join(entry.cases) if entry.cases else ""
        weapon_lines.append(
            f"{WEAPON_PREFIX}{entry.name} | {entry.stattrak} | {entry.rarity} | {entry.price} | {cases_value}\n"
        )

    if weapon_lines:
        lines = lines[:weapon_insert_at] + weapon_lines + lines[weapon_insert_at:]

    ENV_PATH.write_text("".join(lines), encoding="utf-8")

    if missing_cases:
        print("Добавлены кейсы:")
        for name in missing_cases:
            print(f"- {name}")

    if updated_case_prices:
        print("Обновлены цены кейсов:")
        for name in updated_case_prices:
            print(f"- {name}")

    if missing_weapons:
        print("Добавлены оружия:")
        for name in missing_weapons:
            print(f"- {name}")

    if not missing_cases and not updated_case_prices and not missing_weapons:
        print("Изменений не найдено.")


if __name__ == "__main__":
    main()
