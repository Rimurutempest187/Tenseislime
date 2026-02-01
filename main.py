import os, random, sqlite3, logging, time, json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 1812962224  # <-- Telegram ID

DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/gacha.db"

START_COINS = 100
SUMMON_COST = 20
TEN_SUMMON_COST = 180
DAILY_REWARD = 50
INV_PAGE = 10

RARITY_RATE = {
    "Common": 60,
    "Rare": 25,
    "Epic": 10,
    "Legendary": 5
}

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ================= LOGGER =================

logging.basicConfig(level=logging.INFO)

# ================= DATABASE =================

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS users
(id INTEGER PRIMARY KEY, coins INTEGER, lvl INTEGER, exp INTEGER, last_daily INTEGER)
""")

c.execute("""CREATE TABLE IF NOT EXISTS characters
(id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT, rarity TEXT, faction TEXT,
 power INTEGER, price INTEGER, file_id TEXT)
""")

c.execute("""CREATE TABLE IF NOT EXISTS inventory
(user_id INTEGER, char_id INTEGER, count INTEGER,
 FOREIGN KEY(user_id) REFERENCES users(id),
 FOREIGN KEY(char_id) REFERENCES characters(id))
""")

c.execute("""CREATE TABLE IF NOT EXISTS admins
(user_id INTEGER PRIMARY KEY)
""")

conn.commit()

# ================= HELPERS =================

def is_owner(uid):
    return uid == OWNER_ID

def is_admin(uid):
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    return is_owner(uid) or c.fetchone() is not None

def init_user(uid):
    c.execute("SELECT * FROM users WHERE id=?", (uid,))
    if not c.fetchone():
        c.execute(
            "INSERT INTO users VALUES (?,?,?,?,?)",
            (uid, START_COINS, 1, 0, 0)
        )
        conn.commit()

def roll_rarity():
    r = random.randint(1, 100)
    total = 0
    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k
    return "Common"

def add_exp(uid, amount):
    c.execute("SELECT lvl,exp FROM users WHERE id=?", (uid,))
    lvl, exp = c.fetchone()
    exp += amount
    while exp >= lvl * 100:
        exp -= lvl * 100
        lvl += 1
    c.execute(
        "UPDATE users SET lvl=?, exp=? WHERE id=?",
        (lvl, exp, uid)
    )
    conn.commit()

def format_char(c):
    return f"""🆔 {c['id']}
