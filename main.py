# main.py — Tensura World (Final Full Fixed Version)
import os
import random
import sqlite3
import logging
import time
import json
from typing import List, Tuple

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
from flask import Flask
from threading import Thread

app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive!"

def run_web():
    app_web.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# =========== CONFIG ===========
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 1812962224  # <-- replace with your Telegram numeric ID

DATA_DIR = "data"
DB_FILE = os.path.join(DATA_DIR, "gacha.db")

START_COINS = 100
SUMMON_COST = 20
TEN_SUMMON_COST = 180
DAILY_REWARD = 50
INV_PAGE_SIZE = 8

RARITY_RATE = {"Common": 60, "Rare": 25, "Epic": 10, "Legendary": 5}

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# =========== LOGGER ===========
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# =========== DB ===========
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()

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
    name TEXT UNIQUE,
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

# =========== HELPERS ===========
def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def is_admin(uid: int) -> bool:
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    return is_owner(uid) or (c.fetchone() is not None)

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

def add_to_inventory(uid: int, cid: int, amount: int = 1):
    c.execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?", (uid, cid))
    r = c.fetchone()
    if r:
        c.execute("UPDATE inventory SET count = count + ? WHERE user_id=? AND char_id=?", (amount, uid, cid))
    else:
        c.execute("INSERT INTO inventory (user_id, char_id, count) VALUES (?,?,?)", (uid, cid, amount))
    conn.commit()

def format_char_row_plain(row: Tuple) -> str:
    # row: (id, name, rarity, faction, power, price, file_id)
    return (
        f"🆔 ID: {row[0]}\n"
        f"✨ Name: {row[1]}\n"
        f"⭐ Rarity: {row[2]}\n"
        f"🏹 Faction: {row[3]}\n"
        f"💪 Power: {row[4]}\n"
        f"💰 Price: {row[5]}"
    )

def migrate_characters_from_json():
    fn = "characters.json"
    if not os.path.exists(fn):
        return
    try:
        with open(fn, "r", encoding="utf-8") as f:
            chars = json.load(f)
    except Exception as e:
        logger.warning("Failed to load characters.json: %s", e)
        return
    for ch in chars:
        try:
            c.execute("INSERT OR IGNORE INTO characters (id, name, rarity, faction, power, price, file_id) VALUES (?,?,?,?,?,?,?)",
                      (ch.get("id"), ch.get("name"), ch.get("rarity"), ch.get("faction"),
                       int(ch.get("power", 0)), int(ch.get("price", 0)), ch.get("file_id")))
        except Exception as e:
            logger.warning("Skipping char during migration: %s", e)
    conn.commit()
    logger.info("Migration from characters.json done (if file present).")

# =========== COMMANDS ===========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    await update.message.reply_text(
        "🎮 Tensura World (Gacha Bot)\n\n"
        "Commands:\n"
        "/summon - summon 1\n"
        "/summon10 - summon 10 (discount)\n"
        "/store - open store\n"
        "/inventory - view your inventory\n"
        "/balance - coins\n"
        "/daily - claim daily\n"
        "/tops - rankings (coins & characters)\n\n"
        "Admin: send photo with caption OR send photo + /upload command."
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
        remaining = 86400 - (now - last)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await update.message.reply_text(f"⏱ Already claimed. Next in {hours}h {minutes}m")
        return
    c.execute("UPDATE users SET coins = coins + ?, last_daily = ? WHERE id = ?", (DAILY_REWARD, now, uid))
    conn.commit()
    await update.message.reply_text(f"✅ Daily claimed: +{DAILY_REWARD} coins")

