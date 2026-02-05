#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import random
import sqlite3
import logging
import time
import asyncio
import shutil
from threading import Thread
from typing import List, Tuple, Any, Optional

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

# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "1812962224"))

DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/bot.db"
BACKUP_DIR = "backups"

START_COINS = int(os.getenv("START_COINS", "200"))
DAILY_REWARD = int(os.getenv("DAILY_REWARD", "100"))

SUMMON_COST = int(os.getenv("SUMMON_COST", "50"))
TEN_SUMMON_COST = int(os.getenv("TEN_SUMMON_COST", "500"))

INV_PAGE = int(os.getenv("INV_PAGE", "8"))

RARITY_RATE = {
    "Common": 55,
    "Rare": 25,
    "Epic": 15,
    "Legendary": 5
}
ALLOWED_RARITY = list(RARITY_RATE.keys())

BATTLE_CD = 600  # seconds (10 minutes)

# ===================== LOGGING =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ===================== DIRECTORIES =====================
for d in (DATA_DIR, BACKUP_DIR):
    if not os.path.exists(d):
        os.makedirs(d)

# ===================== DB CONNECTION (global) =====================
conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30, isolation_level=None)
try:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
except Exception:
    pass

def safe_execute(query: str, params: Tuple = (), fetchone: bool = False,
                 fetchall: bool = False, commit: bool = False):
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
        logger.exception("❌ DB error: %s | params=%s", query, params)
        return None

# ===================== SCHEMA =====================
safe_execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    coins INTEGER,
    level INTEGER,
    exp INTEGER,
    last_daily INTEGER,
    last_battle INTEGER DEFAULT 0
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

# ===================== BACKUP & RESTORE =====================
def backup_db() -> Optional[str]:
    """Create a timestamped backup copy of the current DB. Returns backup path or None."""
    try:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"bot_{timestamp}.db")
        target = sqlite3.connect(backup_file)
        with target:
            conn.backup(target)
        target.close()
        logger.info(f"💾 Database backup အောင်မြင်စွာ သိမ်းပြီး: {backup_file}")
        return backup_file
    except Exception:
        logger.exception("❌ Database backup မအောင်မြင်ပါ")
        return None

def list_backups() -> List[str]:
    files = []
    try:
        files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("bot_") and f.endswith(".db")])
    except Exception:
        logger.exception("Backup listing အမှား")
    return files

def restore_last_backup() -> bool:
    """Restore the latest backup into main DB file. Returns True on success."""
    global conn
    try:
        backups = list_backups()
        if not backups:
            logger.info("ℹ️ Restore: Backup file မရှိသေးပါ")
            return False
        last = os.path.join(BACKUP_DIR, backups[-1])
        logger.info(f"♻️ Restoring from latest backup: {last}")
        # Close existing connection
        try:
            conn.close()
        except Exception:
            pass
        # Copy file over DB_FILE
        shutil.copy(last, DB_FILE)
        # Reconnect
        conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        logger.info(f"♻️ Database auto-restore ပြီးဆောင်ရွက်ခဲ့သည်: {last}")
        return True
    except Exception:
        logger.exception("❌ Database auto-restore မအောင်မြင်ပါ")
        return False

def auto_backup(interval_sec: int = 3600):
    """Background thread: periodically backup DB."""
    def loop():
        while True:
            time.sleep(interval_sec)
            backup_db()
    t = Thread(target=loop, daemon=True)
    t.start()

# ===================== KEEP-ALIVE (Flask) =====================
app_web = Flask("")

@app_web.route("/")
def home():
    return "🤖 Tensura World Bot — Alive!"

def run_web():
    app_web.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()

# ===================== HELPERS =====================
def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def is_admin(uid: int) -> bool:
    if is_owner(uid):
        return True
    r = safe_execute("SELECT 1 FROM admins WHERE user_id=?", (uid,), fetchone=True)
    return bool(r)

def init_user(uid: int):
    safe_execute(
        "INSERT OR IGNORE INTO users(id, coins, level, exp, last_daily, last_battle) VALUES(?,?,?,?,?,?)",
        (uid, START_COINS, 1, 0, 0, 0), commit=True
    )