🔥 {c['name']}
⭐ {c['rarity']}
🏰 {c['faction']}
⚔️ {c['power']}
💰 {c['price']} coins
"""

# ================= BASIC COMMANDS =================

async def start(update: Update, context):
    uid = update.effective_user.id
    init_user(uid)
    await update.message.reply_text(
        "🎮 Tensura Gacha Bot\n\n"
        "/summon\n/summon10\n/store\n/inventory\n/balance\n"
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
        await update.message.reply_text("❌ Already claimed today")
        return
    c.execute("""
    UPDATE users
    SET coins=coins+?, last_daily=?
    WHERE id=?
    """, (DAILY_REWARD, now, uid))
    conn.commit()
    await update.message.reply_text(f"✅ Daily +{DAILY_REWARD}")

# ================= PHOTO UPLOAD =================

async def photo_handler(update: Update, context):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    if not update.message.caption:
        return
    # auto-save caption fields if admin sends a photo with caption
    data = {}
    for line in update.message.caption.split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip().lower()] = v.strip()
    need = ["name","rarity","faction","power","price"]
    if not all(k in data for k in need):
        await update.message.reply_text("❌ Wrong format")
        return
    c.execute("""
    INSERT INTO characters
    (name,rarity,faction,power,price,file_id)
    VALUES (?,?,?,?,?,?)
    """, (
        data["name"],
        data["rarity"],
        data["faction"],
        int(data["power"]),
        int(data["price"]),
        update.message.photo[-1].file_id
    ))
    conn.commit()
    await update.message.reply_text(f"✅ Character saved. Name: {data['name']}")

# ================= UPLOAD COMMAND =================
async def upload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin only")
        return
    # must be a reply to photo
    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        await update.message.reply_text("❌ Reply to a photo to upload")
        return
    photo = update.message.reply_to_message.photo[-1].file_id
    # parse arguments: ID|Name|Rarity|Faction|Power|Price
    if not context.args:
        await update.message.reply_text("❌ Usage: /upload [ID|]Name|Rarity|Faction|Power|Price")
        return
    args_str = " ".join(context.args)
    parts = args_str.split("|")
    if len(parts) not in [5,6]:
        await update.message.reply_text("❌ Invalid format. Example:\n/upload Benimaru|Epic|Ogre|450|200")
        return
    if len(parts) == 6:
        char_id = parts[0]
        name, rarity, faction, power, price = parts[1:]
    else:
        char_id = None
        name, rarity, faction, power, price = parts
    try:
        power = int(power)
        price = int(price)
        if char_id:
            char_id = int(char_id)
    except:
        await update.message.reply_text("❌ Power and Price must be numbers")
        return
    if char_id:
        c.execute("""
        INSERT INTO characters (id,name,rarity,faction,power,price,file_id)
        VALUES (?,?,?,?,?,?,?)
        """,(char_id,name,rarity,faction,power,price,photo))
    else:
        c.execute("""
        INSERT INTO characters (name,rarity,faction,power,price,file_id)
        VALUES (?,?,?,?,?,?)
        """,(name,rarity,faction,power,price,photo))
    conn.commit()
    await update.message.reply_text(f"✅ Character saved. Name: {name} (ID: {char_id if char_id else 'auto'})")

# ================= SUMMON =================

async def do_summon(update, times):
    uid = update.effective_user.id
    init_user(uid)
    cost = SUMMON_COST if times == 1 else TEN_SUMMON_COST
    c.execute("SELECT coins FROM users WHERE id=?", (uid,))
    coins = c.fetchone()[0]
    if coins < cost:
        await update.message.reply_text("❌ Not enough coins")
        return
    c.execute("UPDATE users SET coins=coins-? WHERE id=?", (cost, uid))
    c.execute("SELECT * FROM characters")
    chars = c.fetchall()
    if not chars:
        await update.message.reply_text("❌ No characters")
        return
    for _ in range(times):
        rarity = roll_rarity()
        pool = [x for x in chars if x[2] == rarity] or chars
        char = random.choice(pool)
        c.execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?", (uid, char[0]))
        res = c.fetchone()
        if res:
            c.execute("UPDATE inventory SET count=count+1 WHERE user_id=? AND char_id=?", (uid, char[0]))
        else:
            c.execute("INSERT INTO inventory VALUES (?,?,1)", (uid, char[0]))
        add_exp(uid,10)
        await update.message.reply_photo(
            char[6],
            caption=format_char({
                "id": char[0],"name": char[1],"rarity": char[2],
                "faction": char[3],"power": char[4],"price": char[5]
            })
        )
    conn.commit()

async def summon(update, context): await do_summon(update,1)
async def summon10(update, context): await do_summon(update,10)

# ================= STORE =================

async def store(update, context):
    c.execute("SELECT * FROM characters")
    chars = c.fetchall()
    if not chars:
        await update.message.reply_text("❌ Store empty")
        return
    char = random.choice(chars)
    keyboard = [[
        InlineKeyboardButton("🛒 Buy", callback_data=f"buy_{char[0]}"),
        InlineKeyboardButton("➡ Next", callback_data="next_store")
    ]]
    await update.message.reply_photo(
        char[6],
        caption=format_char({
            "id": char[0],"name": char[1],"rarity": char[2],
            "faction": char[3],"power": char[4],"price": char[5]
        }),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def store_btn(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    init_user(uid)
    if q.data == "next_store":
        await store(q, context)
        return
    if q.data.startswith("buy_"):
        cid = int(q.data.split("_")[1])
        c.execute("SELECT * FROM characters WHERE id=?", (cid,))
        char = c.fetchone()
        if not char:
            await q.edit_message_caption("❌ Not found")
            return
        c.execute("SELECT coins FROM users WHERE id=?", (uid,))
        coins = c.fetchone()[0]
        if coins < char[5]:
            await q.edit_message_caption("❌ Not enough coins")
            return
        c.execute("UPDATE users SET coins=coins-? WHERE id=?", (char[5], uid))
        c.execute("""
        INSERT INTO inventory VALUES (?,?,1)
        ON CONFLICT(user_id,char_id)
        DO UPDATE SET count=count+1
        """, (uid, cid))
        conn.commit()
        await q.edit_message_caption(f"✅ Bought {char[1]}")

# ================= INVENTORY =================

async def inventory(update, context):
    uid = update.effective_user.id
    init_user(uid)
    c.execute("""
    SELECT characters.name,characters.rarity,inventory.count
    FROM inventory
    JOIN characters ON inventory.char_id=characters.id
    WHERE inventory.user_id=?
    """, (uid,))
    items = c.fetchall()
    if not items:
        await update.message.reply_text("❌ Inventory empty")
        return
    msg = "🎴 Your Inventory\n\n"
    for i,(name,rarity,count) in enumerate(items,1):
        msg += f"{i}. {name} ({rarity}) x{count}\n"
    await update.message.reply_text(msg)

# ================= ADMIN =================

async def addcoins(update, context):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin only")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply to user")
        return
    if not context.args:
        await update.message.reply_text("❌ /addcoins <amount>")
        return
    target = update.message.reply_to_message.from_user.id
    try:
        amount = int(context.args[0])
    except:
        await update.message.reply_text("❌ Number only")
        return
    init_user(target)
    c.execute("UPDATE users SET coins=coins+? WHERE id=?", (amount,target))
    conn.commit()
    await update.message.reply_text("✅ Coins added")

async def addadmin(update, context):
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("❌ Owner only")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply to user")
        return
    target = update.message.reply_to_message.from_user.id
    c.execute("INSERT OR IGNORE INTO admins VALUES (?)", (target,))
    conn.commit()
    await update.message.reply_text("✅ Admin added")

# ================= MAIN =================

def main():
    if not BOT_TOKEN:
        raise Exception("BOT_TOKEN missing")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # basic commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("daily", daily))

    # gacha / summon
    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("summon10", summon10))
    app.add_handler(CommandHandler("store", store))
    app.add_handler(CallbackQueryHandler(store_btn, pattern="^(buy_|next_store)"))
    app.add_handler(CommandHandler("inventory", inventory))

    # photo / upload
    app.add_handler(MessageHandler(filters.PHOTO & filters.Caption(True), photo_handler))
    app.add_handler(CommandHandler("upload", upload_cmd))

    # admin commands
    app.add_handler(CommandHandler("addcoins", addcoins))
    app.add_handler(CommandHandler("addadmin", addadmin))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
