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
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import db
from .config import Config, RARITIES, get_config


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


def users():
    return db.users_col()


def items():
    return db.items_col()


def giveaway_entries():
    return db.giveaway_entries_col()


@app.on_event("startup")
def on_startup() -> None:
    # Creates indexes (idempotent). In serverless this runs on each cold start.
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

    user = users().find_one({"nickname": nickname})
    if user is None:
        now = int(time.time())
        user_doc = {
            "nickname": nickname,
            "token": token,
            "balance": 500,
            "last_claim": 0,
            "cases_opened": 0,
            "cases_won": 0,
            "upgrades": 0,
            "upgrade_wins": 0,
            "max_balance": 500,
            "best_drop_item_id": None,
            "best_upgrade_item_id": None,
            "daily_cases": 0,
            "daily_reset": today_key(),
            "created_at": now,
            "updated_at": now,
        }
        res = users().insert_one(user_doc)
        user = users().find_one({"_id": res.inserted_id})
    else:
        users().update_one({"_id": user["_id"]}, {"$set": {"token": token, "updated_at": int(time.time())}})
        user = users().find_one({"_id": user["_id"]})

    user = reset_daily(user)
    return {"token": token, "user": user_payload(user)}


@app.get("/api/me")
def me(authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    user = fetch_user(token)
    user = reset_daily(user)
    return {"user": user_payload(user)}


@app.post("/api/balance/claim")
def claim_bonus(authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    now = int(time.time())
    cooldown = 20 * 60

    user = fetch_user(token)
    user = reset_daily(user)
    if now - int(user.get("last_claim", 0)) < cooldown:
        return {
            "claimed": False,
            "user": user_payload(user),
            "next_claim": int(user.get("last_claim", 0)) + cooldown,
        }

    # Atomic increment + max_balance update
    user = users().find_one_and_update(
        {"_id": user["_id"]},
        [
            {"$set": {"balance": {"$add": ["$balance", 100]}, "last_claim": now}},
            {"$set": {"max_balance": {"$max": ["$balance", {"$ifNull": ["$max_balance", 0]}]}}},
            {"$set": {"updated_at": now}},
        ],
        return_document=ReturnDocument.AFTER,
    )

    return {"claimed": True, "user": user_payload(user), "next_claim": now + cooldown}


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

    user = fetch_user(token)
    user = reset_daily(user)

    if int(user.get("balance", 0)) < int(case.price):
        raise HTTPException(status_code=400, detail="Недостаточно средств")

    drop = roll_case_drop(config, weapons)
    item_id = create_item(user["_id"], drop, status="owned", source="case", case_id=case.id)

    cases_won_inc = 1 if int(drop["price"]) >= int(case.price) else 0
    users().update_one(
        {"_id": user["_id"]},
        {
            "$inc": {"balance": -int(case.price), "cases_opened": 1, "cases_won": cases_won_inc, "daily_cases": 1},
            "$set": {"updated_at": int(time.time())},
        },
    )

    maybe_update_best(user, item_id, is_upgrade=False)

    user = users().find_one({"_id": user["_id"]})
    push_feed(user["nickname"], drop)
    return {"drop": drop, "case_price": case.price, "user": user_payload(user)}


@app.post("/api/item/sell")
def sell_item(payload: SellRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    user = fetch_user(token)

    item = items().find_one({"_id": payload.item_id, "user_id": user["_id"], "status": "owned"})
    if not item:
        raise HTTPException(status_code=404, detail="Оружие не найдено")

    items().update_one({"_id": payload.item_id}, {"$set": {"status": "sold"}})
    users().update_one({"_id": user["_id"]}, {"$inc": {"balance": int(item["price"])}, "$set": {"updated_at": int(time.time())}})
    update_max_balance(user["_id"])

    user = users().find_one({"_id": user["_id"]})
    return {"user": user_payload(user)}


@app.post("/api/upgrade/targets")
def upgrade_targets(payload: UpgradeTargetsRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    if payload.chance not in {75, 50, 30, 25, 15}:
        raise HTTPException(status_code=400, detail="Неверный шанс")

    config = get_config()

    user = fetch_user(token)
    selected_items = fetch_items(user["_id"], payload.item_ids)
    total = sum(int(item["price"]) for item in selected_items)
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

    user = fetch_user(token)

    selected_items = fetch_items(user["_id"], payload.item_ids)
    if not selected_items:
        raise HTTPException(status_code=400, detail="Нет выбранных оружий")

    success = random.random() < (payload.chance / 100)
    total = sum(int(item["price"]) for item in selected_items)

    users().update_one({"_id": user["_id"]}, {"$inc": {"upgrades": 1}, "$set": {"updated_at": int(time.time())}})

    if success:
        reward = roll_upgrade_reward(target)
        reward_item_id = create_item(user["_id"], reward, status="owned", source="upgrade", case_id=None)

        items().update_many(
            {"_id": {"$in": [it["_id"] for it in selected_items]}, "user_id": user["_id"], "status": "owned"},
            {"$set": {"status": "upgraded"}},
        )
        users().update_one({"_id": user["_id"]}, {"$inc": {"upgrade_wins": 1}})

        # refresh user for best comparison
        user = users().find_one({"_id": user["_id"]})
        maybe_update_best(user, reward_item_id, is_upgrade=True)

        push_feed(user["nickname"], reward)
        result = {"success": True, "reward": reward, "consolation": 0}
    else:
        items().update_many(
            {"_id": {"$in": [it["_id"] for it in selected_items]}, "user_id": user["_id"], "status": "owned"},
            {"$set": {"status": "failed"}},
        )
        consolation = round(total * 0.05)
        users().update_one({"_id": user["_id"]}, {"$inc": {"balance": int(consolation)}})
        update_max_balance(user["_id"])
        result = {"success": False, "reward": None, "consolation": consolation}

    user = users().find_one({"_id": user["_id"]})
    result["user"] = user_payload(user)
    return result


@app.get("/api/giveaways")
def giveaways() -> dict:
    config = get_config()
    now = int(time.time())
    giveaways_list = build_giveaways(config, now)
    return {"giveaways": giveaways_list}


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

    user = fetch_user(token)
    if int(user.get("balance", 0)) < int(giveaway["entry"]):
        raise HTTPException(status_code=400, detail="Недостаточно средств")

    existing = giveaway_entries().find_one({"user_id": user["_id"], "giveaway_id": payload.giveaway_id})
    if existing:
        return {"joined": True, "user": user_payload(user)}

    try:
        giveaway_entries().insert_one(
            {
                "user_id": user["_id"],
                "giveaway_id": payload.giveaway_id,
                "entry": int(giveaway["entry"]),
                "joined_at": int(time.time()),
            }
        )
    except DuplicateKeyError:
        # Race condition: already inserted
        return {"joined": True, "user": user_payload(user)}

    users().update_one({"_id": user["_id"]}, {"$inc": {"balance": -int(giveaway["entry"])}, "$set": {"updated_at": int(time.time())}})
    user = users().find_one({"_id": user["_id"]})
    return {"joined": True, "user": user_payload(user)}


@app.get("/api/notifications")
def notifications(authorization: Optional[str] = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Нет токена")

    config = get_config()
    now = int(time.time())

    user = fetch_user(token)
    rows = list(giveaway_entries().find({"user_id": user["_id"]}).sort("joined_at", -1))

    notifications_list = []
    for row in rows:
        try:
            start = int(row["giveaway_id"])
        except (TypeError, ValueError, KeyError):
            continue
        reward = giveaway_reward_for_start(config, start)
        status = "upcoming" if start > now else "finished"
        notifications_list.append(
            {
                "id": row["giveaway_id"],
                "start": start,
                "entry": int(row["entry"]),
                "status": status,
                "reward": reward,
            }
        )
    return {"notifications": notifications_list}


@app.get("/api/feed")
def feed() -> dict:
    with FEED_LOCK:
        return {"items": list(FEED)}


@app.get("/api/top")
def top_players() -> dict:
    pipeline = [
        {
            "$lookup": {
                "from": "items",
                "let": {"uid": "$_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$user_id", "$$uid"]},
                                    {"$eq": ["$status", "owned"]},
                                ]
                            }
                        }
                    },
                    {"$group": {"_id": None, "sum": {"$sum": "$price"}}},
                ],
                "as": "inv",
            }
        },
        {"$addFields": {"inventory": {"$ifNull": [{"$arrayElemAt": ["$inv.sum", 0]}, 0]}}},
        {"$addFields": {"total": {"$add": ["$balance", "$inventory"]}}},
        {"$sort": {"total": -1}},
        {"$limit": 10},
        {"$project": {"_id": 0, "nickname": 1, "total": 1}},
    ]

    players = list(users().aggregate(pipeline))
    # Ensure ints for JSON
    players = [{"nickname": p["nickname"], "total": int(p.get("total", 0))} for p in players]
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


