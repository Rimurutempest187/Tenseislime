import os
import random
import sqlite3
import logging
import time
from typing import List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from dotenv import load_dotenv
load_dotenv()

# ================= KEEP ALIVE =================
from flask import Flask
from threading import Thread

app_web = Flask("")

@app_web.route("/")
def home():
    return "Bot Alive!"

def run_web():
    app_web.run("0.0.0.0", 8080)

def keep_alive():
    Thread(target=run_web).start()


# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 1812962224   # <<< Change to your Telegram ID

DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/bot.db"

START_COINS = 200
DAILY_REWARD = 100

SUMMON_COST = 25
TEN_SUMMON_COST = 220

INV_PAGE = 8

RARITY_RATE = {
    "Common": 55,
    "Rare": 25,
    "Epic": 15,
    "Legendary": 5
}


# ================= LOG =================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger()


# ================= DB =================

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

conn = sqlite3.connect(
    DB_FILE,
    check_same_thread=False,
    timeout=30,
    isolation_level=None
)
c = conn.cursor()

# Users
c.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    coins INTEGER,
    level INTEGER,
    exp INTEGER,
    last_daily INTEGER
)
""")

# Characters
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
    if is_owner(uid):
        return True
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    return bool(c.fetchone())

def init_user(uid):
    c.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    if not c.fetchone():
        c.execute("INSERT INTO users VALUES(?,?,?,?,?)", (uid, START_COINS, 1, 0, 0))
        conn.commit()

def roll_rarity():
    r = random.randint(1, 100)
    total = 0
    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k
    return "Common"

def add_inventory(uid, cid, amt=1):
    c.execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?", (uid, cid))
    r = c.fetchone()
    if r:
        c.execute("UPDATE inventory SET count = count + ? WHERE user_id=? AND char_id=?", (amt, uid, cid))
    else:
        c.execute("INSERT INTO inventory VALUES(?,?,?)", (uid, cid, amt))
    conn.commit()

def add_exp(uid, amt):
    c.execute("SELECT level,exp FROM users WHERE id=?", (uid,))
    lvl, exp = c.fetchone()
    exp += amt
    while exp >= lvl * 100:
        exp -= lvl * 100
        lvl += 1
    c.execute("UPDATE users SET level=?, exp=? WHERE id=?", (lvl, exp, uid))
    conn.commit()

def format_char(row):
    return (
        f"🆔 ID: {row[0]}\n"
        f"✨ Name: {row[1]}\n"
        f"⭐ Rarity: {row[2]}\n"
        f"🏹 Faction: {row[3]}\n"
        f"💪 Power: {row[4]}\n"
        f"💰 Price: {row[5]}"
    )
# ================= BASIC COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    await update.message.reply_text(
        "🎮 Tensura World Gacha\n\n"
        "/summon\n"
        "/summon10\n"
        "/store\n"
        "/inventory\n"
        "/daily\n"
        "/balance\n"
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
        left = 86400 - (now - last)
        h = left // 3600
        await update.message.reply_text(f"⏱ Wait {h}h")
        return
    c.execute("UPDATE users SET coins = coins + ?, last_daily = ? WHERE id=?", (DAILY_REWARD, now, uid))
    conn.commit()
    await update.message.reply_text(f"✅ +{DAILY_REWARD} coins")


# ================= SUMMON =================

def choose_chars(n):
    c.execute("SELECT * FROM characters")
    rows = c.fetchall()
    if not rows:
        return []
    res = []
    for _ in range(n):
        r = roll_rarity()
        pool = [x for x in rows if x[2] == r] or rows
        res.append(random.choice(pool))
    return res

async def summon(update: Update, context):
    uid = update.effective_user.id
    init_user(uid)
    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]
    if coins < SUMMON_COST:
        await update.message.reply_text("❌ Not enough coins")
        return
    c.execute("UPDATE users SET coins=coins-? WHERE id=?", (SUMMON_COST, uid))
    conn.commit()
    chars = choose_chars(1)
    if not chars:
        await update.message.reply_text("⚠ No chars available")
        return
    ch = chars[0]
    add_inventory(uid, ch[0])
    add_exp(uid, 10)
    await update.message.reply_photo(ch[6], caption=format_char(ch))

async def summon10(update: Update, context):
    uid = update.effective_user.id
    init_user(uid)
    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]
    if coins < TEN_SUMMON_COST:
        await update.message.reply_text("❌ Not enough coins")
        return
    c.execute("UPDATE users SET coins=coins-? WHERE id=?", (TEN_SUMMON_COST, uid))
    conn.commit()
    res = choose_chars(10)
    text = "🎰 10x Summon\n\n"
    count = {}
    for ch in res:
        add_inventory(uid, ch[0])
        add_exp(uid, 10)
        key = ch[1]
        count[key] = count.get(key, 0) + 1
    for k, v in count.items():
        text += f"{k} x{v}\n"
    await update.message.reply_text(text)


# ================= STORE =================

async def send_store(chat_id: int, context):
    c.execute("SELECT * FROM characters")
    chars = c.fetchall()
    if not chars:
        await context.bot.send_message(chat_id, "⚠ Store empty")
        return
    char = random.choice(chars)
    keyboard = [[
        InlineKeyboardButton("Buy", callback_data=f"buy_{char[0]}"),
        InlineKeyboardButton("Next", callback_data="next_store")
    ]]
    await context.bot.send_photo(chat_id, char[6], caption=format_char(char),
                                 reply_markup=InlineKeyboardMarkup(keyboard))

async def store_cmd(update: Update, context):
    await send_store(update.effective_chat.id, context)

async def store_btn(update: Update, context):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    init_user(uid)

    # NEXT
    if q.data == "next_store":
        await q.message.delete()
        await send_store(q.message.chat.id, context)
        return

    # BUY
    if q.data.startswith("buy_"):
        cid = int(q.data.split("_")[1])

        c.execute("SELECT * FROM characters WHERE id=?", (cid,))
        char = c.fetchone()

        if not char:
            await q.edit_message_caption("❌ Character not found")
            return

        c.execute("SELECT coins FROM users WHERE id=?", (uid,))
        coins = c.fetchone()[0]

        if coins < char[5]:
            await q.edit_message_caption("❌ Not enough coins")
            return

        c.execute("UPDATE users SET coins=coins-? WHERE id=?", (char[5], uid))
        add_inventory(uid, cid)
        conn.commit()

        await q.edit_message_caption(f"✅ Bought {char[1]}")
# ================= INVENTORY =================

def build_inventory_pages(uid: int):
    c.execute("""
    SELECT characters.id, characters.name, characters.rarity, inventory.count
    FROM inventory
    JOIN characters ON inventory.char_id=characters.id
    WHERE inventory.user_id=?
    ORDER BY characters.id
    """, (uid,))
    rows = c.fetchall()
    pages = [rows[i:i+INV_PAGE] for i in range(0, len(rows), INV_PAGE)]
    return pages

async def send_inventory_page(chat_id, context, pages, idx):
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

async def inventory_cmd(update: Update, context):
    uid = update.effective_user.id
    init_user(uid)
    pages = build_inventory_pages(uid)
    if not pages:
        await update.message.reply_text("📦 Inventory empty")
        return
    await send_inventory_page(update.effective_chat.id, context, pages, 0)

async def inv_btn(update: Update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    pages = build_inventory_pages(uid)
    if not pages:
        await q.message.reply_text("📦 Inventory empty")
        return
    try:
        idx = int(q.data.split("_")[1])
    except:
        idx = 0
    await q.message.delete()
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
            await update.message.reply_text("Usage: /upload Name|Rarity|Faction|Power|Price")
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
        except:
            await update.message.reply_text("Power / Price must be numbers")
            return

        name = data["name"]
        rarity = data["rarity"]
        faction = data["faction"]

        # Warning if name exists
        c.execute("SELECT id FROM characters WHERE name=?", (name,))
        if c.fetchone():
            await update.message.reply_text("⚠ Name already exists (Allowed)")

        file_id = photo_msg.photo[-1].file_id

        try:
            c.execute("""
                INSERT INTO characters
                (name, rarity, faction, power, price, file_id)
                VALUES (?,?,?,?,?,?)
            """, (name, rarity, faction, power, price, file_id))

            conn.commit()
            new_id = c.lastrowid

        except Exception as e:
            await update.message.reply_text(f"❌ DB Error: {e}")
            return

        await update.message.reply_text(
            f"✅ Uploaded!\nID: {new_id}\nName: {name}"
        )
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
    except:
        await update.message.reply_text("Power / Price must be numbers")
        return


    # Warning if name exists
    c.execute("SELECT id FROM characters WHERE name=?", (name,))
    if c.fetchone():
        await update.message.reply_text("⚠ Name already exists (Allowed)")


    file_id = photo_msg.photo[-1].file_id

    try:
        c.execute("""
            INSERT INTO characters
            (name, rarity, faction, power, price, file_id)
            VALUES (?,?,?,?,?,?)
        """, (name, rarity, faction, power, price, file_id))

        conn.commit()
        new_id = c.lastrowid

    except Exception as e:
        await update.message.reply_text(f"❌ DB Error: {e}")
        return


    await update.message.reply_text(
        f"✅ Uploaded!\nID: {new_id}\nName: {name}"
    )

# ================= TOPS LEADERBOARD ================
async def get_user_name(bot, user_id: int) -> str:
    try:
        user = await bot.get_chat(user_id)

        if user.username:
            return "@" + user.username

        if user.first_name:
            return user.first_name

        return str(user_id)

    except:
        return str(user_id)


async def tops_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    c.execute("""
    SELECT id, level, exp, coins
    FROM users
    ORDER BY level DESC, exp DESC, coins DESC
    LIMIT 10
    """)
    rows = c.fetchall()

    if not rows:
        await update.message.reply_text("⚠ User မရှိသေးပါ")
        return


    text = "🏆 <b>Top Players Ranking</b>\n\n"

    for idx, row in enumerate(rows, 1):

        uid, lvl, exp, coins = row

        # get name
        name = await get_user_name(context.bot, uid)

        text += (
            f"🥇 {idx}. {name}\n"
            f"   🎚 Level: {lvl}\n"
            f"   💰 Coins: {coins}\n"
            f"   📊 EXP: {exp}\n\n"
        )


    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )



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
    except:
        await update.message.reply_text("Invalid user_id or amount")
        return
    init_user(target_id)
    c.execute("UPDATE users SET coins=coins+? WHERE id=?", (amount, target_id))
    conn.commit()
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
    except:
        await update.message.reply_text("Invalid user_id")
        return
    try:
        c.execute("INSERT INTO admins(user_id) VALUES(?)", (target_id,))
        conn.commit()
    except:
        await update.message.reply_text("User is already admin")
        return
    await update.message.reply_text(f"✅ {target_id} added as admin")


# ================= MAIN =================

def main():
    keep_alive()
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Basic
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
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
    app.add_handler(CommandHandler("addcoins", addcoins_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))

    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
