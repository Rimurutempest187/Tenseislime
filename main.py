#!/usr/bin/env python3
import os
import random
import sqlite3
import logging
import time
import asyncio
from threading import Thread
from typing import Optional, List, Tuple, Any

from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ================= KEEP ALIVE =================
app_web = Flask("")

@app_web.route("/")
def home():
    return "Bot Alive!"

def run_web():
    # Flask runs in background thread on port from env or 8080
    app_web.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()


# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "1812962224"))  # change if needed

DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/bot.db"

START_COINS = int(os.getenv("START_COINS", 200))
DAILY_REWARD = int(os.getenv("DAILY_REWARD", 100))

SUMMON_COST = int(os.getenv("SUMMON_COST", 50))
TEN_SUMMON_COST = int(os.getenv("TEN_SUMMON_COST", 500))

INV_PAGE = int(os.getenv("INV_PAGE", 8))

RARITY_RATE = {
    "Common": 55,
    "Rare": 25,
    "Epic": 15,
    "Legendary": 5
}

ALLOWED_RARITY = list(RARITY_RATE.keys())

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


# ================= DB =================
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30, isolation_level=None)

# Improve concurrency
try:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
except Exception:
    pass


def safe_execute(query: str, params: Tuple = (), fetchone: bool = False,
                 fetchall: bool = False, commit: bool = False):
    """
    Safe wrapper around sqlite operations.
    - fetchone=True -> returns single row or None
    - fetchall=True -> returns list of rows or []
    - else -> returns cursor or None
    """
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        if commit:
            conn.commit()
        if fetchone:
            return cur.fetchone()
        if fetchall:
            return cur.fetchall()
        return cur
    except Exception:
        logger.exception("DB error executing query: %s | params=%s", query, params)
        return None


