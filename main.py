# main.py
import os
import random
import sqlite3
import logging
import time
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 1812962224  # <-- change to your Telegram ID
DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/gacha.db"

START_COINS = 100
SUMMON_COST = 20
TEN_SUMMON_COST = 180
DAILY_REWARD = 50
INV_PAGE = 10

RARITY_RATE = {"Common": 60, "Rare": 25, "Epic": 10, "Legendary": 5}

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ================= LOGGER =================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ================= DATABASE =================

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()

# tables
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    coins INTEGER DEFAULT 0,
    lvl INTEGER DEFAULT 1,
    exp INTEGER DEFAULT 0,
    last_daily INTEGER DEFAULT 0
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    rarity TEXT,
    faction TEXT,
    power INTEGER,
    price INTEGER,
    file_id TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS inventory (
    user_id INTEGER,
    char_id INTEGER,
    count INTEGER,
    PRIMARY KEY (user_id, char_id)
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
""")

conn.commit()

# ================= HELPERS =================


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def is_admin(uid: int) -> bool:
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    row = c.fetchone()
    return is_owner(uid) or (row is not None)


def init_user(uid: int):
    c.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    if not c.fetchone():
        c.execute("INSERT INTO users (id, coins, lvl, exp, last_daily) VALUES (?,?,?,?,?)",
                  (uid, START_COINS, 1, 0, 0))
        conn.commit()


def roll_rarity() -> str:
    r = random.randint(1, 100)
    total = 0
    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k
    return "Common"


def add_exp(uid: int, amount: int):
    c.execute("SELECT lvl, exp FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    if not row:
        init_user(uid)
        c.execute("SELECT lvl, exp FROM users WHERE id=?", (uid,))
        row = c.fetchone()

    lvl, exp = row
    exp += amount
    while exp >= lvl * 100:
        exp -= lvl * 100
        lvl += 1

    c.execute("UPDATE users SET lvl=?, exp=? WHERE id=?", (lvl, exp, uid))
    conn.commit()


def add_to_inventory(uid: int, cid: int):
    # safe insert-or-update (works on all sqlite versions)
    c.execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?", (uid, cid))
    r = c.fetchone()
    if r:
        c.execute("UPDATE inventory SET count = count + 1 WHERE user_id=? AND char_id=?", (uid, cid))
    else:
        c.execute("INSERT INTO inventory (user_id, char_id, count) VALUES (?,?,1)", (uid, cid))
    conn.commit()


def format_char_for_caption(row):
    # row is a characters table row tuple: (id, name, rarity, faction, power, price, file_id)
    return f"🆔 {row[0]}\n🔥 {row[1]}\n⭐ {row[2]}\n🏰 {row[3]}\n⚔️ {row[4]}\n💰 {row[5]} coins"


# ================= MIGRATION =================

def migrate_characters():
    if os.path.exists("characters.json"):
        try:
            with open("characters.json", "r", encoding="utf-8") as f:
                chars = json.load(f)
        except Exception as e:
            logger.warning("Could not read characters.json: %s", e)
            return

        for ch in chars:
            try:
                # if id field provided, attempt to insert ignoring conflicts on id
                if "id" in ch:
                    c.execute(
                        "INSERT OR IGNORE INTO characters (id, name, rarity, faction, power, price, file_id) VALUES (?,?,?,?,?,?,?)",
                        (ch.get("id"), ch.get("name"), ch.get("rarity"), ch.get("faction"),
                         int(ch.get("power", 0)), int(ch.get("price", 0)), ch.get("file_id"))
                    )
                else:
                    c.execute(
                        "INSERT OR IGNORE INTO characters (name, rarity, faction, power, price, file_id) VALUES (?,?,?,?,?,?)",
                        (ch.get("name"), ch.get("rarity"), ch.get("faction"),
                         int(ch.get("power", 0)), int(ch.get("price", 0)), ch.get("file_id"))
                    )
            except Exception as e:
                logger.warning("Skipping character during migration: %s", e)
        conn.commit()
        logger.info("✅ character.json migrated to DB (if present)")


# ================= BASIC COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    await update.message.reply_text(
        "🎮 Tensura World (Gacha Bot)\n\n"
        "Commands:\n"
        "/summon - summon 1 (cost)\n"
        "/summon10 - summon 10 (discount)\n"
        "/store - browse store\n"
        "/inventory - view your inventory\n"
        "/balance - coins\n"
        "/daily - claim daily\n"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]
    await update.message.reply_text(f"💰 Coins: {coins}")


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    now = int(time.time())
    c.execute("SELECT last_daily FROM users WHERE id=?", (uid,))
    last = c.fetchone()[0]
    if now - last < 86400:
        await update.message.reply_text("⏳ Already claimed today.")
        return
    c.execute("UPDATE users SET coins = coins + ?, last_daily = ? WHERE id = ?", (DAILY_REWARD, now, uid))
    conn.commit()
    await update.message.reply_text(f"✅ Daily claimed: +{DAILY_REWARD} coins")


# ================= PHOTO UPLOAD (ADMIN) =================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id
    if not is_admin(uid):
        # silent return for non-admins (or you can send a message)
        return

    caption = update.message.caption
    if not caption:
        await update.message.reply_text("❌ Please include caption with character data.")
        return

    data = {}
    for line in caption.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip().lower()] = v.strip()

    required = ["name", "rarity", "faction", "power", "price"]
    if not all(k in data for k in required):
        await update.message.reply_text("❌ Caption format wrong. Required keys: name, rarity, faction, power, price")
        return

    try:
        power = int(data["power"])
        price = int(data["price"])
    except:
        await update.message.reply_text("❌ power and price must be numbers.")
        return

    name = data["name"].strip()

    # duplicate name protection
    c.execute("SELECT id FROM characters WHERE LOWER(name)=?", (name.lower(),))
    if c.fetchone():
        await update.message.reply_text("❌ Character with that name already exists.")
        return

    file_id = update.message.photo[-1].file_id

    c.execute("INSERT INTO characters (name, rarity, faction, power, price, file_id) VALUES (?,?,?,?,?,?)",
              (name, data["rarity"], data["faction"], power, price, file_id))
    conn.commit()
    await update.message.reply_text("✅ Character saved to DB.")


# ================= SUMMON =================

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_summon(update, 1)


async def summon10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_summon(update, 10)


async def do_summon(update: Update, times: int):
    uid = update.effective_user.id
    init_user(uid)

    cost = SUMMON_COST if times == 1 else TEN_SUMMON_COST

    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins_row = c.fetchone()
    if not coins_row:
        await update.message.reply_text("❌ User row missing.")
        return
    coins = coins_row[0]

    if coins < cost:
        await update.message.reply_text("❌ Not enough coins.")
        return

    c.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (cost, uid))

    c.execute("SELECT * FROM characters")
    chars = c.fetchall()
    if not chars:
        await update.message.reply_text("❌ No characters in DB.")
        return

    results = []
    for _ in range(times):
        rarity = roll_rarity()
        pool = [r for r in chars if r[2] == rarity]
        if not pool:
            pool = chars
        char = random.choice(pool)
        add_to_inventory(uid, char[0])
        add_exp(uid, 10)
        results.append(char)

    conn.commit()

    if times == 1:
        char = results[0]
        await update.message.reply_photo(char[6], caption=format_char_for_caption(char))
    else:
        text = "🎉 10x Summon Results\n\n"
        for ch in results:
            text += f"⭐ {ch[1]} ({ch[2]})\n"
        await update.message.reply_text(text)


# ================= STORE =================

async def send_store(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT * FROM characters")
    chars = c.fetchall()
    if not chars:
        await context.bot.send_message(chat_id, "❌ Store empty.")
        return

    char = random.choice(chars)
    keyboard = [
        [
            InlineKeyboardButton("🛒 Buy", callback_data=f"buy_{char[0]}"),
            InlineKeyboardButton("➡ Next", callback_data="next_store")
        ]
    ]

    await context.bot.send_photo(
        chat_id,
        char[6],
        caption=format_char_for_caption(char),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # called from command
    chat_id = update.effective_chat.id
    await send_store(chat_id, context)


async def store_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    init_user(uid)

    if q.data == "next_store":
        # send another store item to same chat
        await send_store(q.message.chat.id, context)
        return

    if q.data.startswith("buy_"):
        try:
            cid = int(q.data.split("_", 1)[1])
        except:
            await q.edit_message_caption("❌ Invalid ID.")
            return

        c.execute("SELECT * FROM characters WHERE id=?", (cid,))
        char = c.fetchone()
        if not char:
            await q.edit_message_caption("❌ Not found.")
            return

        c.execute("SELECT coins FROM users WHERE id=?", (uid,))
        coins_row = c.fetchone()
        if not coins_row:
            await q.edit_message_caption("❌ User row missing.")
            return
        coins = coins_row[0]

        if coins < char[5]:
            await q.edit_message_caption("❌ Not enough coins.")
            return

        c.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (char[5], uid))
        add_to_inventory(uid, cid)
        conn.commit()
        await q.edit_message_caption(f"✅ Bought {char[1]}")


# ================= INVENTORY (PAGINATION) =================

async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)

    c.execute("""
    SELECT characters.id, characters.name, characters.rarity, inventory.count
    FROM inventory
    JOIN characters ON inventory.char_id = characters.id
    WHERE inventory.user_id = ?
    ORDER BY characters.id ASC
    """, (uid,))
    items = c.fetchall()
    if not items:
        await update.message.reply_text("❌ Inventory empty.")
        return

    # build pages (list of lists)
    pages = [items[i:i + INV_PAGE] for i in range(0, len(items), INV_PAGE)]
    await show_inv_page(update.effective_chat.id, context, pages, 0)


async def show_inv_page(chat_id: int, context: ContextTypes.DEFAULT_TYPE, pages, page_index: int):
    page = pages[page_index]
    text = f"🎴 Inventory (Page {page_index+1}/{len(pages)})\n\n"
    for i, row in enumerate(page, start=1):
        cid, name, rarity, count = row
        text += f"{i}. {name} ({rarity}) x{count}  — ID:{cid}\n"

    keyboard = []
    buttons = []
    if page_index > 0:
        buttons.append(InlineKeyboardButton("⬅ Prev", callback_data=f"inv_{page_index-1}"))
    if page_index < len(pages) - 1:
        buttons.append(InlineKeyboardButton("Next ➡", callback_data=f"inv_{page_index+1}"))
    if buttons:
        keyboard.append(buttons)

    await context.bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)


async def inv_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    # rebuild pages from DB to ensure fresh data
    c.execute("""
    SELECT characters.id, characters.name, characters.rarity, inventory.count
    FROM inventory
    JOIN characters ON inventory.char_id = characters.id
    WHERE inventory.user_id = ?
    ORDER BY characters.id ASC
    """, (uid,))
    items = c.fetchall()
    if not items:
        await q.message.reply_text("❌ Inventory empty.")
        return
    pages = [items[i:i + INV_PAGE] for i in range(0, len(items), INV_PAGE)]
    try:
        page_index = int(q.data.split("_", 1)[1])
    except:
        page_index = 0
    # edit message by sending a new message (editing previous callback message text is messy)
    await show_inv_page(q.message.chat.id, context, pages, page_index)


# ================= ADMIN COMMANDS =================

async def addcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin only.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply to a user to give coins.")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: /addcoins <amount> (reply to user)")
        return

    try:
        amount = int(context.args[0])
    except:
        await update.message.reply_text("❌ Amount must be a number.")
        return

    target = update.message.reply_to_message.from_user.id
    init_user(target)
    c.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (amount, target))
    conn.commit()
    await update.message.reply_text(f"✅ Added {amount} coins to user.")


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("❌ Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply to a user to promote.")
        return
    target = update.message.reply_to_message.from_user.id
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (target,))
    conn.commit()
    await update.message.reply_text("✅ Admin added.")


async def deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("❌ Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply to a user to demote.")
        return
    target = update.message.reply_to_message.from_user.id
    c.execute("DELETE FROM admins WHERE user_id = ?", (target,))
    conn.commit()
    await update.message.reply_text("✅ Admin removed.")


async def listchars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin only.")
        return
    c.execute("SELECT id, name, rarity, price FROM characters ORDER BY id")
    rows = c.fetchall()
    if not rows:
        await update.message.reply_text("No characters found.")
        return
    text = "📜 Characters\n\n"
    for r in rows:
        text += f"ID:{r[0]} | {r[1]} | {r[2]} | 💰{r[3]}\n"
    await update.message.reply_text(text)


async def delchar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /delchar <id>")
        return
    try:
        cid = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid ID")
        return
    c.execute("SELECT name FROM characters WHERE id=?", (cid,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text("❌ Character not found.")
        return
    name = row[0]
    c.execute("DELETE FROM characters WHERE id=?", (cid,))
    c.execute("DELETE FROM inventory WHERE char_id=?", (cid,))  # cleanup
    conn.commit()
    await update.message.reply_text(f"✅ Deleted character {name} (ID {cid})")


async def editchar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin only.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /editchar <id> <field> <new_value>\nFields: name, rarity, faction, power, price")
        return
    try:
        cid = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid ID")
        return
    field = context.args[1].lower()
    if field not in ("name", "rarity", "faction", "power", "price"):
        await update.message.reply_text("❌ Invalid field.")
        return
    value = " ".join(context.args[2:])
    if field in ("power", "price"):
        try:
            value = int(value)
        except:
            await update.message.reply_text("❌ power/price must be number.")
            return
    c.execute("SELECT 1 FROM characters WHERE id=?", (cid,))
    if not c.fetchone():
        await update.message.reply_text("❌ Character not found.")
        return
    c.execute(f"UPDATE characters SET {field}=? WHERE id=?", (value, cid))
    conn.commit()
    await update.message.reply_text("✅ Character updated.")


# ================= MAIN =================

def main():
    migrate_characters()

    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing. export BOT_TOKEN before running.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # basic commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("daily", daily))

    # summoning
    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("summon10", summon10))

    # store
    app.add_handler(CommandHandler("store", store))
    app.add_handler(CallbackQueryHandler(store_btn, pattern=r'^(buy_|next_store)'))

    # inventory
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CallbackQueryHandler(inv_btn, pattern=r'^inv_\d+$'))

    # admin photo upload (only accept photos with captions)
    app.add_handler(MessageHandler(filters.PHOTO & filters.Caption(True), photo_handler))

    # admin commands
    app.add_handler(CommandHandler("addcoins", addcoins))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("admins", listchars))  # re-purpose /admins -> list characters for admins
    app.add_handler(CommandHandler("listchars", listchars))
    app.add_handler(CommandHandler("delchar", delchar))
    app.add_handler(CommandHandler("editchar", editchar))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
