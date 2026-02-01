import os, random, sqlite3, logging, time, json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 1812962224

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

# Users Table
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    coins INTEGER, 
    lvl INTEGER, 
    exp INTEGER, 
    last_daily INTEGER
)
""")

# Characters Table
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

# Inventory Table (PRIMARY KEY for stacking)
c.execute("""
CREATE TABLE IF NOT EXISTS inventory (
    user_id INTEGER,
    char_id INTEGER,
    count INTEGER,
    PRIMARY KEY (user_id, char_id)
)
""")

# Admins Table
c.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
""")
conn.commit()

# ================= HELPERS =================
def is_owner(uid): return uid == OWNER_ID

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
    c.execute("UPDATE users SET lvl=?, exp=? WHERE id=?", (lvl, exp, uid))
    conn.commit()

def format_char(char_dict):
    return f"""🆔 {char_dict['id']}
🔥 {char_dict['name']}
⭐ {char_dict['rarity']}
🏰 {char_dict['faction']}
⚔️ {char_dict['power']}
💰 {char_dict['price']} coins
"""

def add_to_inventory(uid, cid):
    c.execute("""
    INSERT INTO inventory (user_id,char_id,count)
    VALUES (?,?,1)
    ON CONFLICT(user_id,char_id)
    DO UPDATE SET count = count + 1
    """, (uid, cid))
    conn.commit()

# ================= MIGRATE CHARACTERS =================
def migrate_characters():
    if os.path.exists("characters.json"):
        with open("characters.json","r") as f:
            chars = json.load(f)
        for char in chars:
            c.execute("""
            INSERT OR IGNORE INTO characters (id,name,rarity,faction,power,price,file_id)
            VALUES (?,?,?,?,?,?,?)
            """, (
                char["id"], char["name"], char["rarity"],
                char["faction"], int(char["power"]),
                int(char["price"]), char["file_id"]
            ))
        conn.commit()
        print("✅ character.json migrated to DB")

# ================= BASIC COMMANDS =================
async def start(update: Update, context):
    uid = update.effective_user.id
    init_user(uid)
    await update.message.reply_text(
        "🎮 Tensura Gacha Bot\n\n"
        "/summon\n/summon10\n/store\n/inventory\n/balance\n/daily"
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
    c.execute("UPDATE users SET coins=coins+?, last_daily=? WHERE id=?", (DAILY_REWARD, now, uid))
    conn.commit()
    await update.message.reply_text(f"✅ Daily +{DAILY_REWARD}")

# ================= PHOTO UPLOAD =================
async def photo_handler(update: Update, context):
    uid = update.effective_user.id
    if not is_admin(uid) or not update.message.caption:
        return
    data = {}
    for line in update.message.caption.split("\n"):
        if ":" not in line: continue
        k,v = line.split(":",1)
        data[k.strip().lower()] = v.strip()
    need = ["name","rarity","faction","power","price"]
    if not all(k in data for k in need):
        await update.message.reply_text("❌ Wrong format")
        return
    c.execute("""
    INSERT INTO characters (name,rarity,faction,power,price,file_id)
    VALUES (?,?,?,?,?,?)
    """, (data["name"], data["rarity"], data["faction"], int(data["power"]), int(data["price"]), update.message.photo[-1].file_id))
    conn.commit()
    await update.message.reply_text("✅ Character saved")

# ================= SUMMON =================
async def summon(update, context): await do_summon(update, 1)
async def summon10(update, context): await do_summon(update, 10)

async def do_summon(update, times):
    uid = update.effective_user.id
    init_user(uid)
    cost = SUMMON_COST if times==1 else TEN_SUMMON_COST
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
        pool = [x for x in chars if x[2]==rarity] or chars
        char = random.choice(pool)
        add_to_inventory(uid, char[0])
        add_exp(uid, 10)
        await update.message.reply_photo(
            char[6],
            caption=format_char({
                "id": char[0],
                "name": char[1],
                "rarity": char[2],
                "faction": char[3],
                "power": char[4],
                "price": char[5]
            })
        )

# ================= STORE =================
async def store(update, context):
    uid = update.effective_user.id
    init_user(uid)
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
            "id": char[0],
            "name": char[1],
            "rarity": char[2],
            "faction": char[3],
            "power": char[4],
            "price": char[5]
        }),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def store_btn(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    init_user(uid)
    if q.data == "next_store":
        await store(update, context)
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
        add_to_inventory(uid, cid)
        await q.edit_message_caption(f"✅ Bought {char[1]}")

# ================= INVENTORY =================
async def inventory(update, context):
    uid = update.effective_user.id
    init_user(uid)
    c.execute("""
    SELECT characters.name, characters.rarity, inventory.count
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
    try: amount = int(context.args[0])
    except: await update.message.reply_text("❌ Number only"); return
    init_user(target)
    c.execute("UPDATE users SET coins=coins+? WHERE id=?", (amount, target))
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
    migrate_characters()
    if not BOT_TOKEN:
        raise Exception("BOT_TOKEN missing")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("summon10", summon10))
    app.add_handler(CommandHandler("store", store))
    app.add_handler(CallbackQueryHandler(store_btn, pattern="^(buy_|next_store)"))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(MessageHandler(filters.PHOTO & filters.Caption(True), photo_handler))
    app.add_handler(CommandHandler("addcoins", addcoins))
    app.add_handler(CommandHandler("addadmin", addadmin))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