# Create tables if not exists
safe_execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    coins INTEGER,
    level INTEGER,
    exp INTEGER,
    last_daily INTEGER
)
""", commit=True)

safe_execute("""
CREATE TABLE IF NOT EXISTS characters(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    rarity TEXT,
    faction TEXT,
    power INTEGER,
    price INTEGER,
    file_id TEXT
)
""", commit=True)

safe_execute("""
CREATE TABLE IF NOT EXISTS inventory(
    user_id INTEGER,
    char_id INTEGER,
    count INTEGER,
    PRIMARY KEY(user_id,char_id)
)
""", commit=True)

safe_execute("""
CREATE TABLE IF NOT EXISTS admins(
    user_id INTEGER PRIMARY KEY
)
""", commit=True)


# Ensure column exists utility (for altering safely)
def ensure_column(table: str, column: str, col_def: str):
    try:
        rows = safe_execute(f"PRAGMA table_info({table})", fetchall=True) or []
        cols = [r[1] for r in rows]
        if column not in cols:
            safe_execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}", commit=True)
    except Exception:
        # if alteration fails, log and continue
        logger.exception("Failed to ensure column %s on %s", column, table)


# Add last_battle column if missing
ensure_column("users", "last_battle", "INTEGER DEFAULT 0")


# ================= HELPERS =================
def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def is_admin(uid: int) -> bool:
    if is_owner(uid):
        return True
    r = safe_execute("SELECT 1 FROM admins WHERE user_id=?", (uid,), fetchone=True)
    return bool(r)

def init_user(uid: int):
    # create user with defaults if not exists
    try:
        safe_execute("INSERT OR IGNORE INTO users(id, coins, level, exp, last_daily, last_battle) VALUES(?,?,?,?,?,?)",
                     (uid, START_COINS, 1, 0, 0, 0), commit=True)
    except Exception:
        logger.exception("Failed to init user %s", uid)

def roll_rarity() -> str:
    r = random.randint(1, 100)
    total = 0
    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k
    return "Common"

def add_inventory(uid: int, cid: int, amt: int = 1):
    try:
        cur = safe_execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?", (uid, cid), fetchone=True)
        if cur:
            safe_execute("UPDATE inventory SET count = count + ? WHERE user_id=? AND char_id=?", (amt, uid, cid), commit=True)
        else:
            safe_execute("INSERT INTO inventory(user_id, char_id, count) VALUES(?,?,?)", (uid, cid, amt), commit=True)
    except Exception:
        logger.exception("add_inventory failed for %s %s", uid, cid)

def add_exp(uid: int, amt: int = 0):
    try:
        row = safe_execute("SELECT level,exp FROM users WHERE id=?", (uid,), fetchone=True)
        if not row:
            return
        lvl, exp = row
        exp += amt
        while exp >= lvl * 100:
            exp -= lvl * 100
            lvl += 1
        safe_execute("UPDATE users SET level=?, exp=? WHERE id=?", (lvl, exp, uid), commit=True)
    except Exception:
        logger.exception("add_exp failed for %s", uid)

def format_char(row: Tuple[Any, ...]) -> str:
    return (
        f"🆔 ID: {row[0]}\n"
        f"✨ Name: {row[1]}\n"
        f"⭐ Rarity: {row[2]}\n"
        f"🏹 Faction: {row[3]}\n"
        f"💪 Power: {row[4]}\n"
        f"💰 Price: {row[5]}"
    )

# ----------------- New helper: total power -----------------
def get_total_power(uid: int) -> int:
    rows = safe_execute("""
    SELECT characters.power, inventory.count
    FROM inventory
    JOIN characters ON inventory.char_id = characters.id
    WHERE inventory.user_id=?
    """, (uid,), fetchall=True) or []
    total = 0
    for power, count in rows:
        try:
            total += int(power) * int(count)
        except Exception:
            continue
    return total

# ================= SUMMON ANIMATION =================
async def summon_animation(msg):
    frames = [
        "🎰 Summoning...",
        "✨ Charging Mana...",
        "🌌 Opening Portal...",
        "⚡ Power Rising...",
        "💥 Breaking Seal...",
        "🌟 Revealing..."
    ]
    for f in frames:
        try:
            await msg.edit_text(f)
        except Exception:
            pass
        await asyncio.sleep(1)


# ================= BATTLE ANIMATION =================
async def battle_animation(msg, me, enemy):
    frames = [
        f"⚔ {me}  VS  {enemy}\n\n🔥 Preparing...",
        f"⚔ {me}  VS  {enemy}\n\n3️⃣ Ready...",
        f"⚔ {me}  VS  {enemy}\n\n2️⃣ Ready...",
        f"⚔ {me}  VS  {enemy}\n\n1️⃣ Ready...",
        f"💥 BATTLE START 💥\n\n{me} ➡ ⚔ ➡ {enemy}",
        f"💢 {enemy} Counter Attack!",
        f"🔥 Massive Damage!",
        f"⚡ Final Hit..."
    ]
    for f in frames:
        try:
            await msg.edit_text(f)
        except Exception:
            pass
        await asyncio.sleep(1.2)


# ================= BASIC COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    text = (
        "🎮 Tensura World Gacha\n\n"
        "📌 Commands\n\n"
        "/profile - မိမိအချက်အလက်\n"
        "/summon - Summon x1\n"
        "/summon10 - Summon x10\n"
        "/store - Shop\n"
        "/inventory - Bag\n"
        "/daily - Daily Reward\n"
        "/balance - Coins\n"
        "/tops - Ranking\n"
        "/battle <user_id> - PvP Battle\n"
    )
    await update.message.reply_text(text)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    r = safe_execute("SELECT coins FROM users WHERE id=?", (uid,), fetchone=True)
    coins = r[0] if r else 0
    await update.message.reply_text(f"💰 Coins: {coins}")


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    r = safe_execute("SELECT level, exp, coins FROM users WHERE id=?", (uid,), fetchone=True)
    if not r:
        await update.message.reply_text("Profile မရပါ")
        return
    lvl, exp, coins = r
    text = (
        f"👤 Profile\n\n"
        f"🆔 ID: {uid}\n"
        f"🎚 Level: {lvl}\n"
        f"📊 EXP: {exp}\n"
        f"💰 Coins: {coins}"
    )
    await update.message.reply_text(text)


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    now = int(time.time())
    r = safe_execute("SELECT last_daily FROM users WHERE id=?", (uid,), fetchone=True)
    last = r[0] if r else 0
    if now - last < 86400:
        left = 86400 - (now - last)
        h = left // 3600
        m = (left % 3600) // 60
        await update.message.reply_text(f"⏱ Wait {h}h {m}m")
        return
    safe_execute("UPDATE users SET coins = coins + ?, last_daily = ? WHERE id=?", (DAILY_REWARD, now, uid), commit=True)
    await update.message.reply_text(f"✅ +{DAILY_REWARD} coins")


# ================= SUMMON (ANIMATED) =================
async def choose_chars(n: int) -> List[Tuple]:
    rows = safe_execute("SELECT * FROM characters", fetchall=True)
    if not rows:
        return []
    res = []
    for _ in range(n):
        r = roll_rarity()
        pool = [x for x in rows if x[2] == r] or rows
        res.append(random.choice(pool))
    return res


async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = update.effective_user.id
    init_user(uid)

    r = safe_execute("SELECT coins FROM users WHERE id=?", (uid,), fetchone=True)
    coins = r[0] if r else 0

    if coins < SUMMON_COST:
        await update.message.reply_text("❌ Coins မလုံလောက်ပါ")
        return

    # deduct
    safe_execute("UPDATE users SET coins=coins-? WHERE id=?", (SUMMON_COST, uid), commit=True)

    # start animation
    msg = await update.message.reply_text("🎰 Summon Initializing...")

    await summon_animation(msg)

    # roll (choose_chars is async here)
    chars = await choose_chars(1)

    if not chars:
        await msg.edit_text("⚠ No Character Found")
        return

    ch = chars[0]

    add_inventory(uid, ch[0])
    add_exp(uid, 10)

    # reveal
    caption = (
        "🌟 SUMMON RESULT 🌟\n\n"
        f"{format_char(ch)}"
    )

    try:
        # try to send as photo, then delete the animation starter
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=ch[6],
            caption=caption
        )
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception:
        await msg.edit_text(caption)


async def summon10(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = update.effective_user.id
    init_user(uid)

    r = safe_execute("SELECT coins FROM users WHERE id=?", (uid,), fetchone=True)
    coins = r[0] if r else 0

    if coins < TEN_SUMMON_COST:
        await update.message.reply_text("❌ Coins မလုံလောက်ပါ")
        return

    safe_execute("UPDATE users SET coins=coins-? WHERE id=?", (TEN_SUMMON_COST, uid), commit=True)

    msg = await update.message.reply_text("🎰 10x Summon Initializing...")
    await summon_animation(msg)

    res = await choose_chars(10)

    text = "🌟 10x SUMMON RESULT 🌟\n\n"
    count = {}

    for ch in res:
        add_inventory(uid, ch[0])
        add_exp(uid, 10)
        key = f"{ch[1]} ({ch[2]})"
        count[key] = count.get(key, 0) + 1

    for k, v in count.items():
        text += f"{k} x{v}\n"

    try:
        await msg.edit_text(text)
    except Exception:
        await update.message.reply_text(text)


# ================= STORE =================
async def send_store(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    chars = safe_execute("SELECT * FROM characters", fetchall=True)
    if not chars:
        await context.bot.send_message(chat_id, "⚠ Store empty")
        return
    char = random.choice(chars)
    keyboard = [[
        InlineKeyboardButton("Buy", callback_data=f"buy_{char[0]}"),
        InlineKeyboardButton("Next", callback_data="next_store")
    ]]
    try:
        await context.bot.send_photo(chat_id, char[6], caption=format_char(char),
                                     reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await context.bot.send_message(chat_id, format_char(char), reply_markup=InlineKeyboardMarkup(keyboard))


async def store_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_store(update.effective_chat.id, context)


async def store_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    init_user(uid)

    # NEXT
    if q.data == "next_store":
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_store(q.message.chat.id, context)
        return

    # BUY
    if q.data.startswith("buy_"):
        cid = int(q.data.split("_")[1])

        char = safe_execute("SELECT * FROM characters WHERE id=?", (cid,), fetchone=True)
        if not char:
            await q.edit_message_caption("❌ Character not found")
            return

        r = safe_execute("SELECT coins FROM users WHERE id=?", (uid,), fetchone=True)
        coins = r[0] if r else 0

        if coins < char[5]:
            await q.edit_message_caption("❌ Not enough coins")
            return

        safe_execute("UPDATE users SET coins=coins-? WHERE id=?", (char[5], uid), commit=True)
        add_inventory(uid, cid)
        await q.edit_message_caption(f"✅ Bought {char[1]}")


# ================= INVENTORY =================
def build_inventory_pages(uid: int):
    rows = safe_execute("""
    SELECT characters.id, characters.name, characters.rarity, inventory.count
    FROM inventory
    JOIN characters ON inventory.char_id=characters.id
    WHERE inventory.user_id=?
    ORDER BY characters.id
    """, (uid,), fetchall=True) or []
    pages = [rows[i:i+INV_PAGE] for i in range(0, len(rows), INV_PAGE)]
    return pages


async def send_inventory_page(chat_id: int, context: ContextTypes.DEFAULT_TYPE, pages: List[List[Tuple]], idx: int):
    page = pages[idx]
    text = f"📦 Inventory Page {idx+1}/{len(pages)}\n\n"
    for i, row in enumerate(page, 1):
        cid, name, rarity, count = row
        text += f"{i}. {name} ({rarity}) x{count} — ID:{cid}\n"

    buttons = []
    nav_buttons = []
    if idx > 0:
        nav_buttons.append(InlineKeyboardButton("⬅ Prev", callback_data=f"inv_{idx-1}"))
    if idx < len(pages)-1:
        nav_buttons.append(InlineKeyboardButton("Next ➡", callback_data=f"inv_{idx+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    await context.bot.send_message(chat_id, text, reply_markup=reply_markup)


async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    pages = build_inventory_pages(uid)
    if not pages:
        await update.message.reply_text("📦 Inventory empty")
        return
    await send_inventory_page(update.effective_chat.id, context, pages, 0)


async def inv_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    pages = build_inventory_pages(uid)
    if not pages:
        await q.message.reply_text("📦 Inventory empty")
        return
    try:
        idx = int(q.data.split("_")[1])
    except Exception:
        idx = 0
    try:
        await q.message.delete()
    except Exception:
        pass
    await send_inventory_page(q.message.chat.id, context, pages, idx)


# ================= ADMIN UPLOAD =================
async def upload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("⚠ Admin only")
        return

    # ===== Get Photo =====
    photo_msg = None

    if update.message.photo:
        photo_msg = update.message

    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        photo_msg = update.message.reply_to_message

    if not photo_msg:
        await update.message.reply_text("📷 Send photo with /upload or reply to photo")
        return

    # ===== Get Args =====
    args_text = " ".join(context.args).strip()

    # ================= CAPTION MODE =================
    if not args_text:
        caption = update.message.caption or ""
        if not caption:
            await update.message.reply_text("Usage: /upload Name|Rarity|Faction|Power|Price  OR add caption lines like Name: X")
            return

        data = {}
        for line in caption.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            data[k.strip().lower()] = v.strip()

        required = ["name", "rarity", "faction", "power", "price"]
        if not all(k in data for k in required):
            await update.message.reply_text(
                "Caption must include:\nname, rarity, faction, power, price"
            )
            return

        try:
            power = int(data["power"])
            price = int(data["price"])
        except Exception:
            await update.message.reply_text("Power / Price must be numbers")
            return

        name = data["name"]
        rarity = data["rarity"]
        faction = data["faction"]

        if rarity not in ALLOWED_RARITY:
            await update.message.reply_text(f"Rarity must be one of: {', '.join(ALLOWED_RARITY)}")
            return

        # Warning if name exists (allowed)
        if safe_execute("SELECT id FROM characters WHERE name=?", (name,), fetchone=True):
            await update.message.reply_text("⚠ Name already exists (Allowed)")

        file_id = photo_msg.photo[-1].file_id

        cur = safe_execute("""
            INSERT INTO characters
            (name, rarity, faction, power, price, file_id)
            VALUES (?,?,?,?,?,?)
        """, (name, rarity, faction, power, price, file_id), commit=True)

        new_id = None
        if cur:
            try:
                new_id = cur.lastrowid
            except Exception:
                row = safe_execute("SELECT id FROM characters WHERE name=? ORDER BY id DESC LIMIT 1", (name,), fetchone=True)
                new_id = row[0] if row else None

        await update.message.reply_text(f"✅ Uploaded!\nID: {new_id}\nName: {name}")
        return

    # ================= PIPE MODE =================
    parts = [p.strip() for p in args_text.split("|")]

    if len(parts) != 5:
        await update.message.reply_text(
            "Usage: /upload Name|Rarity|Faction|Power|Price"
        )
        return

    try:
        name, rarity, faction, power, price = parts
        power = int(power)
        price = int(price)
    except Exception:
        await update.message.reply_text("Power / Price must be numbers")
        return

    if rarity not in ALLOWED_RARITY:
        await update.message.reply_text(f"Rarity must be one of: {', '.join(ALLOWED_RARITY)}")
        return

    # Warning if name exists (Allowed)
    if safe_execute("SELECT id FROM characters WHERE name=?", (name,), fetchone=True):
        await update.message.reply_text("⚠ Name already exists (Allowed)")

    file_id = photo_msg.photo[-1].file_id

    cur = safe_execute("""
        INSERT INTO characters
        (name, rarity, faction, power, price, file_id)
        VALUES (?,?,?,?,?,?)
    """, (name, rarity, faction, power, price, file_id), commit=True)

    new_id = None
    if cur:
        try:
            new_id = cur.lastrowid
        except Exception:
            row = safe_execute("SELECT id FROM characters WHERE name=? ORDER BY id DESC LIMIT 1", (name,), fetchone=True)
            new_id = row[0] if row else None

    await update.message.reply_text(f"✅ Uploaded!\nID: {new_id}\nName: {name}")


# ================= TOPS LEADERBOARD ================
async def get_user_name(bot, user_id: int) -> str:
    try:
        user = await bot.get_chat(user_id)
        if getattr(user, "username", None):
            return "@" + user.username
        if getattr(user, "first_name", None):
            return user.first_name
        return str(user_id)
    except Exception:
        return str(user_id)


async def tops_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = safe_execute("""
    SELECT id, level, exp, coins
    FROM users
    ORDER BY level DESC, exp DESC, coins DESC
    LIMIT 10
    """, fetchall=True) or []

    if not rows:
        await update.message.reply_text("⚠ User မရှိသေးပါ")
        return

    text = "🏆 <b>Top Players Ranking</b>\n\n"

    for idx, row in enumerate(rows, 1):
        uid, lvl, exp, coins = row
        name = await get_user_name(context.bot, uid)
        text += (
            f"#{idx} {name}\n"
            f"   🎚 Level: {lvl}\n"
            f"   💰 Coins: {coins}\n"
            f"   📊 EXP: {exp}\n\n"
        )

    await update.message.reply_text(text, parse_mode="HTML")


# ================= ADMIN COMMANDS =================
async def addcoins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⚠ Admin only")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /addcoins <user_id> <amount>")
        return
    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid user_id or amount")
        return
    init_user(target_id)
    safe_execute("UPDATE users SET coins=coins+? WHERE id=?", (amount, target_id), commit=True)
    await update.message.reply_text(f"✅ Added {amount} coins to {target_id}")


async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⚠ Owner only")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        target_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("Invalid user_id")
        return
    try:
        safe_execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (target_id,), commit=True)
    except Exception:
        await update.message.reply_text("User is already admin or DB error")
        return
    await update.message.reply_text(f"✅ {target_id} added as admin")


# ================= GIFT (user -> user) =================
async def gift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /gift <user_id> <amount>")
        return
    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except Exception:
        await update.message.reply_text("Invalid format")
        return
    if amount <= 0:
        await update.message.reply_text("Amount must be positive")
        return
    r = safe_execute("SELECT coins FROM users WHERE id=?", (uid,), fetchone=True)
    coins = r[0] if r else 0
    if coins < amount:
        await update.message.reply_text("❌ Coins မလုံလောက်ပါ")
        return
    init_user(target)
    safe_execute("UPDATE users SET coins=coins-? WHERE id=?", (amount, uid), commit=True)
    safe_execute("UPDATE users SET coins=coins+? WHERE id=?", (amount, target), commit=True)
    await update.message.reply_text("✅ Gift sent!")


# ================= BATTLE (ANIMATED) =================
BATTLE_CD = 600  # 10 minutes cooldown

async def battle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = update.effective_user.id
    init_user(uid)

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /battle <user_id>")
        return

    try:
        enemy_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("User ID မမှန်ပါ")
        return

    if enemy_id == uid:
        await update.message.reply_text("ကိုယ့်ကိုယ်ကို မတိုက်နိုင်ပါ")
        return

    init_user(enemy_id)

    now = int(time.time())

    row = safe_execute("SELECT last_battle FROM users WHERE id=?", (uid,), fetchone=True)
    last = row[0] if row else 0

    if now - last < BATTLE_CD:
        left = BATTLE_CD - (now-last)
        await update.message.reply_text(f"⏱ {left//60} မိနစ်နောက်မှ ပြန်တိုက်ပါ")
        return

    my_power = get_total_power(uid)
    enemy_power = get_total_power(enemy_id)

    if my_power == 0 or enemy_power == 0:
        await update.message.reply_text("⚠ Character မရှိရင် မတိုက်နိုင်ပါ")
        return

    me_name = update.effective_user.first_name or str(uid)
    enemy_name = await get_user_name(context.bot, enemy_id)

    # start msg for animation
    try:
        msg = await update.message.reply_text("⚔ Battle Initializing...")
    except Exception:
        msg = None

    if msg:
        await battle_animation(msg, me_name, enemy_name)

    # determine winner
    if my_power > enemy_power:
        winner = uid
        loser = enemy_id
        win_name = me_name
    elif my_power < enemy_power:
        winner = enemy_id
        loser = uid
        win_name = enemy_name
    else:
        if msg:
            await msg.edit_text("🤝 Draw! No Winner")
        else:
            await update.message.reply_text("🤝 Draw! No Winner")
        return

    reward = random.randint(80, 150)

    safe_execute("UPDATE users SET coins=coins+?, last_battle=? WHERE id=?", (reward, now, winner), commit=True)
    safe_execute("UPDATE users SET last_battle=? WHERE id=?", (now, loser), commit=True)

    add_exp(winner, 40)
    add_exp(loser, 15)

    final_text = (
        f"🏆 BATTLE RESULT 🏆\n\n"
        f"🔥 {me_name}: {my_power}\n"
        f"💀 {enemy_name}: {enemy_power}\n\n"
        f"👑 Winner: {win_name}\n"
        f"💰 +{reward} Coins\n"
        f"⭐ +40 EXP"
    )

    if msg:
        await msg.edit_text(final_text)
    else:
        await update.message.reply_text(final_text)


# ================= MAIN =================
def main():
    keep_alive()
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing in environment")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Basic
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("daily", daily))

    # Summon (animated)
    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("summon10", summon10))

    # Store
    app.add_handler(CommandHandler("store", store_cmd))
    app.add_handler(CallbackQueryHandler(store_btn, pattern=r'^(buy_\d+|next_store)$'))

    # Inventory
    app.add_handler(CommandHandler("inventory", inventory_cmd))
    app.add_handler(CallbackQueryHandler(inv_btn, pattern=r'^inv_\d+$'))

    # Admin Upload
    app.add_handler(CommandHandler("upload", upload_cmd))

    # Leaderboard
    app.add_handler(CommandHandler("tops", tops_cmd))

    # Admin Commands
    app.add_handler(CommandHandler("addcoins", addcoins_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))

    # Gift
    app.add_handler(CommandHandler("gift", gift_cmd))

    # Battle
    app.add_handler(CommandHandler("battle", battle_cmd))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