def fetch_user(token: str) -> dict:
    user = users().find_one({"token": token})
    if not user:
        raise HTTPException(status_code=401, detail="Неверный токен")
    return user


def user_payload(user_row: dict) -> dict:
    inventory_rows = list(items().find({"user_id": user_row["_id"]}).sort("created_at", -1))

    inventory = [
        {
            "id": item["_id"],
            "name": item["name"],
            "rarity": item["rarity"],
            "price": int(item["price"]),
            "stattrak": bool(item.get("stattrak", False)),
            "status": item["status"],
            "source": item["source"],
        }
        for item in inventory_rows
    ]

    best_drop = fetch_item(user_row.get("best_drop_item_id"))
    best_upgrade = fetch_item(user_row.get("best_upgrade_item_id"))

    return {
        "nickname": user_row["nickname"],
        "balance": int(user_row.get("balance", 0)),
        "last_claim": int(user_row.get("last_claim", 0)),
        "stats": {
            "cases_opened": int(user_row.get("cases_opened", 0)),
            "cases_won": int(user_row.get("cases_won", 0)),
            "upgrades": int(user_row.get("upgrades", 0)),
            "upgrade_wins": int(user_row.get("upgrade_wins", 0)),
            "max_balance": int(user_row.get("max_balance", 0)),
            "daily_cases": int(user_row.get("daily_cases", 0)),
            "best_drop": best_drop,
            "best_upgrade": best_upgrade,
        },
        "inventory": inventory,
    }


