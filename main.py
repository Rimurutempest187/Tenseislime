import json
import os
import random
import logging
import sqlite3

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)


# ================= CONFIG =================

BOT_TOKEN = "8372081478:AAHK5cw9n-TL6QJ4vRXYMSauJC2yX-uart8"

ADMIN_IDS = [
    1812962224   # <-- Your Telegram ID
]

DB_FILE = "bot.db"
CHAR_FILE = "characters.json"

MAX_DAILY = 5


# ================= LOG =================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ================= DATABASE =================

def init_db():

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        coins INTEGER,
        daily_count INTEGER,
        daily_date TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS cards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        char_id INTEGER
    )
    """)

    conn.commit()
    conn.close()


def get_user(uid):

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()

    if not row:

        c.execute("""
        INSERT INTO users VALUES(?,?,?,?)
        """, (uid, 100, 0, ""))

        conn.commit()

        row = (uid, 100, 0, "")

    conn.close()

    return row


def update_user(uid, coins, count, date):

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    UPDATE users SET coins=?,daily_count=?,daily_date=?
    WHERE user_id=?
    """, (coins, count, date, uid))

    conn.commit()
    conn.close()


def add_card(uid, cid):

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    INSERT INTO cards(user_id,char_id)
    VALUES(?,?)
    """, (uid, cid))

    conn.commit()
    conn.close()


def count_cards(uid):

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
    SELECT COUNT(*) FROM cards WHERE user_id=?
    """, (uid,))

    n = c.fetchone()[0]

    conn.close()

    return n


# ================= CHAR JSON =================

def load_chars():

    if not os.path.exists(CHAR_FILE):

        with open(CHAR_FILE,"w") as f:
            json.dump([], f)

    with open(CHAR_FILE,"r",encoding="utf-8") as f:
        return json.load(f)


def save_chars(data):

    with open(CHAR_FILE,"w",encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ================= RARITY =================

RARITY = ["Common","Rare","Epic","Legendary"]

EMOJI = {
    "Common":"🌱",
    "Rare":"🔮",
    "Epic":"🔥",
    "Legendary":"👑"
}


def roll():

    r = random.randint(1,100)

    if r<=60: return "Common"
    if r<=85: return "Rare"
    if r<=95: return "Epic"
    return "Legendary"


# ================= COMMANDS =================

async def start(update:Update, context):

    uid = update.effective_user.id

    user = get_user(uid)

    cards = count_cards(uid)

    text = f"""
🌌 Welcome!

💰 Coins: {user[1]}
🎴 Cards: {cards}

/store - Shop
/bal - Balance
"""

    await update.message.reply_text(text)


async def bal(update:Update, context):

    uid = update.effective_user.id

    user = get_user(uid)

    cards = count_cards(uid)

    await update.message.reply_text(
        f"💳 Balance\n\n💰 {user[1]} coins\n🎴 {cards} cards"
    )


# ================= STORE =================

async def store(update:Update, context):

    chars = load_chars()

    if not chars:

        await update.message.reply_text("Store empty.")
        return


    char = random.choice(chars)

    text = f"""
{EMOJI[char['rarity']]} {char['name']}
⭐ {char['rarity']}
💰 {char['price']} coins
"""

    kb = [
        [
            InlineKeyboardButton(
                "🛒 Buy",
                callback_data=f"buy_{char['id']}"
            )
        ]
    ]

    markup = InlineKeyboardMarkup(kb)


    if "image_file_id" in char:

        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=char["image_file_id"],
            caption=text,
            reply_markup=markup
        )

    else:

        await update.message.reply_text(text, reply_markup=markup)



# ================= BUY =================

async def buy(update:Update, context, cid):

    q = update.callback_query
    uid = q.from_user.id

    user = get_user(uid)

    coins = user[1]

    chars = load_chars()

    char = next((c for c in chars if c["id"]==cid),None)

    if not char:

        await q.edit_message_text("Invalid.")
        return


    if coins < char["price"]:

        await q.edit_message_text("Not enough coins.")
        return


    coins -= char["price"]

    update_user(uid, coins, user[2], user[3])

    add_card(uid, cid)


    await q.edit_message_text(
        f"✅ Bought {char['name']}\n💰 Left: {coins}"
    )



# ================= ADMIN PHOTO SAVE =================

async def save_photo(update:Update, context):

    uid = update.effective_user.id

    if uid not in ADMIN_IDS:
        return


    photo = update.message.photo[-1]

    fid = photo.file_id


    chars = load_chars()

    nid = max([c["id"] for c in chars],default=0)+1


    new = {
        "id": nid,
        "name": f"Char {nid}",
        "rarity": "Common",
        "price": 100,
        "faction": "Unknown",
        "image_file_id": fid
    }


    chars.append(new)

    save_chars(chars)


    await update.message.reply_text(
        f"✅ Character Saved\nID: {nid}"
    )


# ================= ADMIN =================

async def give_coin(update:Update, context):

    uid = update.effective_user.id

    if uid not in ADMIN_IDS:
        return


    if len(context.args)!=2:
        await update.message.reply_text("/give_coin id amount")
        return


    tid = int(context.args[0])
    amt = int(context.args[1])


    user = get_user(tid)

    coins = user[1] + amt

    update_user(tid, coins, user[2], user[3])


    await update.message.reply_text("✅ Done")


# ================= CALLBACK =================

async def button(update:Update, context):

    q = update.callback_query

    await q.answer()


    data = q.data


    if data.startswith("buy_"):

        cid = int(data.split("_")[1])

        await buy(update, context, cid)



# ================= MAIN =================

def main():

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()


    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bal", bal))
    app.add_handler(CommandHandler("store", store))

    app.add_handler(CommandHandler("give_coin", give_coin))

    app.add_handler(MessageHandler(filters.PHOTO, save_photo))

    app.add_handler(CallbackQueryHandler(button))


    print("🤖 Bot Started")

    app.run_polling()



if __name__=="__main__":
    main()
