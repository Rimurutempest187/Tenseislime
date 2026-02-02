# ================= Tensura World Gacha Bot =================

import os
import random
import sqlite3
import logging
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 1812962224   # <-- Your Telegram ID

DATA_DIR = "data"
DB_FILE = os.path.join(DATA_DIR, "gacha.db")

START_COINS = 100
SUMMON_COST = 20
TEN_SUMMON_COST = 180
DAILY_REWARD = 50
INV_PAGE_SIZE = 8

RARITY_RATE = {
    "Common": 60,
    "Rare": 25,
    "Epic": 10,
    "Legendary": 5
}

os.makedirs(DATA_DIR, exist_ok=True)

# ================= LOGGER =================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= DATABASE =================

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()

# Users
c.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    coins INTEGER,
    lvl INTEGER,
    exp INTEGER,
    last_daily INTEGER
)
""")

# Characters (NO UNIQUE NAME)
c.execute("""
CREATE TABLE IF NOT EXISTS characters(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    rarity TEXT,
    faction TEXT,
    power INTEGER,
    price INTEGER,
    file_id TEXT
)
""")

# Inventory
c.execute("""
CREATE TABLE IF NOT EXISTS inventory(
    user_id INTEGER,
    char_id INTEGER,
    count INTEGER,
    PRIMARY KEY(user_id,char_id)
)
""")

# Admins
c.execute("""
CREATE TABLE IF NOT EXISTS admins(
    user_id INTEGER PRIMARY KEY
)
""")

conn.commit()


# ================= HELPERS =================

def is_owner(uid):
    return uid == OWNER_ID


def is_admin(uid):
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    return is_owner(uid) or c.fetchone() is not None


def init_user(uid):
    c.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    if not c.fetchone():
        c.execute("""
        INSERT INTO users VALUES (?,?,?,?,?)
        """, (uid, START_COINS, 1, 0, 0))
        conn.commit()


def roll_rarity():
    r = random.randint(1, 100)
    total = 0

    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k

    return "Common"


def add_inventory(uid, cid, n=1):

    c.execute("""
    SELECT count FROM inventory
    WHERE user_id=? AND char_id=?
    """, (uid, cid))

    row = c.fetchone()

    if row:
        c.execute("""
        UPDATE inventory
        SET count = count + ?
        WHERE user_id=? AND char_id=?
        """, (n, uid, cid))
    else:
        c.execute("""
        INSERT INTO inventory VALUES (?,?,?)
        """, (uid, cid, n))

    conn.commit()


def format_char(ch):

    return (
        f"🆔 ID: {ch[0]}\n"
        f"✨ Name: {ch[1]}\n"
        f"⭐ Rarity: {ch[2]}\n"
        f"🏹 Faction: {ch[3]}\n"
        f"💪 Power: {ch[4]}\n"
        f"💰 Price: {ch[5]}"
    )


# ================= BASIC =================

async def start(update: Update, context):

    uid = update.effective_user.id
    init_user(uid)

    await update.message.reply_text(
        "🎮 Tensura World Gacha\n\n"
        "/summon\n"
        "/summon10\n"
        "/store\n"
        "/inventory\n"
        "/balance\n"
        "/daily\n"
        "/tops\n"
    )


async def balance(update: Update, context):

    uid = update.effective_user.id
    init_user(uid)

    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]

    await update.message.reply_text(f"💰 Coins: {coins}")


async def daily(update: Update, context):

    uid = update.effective_user.id
    init_user(uid)

    now = int(time.time())

    c.execute("SELECT last_daily FROM users WHERE id=?", (uid,))
    last = c.fetchone()[0]

    if now - last < 86400:
        await update.message.reply_text("⏱ Already claimed today.")
        return

    c.execute("""
    UPDATE users
    SET coins=coins+?, last_daily=?
    WHERE id=?
    """, (DAILY_REWARD, now, uid))

    conn.commit()

    await update.message.reply_text(f"✅ +{DAILY_REWARD} coins")


# ================= UPLOAD =================

async def upload_cmd(update: Update, context):

    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("Admin only.")
        return

    # Get photo
    if update.message.photo:
        msg = update.message

    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        msg = update.message.reply_to_message

    else:
        await update.message.reply_text("Send /upload with photo.")
        return


    text = " ".join(context.args)

    if not text:

        await update.message.reply_text(
            "/upload Name|Rarity|Faction|Power|Price"
        )
        return


    parts = [p.strip() for p in text.split("|")]

    if len(parts) != 5:
        await update.message.reply_text("Wrong format.")
        return


    try:

        name, rarity, faction, p, pr = parts
        power = int(p)
        price = int(pr)

    except:
        await update.message.reply_text("Power/Price must be number.")
        return


    file_id = msg.photo[-1].file_id


    try:

        c.execute("""
        INSERT INTO characters
        (name,rarity,faction,power,price,file_id)
        VALUES (?,?,?,?,?,?)
        """, (name, rarity, faction, power, price, file_id))

        conn.commit()

        cid = c.lastrowid

    except Exception as e:

        await update.message.reply_text(f"DB Error: {e}")
        return


    await update.message.reply_text(
        f"✅ Uploaded\nID: {cid}\n{name}"
    )


# ================= SUMMON =================

def get_chars():

    c.execute("SELECT * FROM characters")
    return c.fetchall()


def choose(times):

    chars = get_chars()

    if not chars:
        return []

    result = []

    for _ in range(times):

        r = roll_rarity()

        pool = [x for x in chars if x[2] == r]

        if not pool:
            pool = chars

        result.append(random.choice(pool))

    return result


async def summon(update: Update, context):

    uid = update.effective_user.id
    init_user(uid)

    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]

    if coins < SUMMON_COST:
        await update.message.reply_text("No coins.")
        return


    c.execute("""
    UPDATE users SET coins=coins-?
    WHERE id=?
    """, (SUMMON_COST, uid))

    conn.commit()

    res = choose(1)

    if not res:
        await update.message.reply_text("No characters.")
        return


    ch = res[0]

    add_inventory(uid, ch[0])

    await update.message.reply_photo(
        ch[6],
        caption=format_char(ch)
    )


async def summon10(update: Update, context):

    uid = update.effective_user.id
    init_user(uid)

    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]

    if coins < TEN_SUMMON_COST:
        await update.message.reply_text("No coins.")
        return


    c.execute("""
    UPDATE users SET coins=coins-?
    WHERE id=?
    """, (TEN_SUMMON_COST, uid))

    conn.commit()

    res = choose(10)

    if not res:
        return


    text = "🎰 10x Result\n\n"

    for ch in res:

        add_inventory(uid, ch[0])
        text += f"{ch[1]} ({ch[2]})\n"


    await update.message.reply_text(text)


# ================= INVENTORY =================

def get_inventory(uid):

    c.execute("""
    SELECT ch.id,ch.name,ch.rarity,inv.count
    FROM inventory inv
    JOIN characters ch
    ON inv.char_id=ch.id
    WHERE inv.user_id=?
    """, (uid,))

    return c.fetchall()


async def inventory_cmd(update: Update, context):

    uid = update.effective_user.id
    init_user(uid)

    items = get_inventory(uid)

    if not items:
        await update.message.reply_text("Empty.")
        return


    text = "📦 Inventory\n\n"

    for i, r in enumerate(items, 1):

        text += f"{i}. {r[1]} ({r[2]}) x{r[3]}\n"


    await update.message.reply_text(text)


# ================= TOP =================

async def tops(update: Update, context):

    c.execute("""
    SELECT id,coins
    FROM users
    ORDER BY coins DESC
    LIMIT 10
    """)

    rows = c.fetchall()

    msg = "🏆 Coins Ranking\n\n"

    for i, r in enumerate(rows, 1):
        msg += f"{i}. {r[0]} — {r[1]}\n"


    await update.message.reply_text(msg)


# ================= ADMIN =================

async def addadmin(update: Update, context):

    uid = update.effective_user.id

    if not is_owner(uid):
        return


    if not update.message.reply_to_message:
        return


    target = update.message.reply_to_message.from_user.id

    c.execute("""
    INSERT OR IGNORE INTO admins VALUES (?)
    """, (target,))

    conn.commit()

    await update.message.reply_text("Admin added.")


# ================= MAIN =================

def main():

    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing")


    app = ApplicationBuilder().token(BOT_TOKEN).build()


    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("daily", daily))

    app.add_handler(CommandHandler("upload", upload_cmd))

    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("summon10", summon10))

    app.add_handler(CommandHandler("inventory", inventory_cmd))
    app.add_handler(CommandHandler("tops", tops))

    app.add_handler(CommandHandler("addadmin", addadmin))


    logger.info("Bot running...")
    app.run_polling()



if __name__ == "__main__":
    main()
