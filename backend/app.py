from __future__ import annotations

import random
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .config import Config, RARITIES, get_config

BASE_DIR = db.BASE_DIR

app = FastAPI(title="Kazino API")

FEED_LIMIT = 16
FEED = deque(maxlen=FEED_LIMIT)
FEED_LOCK = threading.Lock()


class LoginRequest(BaseModel):
    nickname: str


class CaseOpenRequest(BaseModel):
    case_id: str


class SellRequest(BaseModel):
    item_id: str


class UpgradeTargetsRequest(BaseModel):
    item_ids: list[str]
    chance: int


class UpgradeStartRequest(BaseModel):
    item_ids: list[str]
    target_id: str
    chance: int


class GiveawayJoinRequest(BaseModel):
    giveaway_id: str


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    start_feed_thread()


@app.get("/api/bootstrap")
def bootstrap() -> dict:
    config = get_config()
    return {
        "cases": [case.__dict__ for case in config.cases],
        "categories": config.categories,
        "rarities": config.rarities,
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict:
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=400, detail="Ник обязателен")

    token = secrets.token_urlsafe(24)
    with db.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE nickname = ?", (nickname,)).fetchone()
        if user is None:
            user_id = conn.execute(
                "INSERT INTO users (nickname, token, balance, max_balance, daily_reset) VALUES (?, ?, ?, ?, ?)",
                (nickname, token, 500, 500, today_key()),
            ).lastrowid
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        else:
            conn.execute("UPDATE users SET token = ? WHERE id = ?", (token, user["id"]))
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()

        user = reset_daily(conn, user)
        return {"token": token, "user": user_payload(conn, user)}