# ================= ADMIN UPLOAD SYSTEM =================
async def upload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⚠ Admin only.")
        return
    # get attached photo or reply
    photo_msg = None
    if update.message.photo:
        photo_msg = update.message
    elif update.message.reply_to_message and update.message.reply_to_message.photo:
        photo_msg = update.message.reply_to_message
    else:
        await update.message.reply_text("📷 Send a photo with this command or reply to a photo.")
        return

    args_text = " ".join(context.args).strip()
    if not args_text:
        # fallback to caption parsing
        caption = update.message.caption or ""
        if caption:
            data = {}
            for line in caption.splitlines():
                if ":" not in line: continue
                k, v = line.split(":", 1)
                data[k.strip().lower()] = v.strip()
            required = ["name","rarity","faction","power","price"]
            if not all(k in data for k in required):
                await update.message.reply_text("Caption must have: Name, Rarity, Faction, Power, Price")
                return
            try:
                power = int(data["power"]); price=int(data["price"])
            except:
                await update.message.reply_text("Power and Price must be integers.")
                return
            name = data["name"]
            c.execute("SELECT id FROM characters WHERE LOWER(name)=?", (name.lower(),))
            if c.fetchone():
                await update.message.reply_text("Character name already exists.")
                return
            file_id = photo_msg.photo[-1].file_id
            c.execute("INSERT INTO characters (name, rarity, faction, power, price, file_id) VALUES (?,?,?,?,?,?)",
                      (name, data["rarity"], data["faction"], power, price, file_id))
            conn.commit()
            await update.message.reply_text(f"✅ Character saved: {name}")
            return

        await update.message.reply_text("Usage: /upload Name|Rarity|Faction|Power|Price")
        return

    # pipe format parsing
    parts = [p.strip() for p in args_text.split("|")]
    if len(parts) != 5:
        await update.message.reply_text("Usage: /upload Name|Rarity|Faction|Power|Price")
        return
    try:
        name, rarity, faction, power_str, price_str = parts
        power = int(power_str); price = int(price_str)
    except:
        await update.message.reply_text("Power and Price must be integers.")
        return
    c.execute("SELECT id FROM characters WHERE LOWER(name)=?", (name.lower(),))
    if c.fetchone():
        await update.message.reply_text("Character name already exists.")
        return
    file_id = photo_msg.photo[-1].file_id
    c.execute("INSERT INTO characters (name, rarity, faction, power, price, file_id) VALUES (?,?,?,?,?,?)",
              (name, rarity, faction, power, price, file_id))
    conn.commit()
    await update.message.reply_text(f"✅ Character saved: {name}")

# ================= SUMMON =================
def choose_chars(times: int) -> List[Tuple]:
    c.execute("SELECT * FROM characters")
    chars = c.fetchall()
    if not chars: return []
    results = []
    for _ in range(times):
        rarity = roll_rarity()
        pool = [r for r in chars if r[2]==rarity] or chars
        results.append(random.choice(pool))
    return results

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]
    if coins < SUMMON_COST:
        await update.message.reply_text("❌ Not enough coins.")
        return
    c.execute("UPDATE users SET coins = coins - ? WHERE id=?", (SUMMON_COST, uid))
    conn.commit()
    results = choose_chars(1)
    if not results:
        await update.message.reply_text("⚠ No characters configured.")
        return
    ch = results[0]
    add_to_inventory(uid, ch[0])
    add_exp(uid, 10)
    await update.message.reply_photo(ch[6], caption=format_char_row_plain(ch))

async def summon10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]
    if coins < TEN_SUMMON_COST:
        await update.message.reply_text("❌ Not enough coins.")
        return
    c.execute("UPDATE users SET coins = coins - ? WHERE id=?", (TEN_SUMMON_COST, uid))
    conn.commit()
    results = choose_chars(10)
    if not results:
        await update.message.reply_text("⚠ No characters configured.")
        return
    tally = {}
    for ch in results:
        add_to_inventory(uid, ch[0])
        add_exp(uid, 10)
        key = (ch[0], ch[1], ch[2])
        tally[key] = tally.get(key,0)+1
    text="🎰 10x Summon Results\n\n"
    for (cid,name,rarity),cnt in tally.items():
        text+=f"{name} ({rarity}) x{cnt}\n"
    await update.message.reply_text(text)