def fetch_item(item_id: Optional[str]) -> Optional[dict]:
    if not item_id:
        return None
    item = items().find_one({"_id": item_id})
    if not item:
        return None
    return {
        "id": item["_id"],
        "name": item["name"],
        "rarity": item["rarity"],
        "price": int(item["price"]),
        "stattrak": bool(item.get("stattrak", False)),
    }


def fetch_items(user_id, item_ids: list[str]):
    if not item_ids:
        return []
    return list(items().find({"user_id": user_id, "status": "owned", "_id": {"$in": item_ids}}))


def reset_daily(user_row: dict):
    today = today_key()
    if int(user_row.get("daily_reset", 0)) != today:
        users().update_one(
            {"_id": user_row["_id"]},
            {"$set": {"daily_cases": 0, "daily_reset": today, "updated_at": int(time.time())}},
        )
        return users().find_one({"_id": user_row["_id"]})
    return user_row


def update_max_balance(user_id) -> None:
    users().update_one(
        {"_id": user_id},
        [{"$set": {"max_balance": {"$max": ["$balance", {"$ifNull": ["$max_balance", 0]}]}}}],
    )


def maybe_update_best(user_row: dict, item_id: str, is_upgrade: bool) -> None:
    item = items().find_one({"_id": item_id})
    if not item:
        return

    field = "best_upgrade_item_id" if is_upgrade else "best_drop_item_id"
    current_id = user_row.get(field)
    if current_id:
        current = items().find_one({"_id": current_id}, {"price": 1})
        if current and int(current.get("price", 0)) >= int(item.get("price", 0)):
            return

    users().update_one({"_id": user_row["_id"]}, {"$set": {field: item_id}})


def create_item(user_id, drop: dict, status: str, source: str, case_id: Optional[str]) -> str:
    item_id = uuid.uuid4().hex
    drop["id"] = item_id

    items().insert_one(
        {
            "_id": item_id,
            "user_id": user_id,
            "name": drop["name"],
            "rarity": drop["rarity"],
            "price": int(drop["price"]),
            "stattrak": bool(drop.get("stattrak", False)),
            "status": status,
            "source": source,
            "case_id": case_id,
            "created_at": int(time.time()),
        }
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
    giveaways_list = []
    for idx in range(3):
        start = next_start + idx * interval
        reward = giveaway_reward_for_start(config, start)
        giveaways_list.append(
            {
                "id": str(start),
                "entry": entries[min(idx, len(entries) - 1)],
                "start": start,
                "reward": reward,
            }
        )
    return giveaways_list


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