@app.get("/api/me")
def me(authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    with db.connect() as conn:
        user = fetch_user(conn, token)
        user = reset_daily(conn, user)
        return {"user": user_payload(conn, user)}


@app.post("/api/balance/claim")
def claim_bonus(authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    now = int(time.time())
    cooldown = 20 * 60
    with db.connect() as conn:
        user = fetch_user(conn, token)
        user = reset_daily(conn, user)
        if now - user["last_claim"] < cooldown:
            return {
                "claimed": False,
                "user": user_payload(conn, user),
                "next_claim": user["last_claim"] + cooldown,
            }

        new_balance = user["balance"] + 100
        conn.execute(
            "UPDATE users SET balance = ?, last_claim = ? WHERE id = ?",
            (new_balance, now, user["id"]),
        )
        update_max_balance(conn, user["id"])
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return {"claimed": True, "user": user_payload(conn, user), "next_claim": now + cooldown}


@app.get("/api/cases/{case_id}/weapons")
def case_weapons(case_id: str) -> dict:
    config = get_config()
    if case_id not in config.cases_by_id:
        raise HTTPException(status_code=404, detail="Кейс не найден")
    weapons = config.weapons_by_case.get(case_id, [])

    rarity_order = {rarity["id"]: idx for idx, rarity in enumerate(RARITIES)}
    sorted_weapons = sorted(weapons, key=lambda w: rarity_order.get(w.rarity, 999))
    return {
        "weapons": [
            {
                "id": weapon.id,
                "name": weapon.name,
                "rarity": weapon.rarity,
                "price": weapon.price,
                "stattrak": weapon.stattrak,
            }
            for weapon in sorted_weapons
        ]
    }


@app.post("/api/case/open")
def open_case(payload: CaseOpenRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    config = get_config()
    case = config.cases_by_id.get(payload.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Кейс не найден")

    weapons = config.weapons_by_case.get(case.id, [])
    if not weapons:
        raise HTTPException(status_code=400, detail="В кейсе нет оружий")

    with db.connect() as conn:
        user = fetch_user(conn, token)
        user = reset_daily(conn, user)
        if user["balance"] < case.price:
            raise HTTPException(status_code=400, detail="Недостаточно средств")

        drop = roll_case_drop(config, weapons)
        item_id = create_item(conn, user["id"], drop, status="owned", source="case", case_id=case.id)

        cases_won_inc = 1 if drop["price"] >= case.price else 0
        conn.execute(
            "UPDATE users SET balance = ?, cases_opened = cases_opened + 1, cases_won = cases_won + ?, daily_cases = daily_cases + 1 WHERE id = ?",
            (user["balance"] - case.price, cases_won_inc, user["id"]),
        )
        maybe_update_best(conn, user, item_id, is_upgrade=False)
        conn.commit()

        user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        push_feed(user["nickname"], drop)
        return {"drop": drop, "case_price": case.price, "user": user_payload(conn, user)}


@app.post("/api/item/sell")
def sell_item(payload: SellRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    with db.connect() as conn:
        user = fetch_user(conn, token)
        item = conn.execute(
            "SELECT * FROM items WHERE id = ? AND user_id = ? AND status = 'owned'",
            (payload.item_id, user["id"]),
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Оружие не найдено")

        conn.execute("UPDATE items SET status = 'sold' WHERE id = ?", (payload.item_id,))
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (item["price"], user["id"]),
        )
        update_max_balance(conn, user["id"])
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return {"user": user_payload(conn, user)}


@app.post("/api/upgrade/targets")
def upgrade_targets(payload: UpgradeTargetsRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    if payload.chance not in {75, 50, 30, 25, 15}:
        raise HTTPException(status_code=400, detail="Неверный шанс")

    config = get_config()

    with db.connect() as conn:
        user = fetch_user(conn, token)
        items = fetch_items(conn, user["id"], payload.item_ids)
        total = sum(item["price"] for item in items)
        if not total:
            return {"value": 0, "targets": [], "chance": payload.chance}

        target_value = total * (100 / payload.chance)
        targets = pick_upgrade_targets(config, target_value, count=8)
        return {
            "value": total,
            "chance": payload.chance,
            "targets": [weapon_payload(target) for target in targets],
        }


@app.post("/api/upgrade/start")
def upgrade_start(payload: UpgradeStartRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    if payload.chance not in {75, 50, 30, 25, 15}:
        raise HTTPException(status_code=400, detail="Неверный шанс")

    config = get_config()
    target = config.weapons_by_id.get(payload.target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Цель не найдена")

    with db.connect() as conn:
        user = fetch_user(conn, token)
        items = fetch_items(conn, user["id"], payload.item_ids)
        if not items:
            raise HTTPException(status_code=400, detail="Нет выбранных оружий")

        success = random.random() < (payload.chance / 100)
        total = sum(item["price"] for item in items)

        conn.execute(
            "UPDATE users SET upgrades = upgrades + 1 WHERE id = ?",
            (user["id"],),
        )

        if success:
            reward = roll_upgrade_reward(target)
            create_item(conn, user["id"], reward, status="owned", source="upgrade", case_id=None)
            conn.execute(
                "UPDATE items SET status = 'upgraded' WHERE id IN ({seq})".format(
                    seq=",".join("?" * len(items))
                ),
                [item["id"] for item in items],
            )
            conn.execute(
                "UPDATE users SET upgrade_wins = upgrade_wins + 1 WHERE id = ?",
                (user["id"],),
            )
            maybe_update_best(conn, user, reward["id"], is_upgrade=True)
            push_feed(user["nickname"], reward)
            result = {"success": True, "reward": reward, "consolation": 0}
        else:
            conn.execute(
                "UPDATE items SET status = 'failed' WHERE id IN ({seq})".format(
                    seq=",".join("?" * len(items))
                ),
                [item["id"] for item in items],
            )
            consolation = round(total * 0.05)
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (consolation, user["id"]),
            )
            update_max_balance(conn, user["id"])
            result = {"success": False, "reward": None, "consolation": consolation}

        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        result["user"] = user_payload(conn, user)
        return result


@app.get("/api/giveaways")
def giveaways() -> dict:
    config = get_config()
    now = int(time.time())
    giveaways = build_giveaways(config, now)
    return {"giveaways": giveaways}


@app.post("/api/giveaways/join")
def giveaways_join(payload: GiveawayJoinRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    config = get_config()
    current = {item["id"]: item for item in build_giveaways(config, int(time.time()))}
    giveaway = current.get(payload.giveaway_id)
    if not giveaway:
        raise HTTPException(status_code=404, detail="Розыгрыш не найден")

    with db.connect() as conn:
        user = fetch_user(conn, token)
        if user["balance"] < giveaway["entry"]:
            raise HTTPException(status_code=400, detail="Недостаточно средств")

        existing = conn.execute(
            "SELECT 1 FROM giveaway_entries WHERE user_id = ? AND giveaway_id = ?",
            (user["id"], payload.giveaway_id),
        ).fetchone()
        if existing:
            return {"joined": True, "user": user_payload(conn, user)}

        conn.execute(
            "INSERT OR IGNORE INTO giveaway_entries (user_id, giveaway_id, entry, joined_at) VALUES (?, ?, ?, ?)",
            (user["id"], payload.giveaway_id, giveaway["entry"], int(time.time())),
        )
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ?",
            (giveaway["entry"], user["id"]),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return {"joined": True, "user": user_payload(conn, user)}


@app.get("/api/notifications")
def notifications(authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    config = get_config()
    now = int(time.time())
    with db.connect() as conn:
        user = fetch_user(conn, token)
        rows = conn.execute(
            "SELECT giveaway_id, entry, joined_at FROM giveaway_entries WHERE user_id = ? ORDER BY joined_at DESC",
            (user["id"],),
        ).fetchall()

    notifications = []
    for row in rows:
        try:
            start = int(row["giveaway_id"])
        except (TypeError, ValueError):
            continue
        reward = giveaway_reward_for_start(config, start)
        status = "upcoming" if start > now else "finished"
        notifications.append(
            {
                "id": row["giveaway_id"],
                "start": start,
                "entry": row["entry"],
                "status": status,
                "reward": reward,
            }
        )
    return {"notifications": notifications}


@app.get("/api/feed")
def feed() -> dict:
    with FEED_LOCK:
        return {"items": list(FEED)}


@app.get("/api/top")
def top_players() -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.nickname, u.balance,
                   COALESCE(SUM(CASE WHEN i.status = 'owned' THEN i.price ELSE 0 END), 0) AS inventory
            FROM users u
            LEFT JOIN items i ON u.id = i.user_id
            GROUP BY u.id
            ORDER BY u.balance + inventory DESC
            LIMIT 10
            """
        ).fetchall()
        players = [
            {"nickname": row["nickname"], "total": int(row["balance"] + row["inventory"])}
            for row in rows
        ]
    return {"players": players}


@app.get("/")
def root() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")

@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(BASE_DIR / "styles.css")


@app.get("/app.js")
def script() -> FileResponse:
    return FileResponse(BASE_DIR / "app.js")


GUNS_DIR = BASE_DIR / "guns"
CASE_DIR = BASE_DIR / "case"
PARTIALS_DIR = BASE_DIR / "partials"

if GUNS_DIR.exists():
    app.mount("/guns", StaticFiles(directory=GUNS_DIR), name="guns")
if CASE_DIR.exists():
    app.mount("/case", StaticFiles(directory=CASE_DIR), name="case")
if PARTIALS_DIR.exists():
    app.mount("/partials", StaticFiles(directory=PARTIALS_DIR), name="partials")


# Helpers


def extract_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return authorization.strip()


def fetch_user(conn, token: str):
    user = conn.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Неверный токен")
    return user


def user_payload(conn, user_row) -> dict:
    inventory_rows = conn.execute(
        "SELECT * FROM items WHERE user_id = ? ORDER BY created_at DESC",
        (user_row["id"],),
    ).fetchall()

    inventory = [
        {
            "id": item["id"],
            "name": item["name"],
            "rarity": item["rarity"],
            "price": item["price"],
            "stattrak": bool(item["stattrak"]),
            "status": item["status"],
            "source": item["source"],
        }
        for item in inventory_rows
    ]

    best_drop = fetch_item(conn, user_row["best_drop_item_id"])
    best_upgrade = fetch_item(conn, user_row["best_upgrade_item_id"])

    return {
        "nickname": user_row["nickname"],
        "balance": user_row["balance"],
        "last_claim": user_row["last_claim"],
        "stats": {
            "cases_opened": user_row["cases_opened"],
            "cases_won": user_row["cases_won"],
            "upgrades": user_row["upgrades"],
            "upgrade_wins": user_row["upgrade_wins"],
            "max_balance": user_row["max_balance"],
            "daily_cases": user_row["daily_cases"],
            "best_drop": best_drop,
            "best_upgrade": best_upgrade,
        },
        "inventory": inventory,
    }


def fetch_item(conn, item_id: Optional[str]) -> Optional[dict]:
    if not item_id:
        return None
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return None
    return {
        "id": item["id"],
        "name": item["name"],
        "rarity": item["rarity"],
        "price": item["price"],
        "stattrak": bool(item["stattrak"]),
    }


def fetch_items(conn, user_id: int, item_ids: list[str]):
    if not item_ids:
        return []
    query = "SELECT * FROM items WHERE user_id = ? AND status = 'owned' AND id IN ({seq})".format(
        seq=",".join("?" * len(item_ids))
    )
    return conn.execute(query, [user_id, *item_ids]).fetchall()


def reset_daily(conn, user_row):
    today = today_key()
    if user_row["daily_reset"] != today:
        conn.execute(
            "UPDATE users SET daily_cases = 0, daily_reset = ? WHERE id = ?",
            (today, user_row["id"]),
        )
        conn.commit()
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_row["id"],)).fetchone()
    return user_row


def update_max_balance(conn, user_id: int) -> None:
    conn.execute(
        "UPDATE users SET max_balance = CASE WHEN balance > max_balance THEN balance ELSE max_balance END WHERE id = ?",
        (user_id,),
    )


def maybe_update_best(conn, user_row, item_id: str, is_upgrade: bool) -> None:
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return

    field = "best_upgrade_item_id" if is_upgrade else "best_drop_item_id"
    current_id = user_row[field]
    if current_id:
        current = conn.execute("SELECT price FROM items WHERE id = ?", (current_id,)).fetchone()
        if current and current["price"] >= item["price"]:
            return

    conn.execute(f"UPDATE users SET {field} = ? WHERE id = ?", (item_id, user_row["id"]))


def create_item(conn, user_id: int, drop: dict, status: str, source: str, case_id: Optional[str]) -> str:
    item_id = uuid.uuid4().hex
    drop["id"] = item_id
    conn.execute(
        "INSERT INTO items (id, user_id, name, rarity, price, stattrak, status, source, case_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item_id,
            user_id,
            drop["name"],
            drop["rarity"],
            drop["price"],
            int(drop.get("stattrak", False)),
            status,
            source,
            case_id,
            int(time.time()),
        ),
    )
    return item_id


def rarity_weights() -> list[tuple[str, float]]:
    return [(item["id"], item["weight"]) for item in RARITIES]


def roll_case_drop(config: Config, weapons: list) -> dict:
    rarity = pick_weighted_rarity()
    candidates = [weapon for weapon in weapons if weapon.rarity == rarity]
    if not candidates:
        candidates = weapons
    weapon = random.choice(candidates)
    return apply_stattrak(weapon)


def apply_stattrak(weapon) -> dict:
    is_stattrak = weapon.stattrak or random.random() < 0.05
    price = weapon.price
    if is_stattrak and not weapon.stattrak:
        price = round(price * 1.3)
    return {
        "id": uuid.uuid4().hex,
        "name": weapon.name,
        "rarity": weapon.rarity,
        "price": price,
        "stattrak": is_stattrak,
    }


def weapon_payload(weapon) -> dict:
    return {
        "id": weapon.id,
        "name": weapon.name,
        "rarity": weapon.rarity,
        "price": weapon.price,
        "stattrak": weapon.stattrak,
    }


def roll_upgrade_reward(target) -> dict:
    is_stattrak = target.stattrak or random.random() < 0.05
    price = target.price
    if is_stattrak and not target.stattrak:
        price = round(price * 1.3)
    return {
        "id": uuid.uuid4().hex,
        "name": target.name,
        "rarity": target.rarity,
        "price": price,
        "stattrak": is_stattrak,
    }


def pick_weighted_rarity() -> str:
    total = sum(weight for _, weight in rarity_weights())
    roll = random.random() * total
    for rarity, weight in rarity_weights():
        roll -= weight
        if roll <= 0:
            return rarity
    return RARITIES[0]["id"]


def pick_upgrade_targets(config: Config, target_value: float, count: int) -> list:
    weapons = config.weapons
    if not weapons:
        return []

    lower = target_value * 0.7
    upper = target_value * 1.3
    pool = [weapon for weapon in weapons if lower <= weapon.price <= upper]
    if len(pool) < count:
        pool = sorted(weapons, key=lambda w: abs(w.price - target_value))[: max(count, 8)]
    return random.sample(pool, k=min(count, len(pool)))


def push_feed(nickname: str, drop: dict) -> None:
    item = {
        "nickname": nickname,
        "weapon": drop["name"],
        "rarity": drop["rarity"],
        "price": drop["price"],
        "stattrak": drop.get("stattrak", False),
        "ts": int(time.time()),
    }
    with FEED_LOCK:
        FEED.appendleft(item)


def start_feed_thread() -> None:
    def worker():
        while True:
            time.sleep(random.randint(5, 8))
            config = get_config()
            if not config.cases:
                continue
            case = random.choice(config.cases)
            weapons = config.weapons_by_case.get(case.id, [])
            if not weapons:
                continue
            drop = roll_case_drop(config, weapons)
            push_feed(random_nickname(), drop)

    threading.Thread(target=worker, daemon=True).start()


def random_nickname() -> str:
    pool = ["Neo", "Fox", "Skull", "Zero", "Rex", "Nova", "Ice", "Fire", "Echo", "Ghost"]
    return f"{random.choice(pool)}{random.randint(1, 999)}"


def build_giveaways(config: Config, now: int) -> list[dict]:
    interval = 5 * 60 * 60
    next_start = ((now // interval) + 1) * interval
    entries = [199, 349, 549]
    giveaways = []
    for idx in range(3):
        start = next_start + idx * interval
        reward = giveaway_reward_for_start(config, start)
        giveaways.append(
            {
                "id": str(start),
                "entry": entries[min(idx, len(entries) - 1)],
                "start": start,
                "reward": reward,
            }
        )
    return giveaways


def giveaway_reward_for_start(config: Config, start: int) -> dict:
    rng = random.Random(start)
    return pick_giveaway_reward(config, rng)


def pick_giveaway_reward(config: Config, rng: random.Random) -> dict:
    rarity_pool = {"classified", "covert", "extraordinary"}
    candidates = [weapon for weapon in config.weapons if weapon.rarity in rarity_pool]
    if not candidates:
        candidates = config.weapons
    if not candidates:
        return {"id": uuid.uuid4().hex, "name": "Пусто", "rarity": "consumer", "price": 0, "stattrak": False}
    weapon = rng.choice(candidates)
    if weapon.stattrak:
        return weapon_payload(weapon)
    is_stattrak = rng.random() < 0.05
    price = weapon.price
    if is_stattrak:
        price = round(price * 1.3)
    return {"id": uuid.uuid4().hex, "name": weapon.name, "rarity": weapon.rarity, "price": price, "stattrak": is_stattrak}


def today_key() -> int:
    return int(datetime.utcnow().strftime("%Y%m%d"))