# ================= STORE =================
async def send_store(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    c.execute("SELECT * FROM characters")
    chars = c.fetchall()
    if not chars:
        await context.bot.send_message(chat_id, "⚠ Store empty.")
        return
    char = random.choice(chars)
    keyboard = [
        [InlineKeyboardButton("Buy", callback_data=f"buy_{char[0]}"),
         InlineKeyboardButton("Next", callback_data="next_store")]
    ]
    await context.bot.send_photo(chat_id, char[6], caption=format_char_row_plain(char),
                                 reply_markup=InlineKeyboardMarkup(keyboard))

async def store_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_store(update.effective_chat.id, context)

async def store_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    init_user(uid)
    if q.data=="next_store":
        await send_store(q.message.chat.id, context)
        return
    if q.data.startswith("buy_"):
        cid=int(q.data.split("_")[1])
        c.execute("SELECT * FROM characters WHERE id=?", (cid,))
        char=c.fetchone()
        if not char:
            await q.edit_message_caption("❌ Not found.")
            return
        c.execute("SELECT coins FROM users WHERE id=?", (uid,))
        coins=c.fetchone()[0]
        if coins<char[5]:
            await q.edit_message_caption("❌ Not enough coins.")
            return
        c.execute("UPDATE users SET coins = coins - ? WHERE id=?", (char[5], uid))
        add_to_inventory(uid, cid)
        conn.commit()
        await q.edit_message_caption(f"✅ Bought {char[1]}")

# ================= INVENTORY =================
def build_inventory_pages(user_id:int):
    c.execute("""
    SELECT characters.id, characters.name, characters.rarity, inventory.count
    FROM inventory
    JOIN characters ON inventory.char_id = characters.id
    WHERE inventory.user_id=?
    ORDER BY characters.id ASC
    """,(user_id,))
    items=c.fetchall()
    pages=[items[i:i+INV_PAGE_SIZE] for i in range(0,len(items),INV_PAGE_SIZE)]
    return pages

async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_user(uid)
    pages = build_inventory_pages(uid)
    if not pages:
        await update.message.reply_text("📦 Inventory empty.")
        return
    await send_inventory_page(update.effective_chat.id, context, pages, 0)

async def send_inventory_page(chat_id:int, context: ContextTypes.DEFAULT_TYPE, pages:list, idx:int):
    page = pages[idx]
    text = f"📦 Inventory — Page {idx+1}/{len(pages)}\n\n"
    for i,row in enumerate(page,1):
        cid,name,rarity,count=row
        text+=f"{i}. {name} ({rarity}) x{count} — ID:{cid}\n"
    buttons=[]
    if idx>0:
        buttons.append(InlineKeyboardButton("⬅ Prev", callback_data=f"inv_{idx-1}"))
    if idx<len(pages)-1:
        buttons.append(InlineKeyboardButton("Next ➡", callback_data=f"inv_{idx+1}"))
    reply_markup=InlineKeyboardMarkup([buttons]) if buttons else None
    await context.bot.send_message(chat_id, text, reply_markup=reply_markup)

async def inv_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    pages = build_inventory_pages(uid)
    if not pages:
        await q.message.reply_text("📦 Inventory empty.")
        return
    try:
        idx=int(q.data.split("_")[1])
    except:
        idx=0
    await send_inventory_page(q.message.chat.id, context, pages, idx)

# ================= TOPS =================
async def tops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard=[
        [InlineKeyboardButton("Coins Ranking", callback_data="rank_coins"),
         InlineKeyboardButton("Character Ranking", callback_data="rank_chars")]
    ]
    await update.message.reply_text("🏆 Top Rankings", reply_markup=InlineKeyboardMarkup(keyboard))

async def tops_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    keyboard=[
        [InlineKeyboardButton("Coins Ranking", callback_data="rank_coins"),
         InlineKeyboardButton("Character Ranking", callback_data="rank_chars")]
    ]
    if q.data=="rank_coins":
                c.execute("SELECT id, coins FROM users ORDER BY coins DESC LIMIT 10")
        rows = c.fetchall()
        if not rows:
            msg = "No users found."
        else:
            msg = "💰 Coins Ranking\n\n"
            for i, (user_id, coins) in enumerate(rows, 1):
                try:
                    user = await context.bot.get_chat(int(user_id))
                    name = getattr(user, "first_name", None) or getattr(user, "username", None) or str(user_id)
                except Exception:
                    name = str(user_id)
                msg += f"{i}. {name} — {coins} coins\n"
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if q.data=="rank_chars":
        c.execute("""
        SELECT u.id, COALESCE(SUM(ch.power * inv.count),0) AS total_power
        FROM users u
        LEFT JOIN inventory inv ON u.id=inv.user_id
        LEFT JOIN characters ch ON inv.char_id=ch.id
        GROUP BY u.id
        ORDER BY total_power DESC
        LIMIT 10
        """)
        rows=c.fetchall()
        if not rows:
            msg="No characters yet."
        else:
            msg="💪 Character Power Ranking\n\n"
            for i,(user_id,total_power) in enumerate(rows,1):
                try:
                    user = await context.bot.get_chat(int(user_id))
                    name = getattr(user,"first_name",None) or getattr(user,"username",None) or str(user_id)
                except Exception:
                    name=str(user_id)
                msg+=f"{i}. {name} — {total_power} power\n"
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

# ================= ADMIN MANAGEMENT =================
async def addcoins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⚠ Admin only.")
        return
    if not update.message.reply_to_message or not context.args:
        await update.message.reply_text("Reply to a user and use: /addcoins <amount>")
        return
    try:
        amount=int(context.args[0])
    except:
        await update.message.reply_text("Amount must be integer.")
        return
    target=update.message.reply_to_message.from_user.id
    init_user(target)
    c.execute("UPDATE users SET coins = coins + ? WHERE id=?",(amount,target))
    conn.commit()
    await update.message.reply_text(f"✅ Added {amount} coins to user.")

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user and run /addadmin")
        return
    target=update.message.reply_to_message.from_user.id
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)",(target,))
    conn.commit()
    await update.message.reply_text("✅ Admin added.")