def roll_rarity() -> str:
    r = random.randint(1, 100)
    total = 0
    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k
    return "Common"

def add_inventory(uid: int, cid: int, amt: int = 1):
    cur = safe_execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?", (uid, cid), fetchone=True)
    if cur:
        safe_execute("UPDATE inventory SET count = count + ? WHERE user_id=? AND char_id=?", (amt, uid, cid), commit=True)
    else:
        safe_execute("INSERT INTO inventory(user_id, char_id, count) VALUES(?,?,?)", (uid, cid, amt), commit=True)

def add_exp(uid: int, amt: int = 0):
    row = safe_execute("SELECT level,exp FROM users WHERE id=?", (uid,), fetchone=True)
    if not row:
        return
    lvl, exp = row
    exp += amt
    while exp >= lvl * 100:
        exp -= lvl * 100
        lvl += 1
    safe_execute("UPDATE users SET level=?, exp=? WHERE id=?", (lvl, exp, uid), commit=True)

def format_char(row: Tuple[Any, ...]) -> str:
    return (
        f"🆔 ID: {row[0]}\n"
        f"✨ Name: {row[1]}\n"
        f"⭐ Rarity: {row[2]}\n"
        f"🏹 Faction: {row[3]}\n"
        f"💪 Power: {row[4]}\n"
        f"💰 Price: {row[5]}"
    )

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

# ===================== ANIMATIONS =====================
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

# ===================== CHAR SELECTION (sync/async) =====================
def choose_chars_sync(n: int) -> List[Tuple]:
    rows = safe_execute("SELECT * FROM characters", fetchall=True) or []
    if not rows:
        return []
    res = []
    for _ in range(n):
        r = roll_rarity()
        pool = [x for x in rows if x[2] == r] or rows
        res.append(random.choice(pool))
    return res

async def choose_chars(n: int) -> List[Tuple]:
    return choose_chars_sync(n)

# ===================== COMMANDS =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    text = (
        "🎮 Tensura World Gacha\n\n"
        "📌 Commands (မြန်မာ)\n\n"
        "/profile - မိမိအချက်အလက်\n"
        "/summon - Summon x1\n"
        "/summon10 - Summon x10\n"
        "/store - ဆိုင်\n"
        "/inventory - ကုန်အိတ်\n"
        "/daily - နေ့စဉ်ဆုပေး\n"
        "/balance - ချွေတာ\n"
        "/tops - အဆင့်စား\n"
        "/battle - တိုက်ရန် user message ကို reply လုပ်ပါ\n"
        "/gift - user message ကို reply လုပ်ပြီး /gift <char_id> <count>\n"
        "/addcoins - admin reply to user to give coins\n"
        "/upload - admin reply photo or send with args to upload character\n"
        "/restore - Owner only: restore latest backup\n"
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
        await update.message.reply_text(f"⏱ {h}h {m}m ကျန်ရှိပါတယ်")
        return
    safe_execute("UPDATE users SET coins = coins + ?, last_daily = ? WHERE id=?", (DAILY_REWARD, now, uid), commit=True)
    await update.message.reply_text(f"✅ +{DAILY_REWARD} coins ရရှိခဲ့သည်")

# ---- Summon x1 ----
async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    r = safe_execute("SELECT coins FROM users WHERE id=?", (uid,), fetchone=True)
    coins = r[0] if r else 0
    if coins < SUMMON_COST:
        await update.message.reply_text("❌ Coins မလုံလောက်ပါ")
        return
    safe_execute("UPDATE users SET coins=coins-? WHERE id=?", (SUMMON_COST, uid), commit=True)
    msg = await update.message.reply_text("🎰 Summon Initializing...")
    await summon_animation(msg)
    chars = await choose_chars(1)
    if not chars:
        await msg.edit_text("⚠ No Character Found")
        return
    ch = chars[0]
    add_inventory(uid, ch[0])
    add_exp(uid, 10)
    caption = "🌟 SUMMON RESULT 🌟\n\n" + format_char(ch)
    try:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=ch[6], caption=caption)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception:
        await msg.edit_text(caption)