async def deladmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user and run /deladmin")
        return
    target=update.message.reply_to_message.from_user.id
    c.execute("DELETE FROM admins WHERE user_id=?",(target,))
    conn.commit()
    await update.message.reply_text("✅ Admin removed.")

async def listchars_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("Admin only.")
        return
    c.execute("SELECT id,name,rarity,price FROM characters ORDER BY id")
    rows=c.fetchall()
    if not rows:
        await update.message.reply_text("No characters.")
        return
    text="📜 Characters List\n\n"
    for r in rows:
        text+=f"ID:{r[0]} | {r[1]} | {r[2]} | {r[3]}\n"
    await update.message.reply_text(text)

async def delchar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /delchar <id>")
        return
    try:
        cid=int(context.args[0])
    except:
        await update.message.reply_text("Invalid ID.")
        return
    c.execute("SELECT name FROM characters WHERE id=?",(cid,))
    row=c.fetchone()
    if not row:
        await update.message.reply_text("Character not found.")
        return
    name=row[0]
    c.execute("DELETE FROM characters WHERE id=?",(cid,))
    c.execute("DELETE FROM inventory WHERE char_id=?",(cid,))
    conn.commit()
    await update.message.reply_text(f"✅ Deleted {name} (ID {cid})")

async def editchar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("Admin only.")
        return
    if len(context.args)<3:
        await update.message.reply_text("Usage: /editchar <id> <field> <new_value>")
        return
    try:
        cid=int(context.args[0])
    except:
        await update.message.reply_text("Invalid ID")
        return
    field=context.args[1].lower()
    if field not in ("name","rarity","faction","power","price"):
        await update.message.reply_text("Invalid field.")
        return
    value=" ".join(context.args[2:])
    if field in ("power","price"):
        try:
            value=int(value)
        except:
            await update.message.reply_text("power/price must be integers.")
            return
    c.execute("SELECT 1 FROM characters WHERE id=?",(cid,))
    if not c.fetchone():
        await update.message.reply_text("Character not found.")
        return
    c.execute(f"UPDATE characters SET {field}=? WHERE id=?",(value,cid))
    conn.commit()
    await update.message.reply_text("✅ Character updated.")

# ================= START BOT =================
def main():
    keep_alive()
    migrate_characters_from_json()
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing. export BOT_TOKEN in environment.")

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

    # Photo upload (admin)
    app.add_handler(CommandHandler("upload", upload_cmd))

    # Tops
    app.add_handler(CommandHandler("tops", tops))
    app.add_handler(CallbackQueryHandler(tops_btn, pattern=r'^(rank_coins|rank_chars)$'))

    # Admin
    app.add_handler(CommandHandler("addcoins", addcoins_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("deladmin", deladmin_cmd))
    app.add_handler(CommandHandler("listchars", listchars_cmd))
    app.add_handler(CommandHandler("delchar", delchar_cmd))
    app.add_handler(CommandHandler("editchar", editchar_cmd))

    logger.info("Bot starting...")
    app.run_polling()

if __name__=="__main__":
    main()