# ---- Summon x10 ----
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
        await context.bot.send_message(chat_id, "⚠ Store ဖိုင် ထဲ ဗလာပါ")
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
    if q.data == "next_store":
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_store(q.message.chat.id, context)
        return
    if q.data.startswith("buy_"):
        cid = int(q.data.split("_")[1])
        char = safe_execute("SELECT * FROM characters WHERE id=?", (cid,), fetchone=True)
        if not char:
            await q.edit_message_caption("❌ Character မတွေ့ပါ")
            return
        r = safe_execute("SELECT coins FROM users WHERE id=?", (uid,), fetchone=True)
        coins = r[0] if r else 0
        if coins < char[5]:
            await q.edit_message_caption("❌ Coins မလုံလောက်ပါ")
            return
        safe_execute("UPDATE users SET coins=coins-? WHERE id=?", (char[5], uid), commit=True)
        add_inventory(uid, cid)
        try:
            await q.edit_message_caption(f"✅ Bought {char[1]}")
        except Exception:
            try:
                await q.message.reply_text(f"✅ Bought {char[1]}")
            except Exception:
                pass

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
        await update.message.reply_text("📦 Inventory သာမန်အားဖြင့် ဗလာပါ")
        return
    await send_inventory_page(update.effective_chat.id, context, pages, 0)

async def inv_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    pages = build_inventory_pages(uid)
    if not pages:
        await q.message.reply_text("📦 Inventory ဗလာပါ")
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
        await update.message.reply_text("⚠ Admin မဟုတ်ပါ")
        return
    photo_msg = None
    if update.message.photo:
        photo_msg = update.message
    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        photo_msg = update.message.reply_to_message
    if not photo_msg:
        await update.message.reply_text("📷 /upload လုပ်ချင်ရင် photo တစ်ပုံပို့ပါ သို့မဟုတ် photo ကို reply လုပ်ပြီး /upload")
        return
    args_text = " ".join(context.args).strip()
    # If args not provided, try to parse caption lines
    if not args_text:
        caption = update.message.caption or update.message.reply_to_message.caption if update.message.reply_to_message else ""
        if not caption:
            await update.message.reply_text("Usage: /upload Name|Rarity|Faction|Power|Price  OR attach caption lines (Name: X)")
            return
        data = {}
        for line in caption.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            data[k.strip().lower()] = v.strip()
        required = ["name", "rarity", "faction", "power", "price"]
        if not all(k in data for k in required):
            await update.message.reply_text("Caption မှာ name, rarity, faction, power, price သေချာရေးပါ")
            return
        try:
            power = int(data["power"])
            price = int(data["price"])
        except Exception:
            await update.message.reply_text("Power နှင့် Price က ဂဏန်းဖြစ်ရပါမယ်")
            return
        name = data["name"]
        rarity = data["rarity"]
        faction = data["faction"]
    else:
        parts = [p.strip() for p in args_text.split("|")]
        if len(parts) != 5:
            await update.message.reply_text("Usage: /upload Name|Rarity|Faction|Power|Price")
            return
        try:
            name, rarity, faction, power_s, price_s = parts
            power = int(power_s)
            price = int(price_s)
        except Exception:
            await update.message.reply_text("Power နှင့် Price က ဂဏန်းဖြစ်ရပါမယ်")
            return
    if rarity not in ALLOWED_RARITY:
        await update.message.reply_text(f"Rarity က {', '.join(ALLOWED_RARITY)} အထဲမှတစ်ခုဖြစ်ရမယ်")
        return
    # allow duplicate names but warn (original code allowed)
    file_id = photo_msg.photo[-1].file_id
    cur = safe_execute("""
        INSERT INTO characters (name, rarity, faction, power, price, file_id)
        VALUES (?,?,?,?,?,?)
    """, (name, rarity, faction, power, price, file_id), commit=True)
    new_id = None
    if cur:
        try:
            new_id = cur.lastrowid
        except Exception:
            row = safe_execute("SELECT id FROM characters WHERE name=? ORDER BY id DESC LIMIT 1", (name,), fetchone=True)
            new_id = row[0] if row else None
    await update.message.reply_text(f"✅ Uploaded! ID: {new_id} | Name: {name}")

# ================= TOPS LEADERBOARD =================
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

# ================= ADMIN: addadmin =================
async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⚠ Owner မှသာ အသုံးပြုနိုင်ပါသည်")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        target = int(context.args[0])
    except Exception:
        await update.message.reply_text("Invalid user_id")
        return
    safe_execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (target,), commit=True)
    await update.message.reply_text(f"✅ {target} ကို admin ထည့်ပြီးပါပြီ")

# ================= ADMIN: addcoins (reply) =================
async def addcoins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user.id
    if not is_admin(admin):
        await update.message.reply_text("⚠ Admin only")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠ User ကို reply လုပ်ပြီး /addcoins <amount>")
        return
    target = update.message.reply_to_message.from_user.id
    init_user(target)
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /addcoins <amount>")
        return
    try:
        amount = int(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Amount မမှန်ပါ")
        return
    if amount <= 0:
        await update.message.reply_text("❌ Amount must be > 0")
        return
    safe_execute("UPDATE users SET coins = coins + ? WHERE id=?", (amount, target), commit=True)
    await update.message.reply_text(f"✅ Added {amount} coins to {update.message.reply_to_message.from_user.first_name}")

# ================= GIFT CHARACTER (reply) =================
async def gift_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user.id
    init_user(sender)
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠ Reply to a user's message and use /gift <char_id> <count>")
        return
    receiver = update.message.reply_to_message.from_user.id
    init_user(receiver)
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /gift <char_id> <count>")
        return
    try:
        char_id = int(context.args[0]); count = int(context.args[1])
    except Exception:
        await update.message.reply_text("❌ ID / Count မမှန်ပါ")
        return
    if count <= 0:
        await update.message.reply_text("❌ Count must be > 0")
        return
    row = safe_execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?", (sender, char_id), fetchone=True)
    if not row or row[0] < count:
        await update.message.reply_text("❌ Character မလုံလောက်ပါ")
        return
    ch = safe_execute("SELECT name, rarity FROM characters WHERE id=?", (char_id,), fetchone=True)
    if not ch:
        await update.message.reply_text("❌ Character ID မရှိပါ")
        return
    name, rarity = ch
    safe_execute("UPDATE inventory SET count = count - ? WHERE user_id=? AND char_id=?", (count, sender, char_id), commit=True)
    add_inventory(receiver, char_id, count)
    safe_execute("DELETE FROM inventory WHERE user_id=? AND char_id=? AND count<=0", (sender, char_id), commit=True)
    await update.message.reply_text(
        f"🎁 Gift Success!\n\n📦 {name} ({rarity}) x{count}\n➡ Sent to {update.message.reply_to_message.from_user.first_name}"
    )

# ================= BATTLE (reply or id) =================
async def battle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    enemy_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        enemy_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            enemy_id = int(context.args[0])
        except Exception:
            enemy_id = None
    if not enemy_id:
        await update.message.reply_text("မှားနေသည် — တိုက်ချင်သူ၏ message ကို reply လုပ်ပြီး `/battle` လို့ပို့ပါ။")
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
    row2 = safe_execute("SELECT last_battle FROM users WHERE id=?", (enemy_id,), fetchone=True)
    enemy_last = row2[0] if row2 else 0
    if now - enemy_last < 10:
        await update.message.reply_text("Opponent is busy, try again a bit later.")
        return
    my_power = get_total_power(uid)
    enemy_power = get_total_power(enemy_id)
    if my_power == 0 or enemy_power == 0:
        await update.message.reply_text("⚠ တိုက်ရန် characters မရှိသေးပါ")
        return
    me_name = update.effective_user.first_name or str(uid)
    enemy_name = await get_user_name(context.bot, enemy_id)
    try:
        msg = await update.message.reply_text("⚔ Battle Initializing...")
    except Exception:
        msg = None
    if msg:
        await battle_animation(msg, me_name, enemy_name)
    if my_power > enemy_power:
        winner = uid; loser = enemy_id; win_name = me_name
    elif my_power < enemy_power:
        winner = enemy_id; loser = uid; win_name = enemy_name
    else:
        winner = random.choice([uid, enemy_id])
        loser = enemy_id if winner == uid else uid
        win_name = me_name if winner == uid else enemy_name
    reward = random.randint(80, 150)
    safe_execute("UPDATE users SET coins=coins+?, last_battle=? WHERE id=?", (reward, now, winner), commit=True)
    safe_execute("UPDATE users SET last_battle=? WHERE id=?", (now, loser), commit=True)
    add_exp(winner, 40); add_exp(loser, 15)
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

# ================= ADMIN: restore command =================
async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⚠ Owner only command")
        return
    ok = restore_last_backup()
    if ok:
        await update.message.reply_text("♻️ Auto-restore ပြီးပါပြီ။ Bot ကို restart လိုအပ်နိုင်သည်။")
    else:
        await update.message.reply_text("❌ Restore မအောင်မြင်ပါ — Backup မရှိသေးပါ သို့မဟုတ် အမှားရှိပါသည်")
# ================= BACKUPS LIST =================
async def backups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⚠ Owner only command")
        return
    files = list_backups()
    if not files:
        await update.message.reply_text("📂 Backup မရှိသေးပါ")
        return
    text = "📂 Backup List:\n\n"
    for i, f in enumerate(files, 1):
        text += f"{i}. {f}\n"
    await update.message.reply_text(text)
# ====== imports (ဖိုင်အထိ) ======
import sys
from threading import Thread  # အသုံးပြုထား already ရှိမယ်; ရှိပြန်ပါက ဒုတိယကြိမ် import မလိုပါ

# ====== /restart handler ======
async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Owner only
    if uid != OWNER_ID:
        await update.message.reply_text("⚠️ Owner only command")
        return

    # Create a manual backup before restart
    try:
        bfile = backup_db()
        if bfile:
            await update.message.reply_text(f"💾 Backup created: {os.path.basename(bfile)}\n🔄 Restarting bot...")
        else:
            await update.message.reply_text("⚠ Backup မအောင်မြင်သော်လည်း Restart ပြုလုပ်မယ်...")
    except Exception:
        # still attempt restart even if backup fails
        logger.exception("Backup before restart failed")
        await update.message.reply_text("⚠ Backup မအောင်မြင်သော်လည်း Restart ပြုလုပ်မည်။")

    # Restart strategy:
    # - If USE_SYSTEMD_RESTART=1 is set in env, exit process so systemd (or process manager) can restart it.
    # - Otherwise use os.execv to re-exec the Python process (works for most direct runs).
    def _do_restart():
        # small sleep to allow Telegram message to be sent
        time.sleep(1)
        try:
            if os.getenv("USE_SYSTEMD_RESTART", "0") == "1":
                logger.info("Restart via exit (systemd/process manager expected to restart).")
                os._exit(0)
            else:
                logger.info("Restart via os.execv (re-execing current Python process).")
                os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            logger.exception("Restart failed - exiting as fallback.")
            try:
                os._exit(0)
            except Exception:
                pass

    # Start restart in background so handler can return cleanly
    t = Thread(target=_do_restart, daemon=True)
    t.start()

# ===================== MAIN =====================
def main():
    keep_alive()
    # Restore latest backup at startup if exists
    try:
        restore_last_backup()
    except Exception:
        logger.exception("Startup restore failed (ignored)")
    # Start auto backup thread
    auto_backup(3600)  # backup every 1 hour (adjust as needed)

    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN မရှိပါ၊ .env ထဲမှာ ထည့်ပေးပါ။")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Basic
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("daily", daily))

    # Summon
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
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("addcoins", addcoins_cmd))
    app.add_handler(CommandHandler("gift", gift_cmd))          # reply mode
    app.add_handler(CommandHandler("battle", battle_cmd))

    # Admin restore / backup commands
    app.add_handler(CommandHandler("restore", restore_cmd))
    app.add_handler(CommandHandler("backups", backups_cmd))  # ✅ added here
    app.add_handler(CommandHandler("restart", restart_cmd))

    logger.info("✅ Bot စတင်လည်နေပါပြီ")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
