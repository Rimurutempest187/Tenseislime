import os
import json
import random
from collections import Counter

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

CHAR_FILE = "characters.json"
INV_FILE = "inventory.json"
COIN_FILE = "coins.json"
ADMINS_FILE = "admins.json"

START_COINS = 100
ITEMS_PER_PAGE = 5

RARITY_RATE = {
    "Common": 60,
    "Rare": 25,
    "Epic": 10,
    "Legendary": 5
}

RARITY_ORDER = {
    "Legendary": 4,
    "Epic": 3,
    "Rare": 2,
    "Common": 1
}

# ================= JSON =================


def load_json(file, default):

    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump(default, f)

    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return default


def save_json(file, data):

    with open(file, "w") as f:
        json.dump(data, f, indent=4)


# ================= ADMIN =================


def is_admin(uid):

    admins = load_json(ADMINS_FILE, [])
    return str(uid) in admins


# ================= USER INIT =================


def init_user(uid):

    coins = load_json(COIN_FILE, {})
    inv = load_json(INV_FILE, {})

    if uid not in coins:
        coins[uid] = START_COINS

    if uid not in inv:
        inv[uid] = []

    save_json(COIN_FILE, coins)
    save_json(INV_FILE, inv)


# ================= START =================


async def start(update: Update, context):

    uid = str(update.effective_user.id)

    init_user(uid)

    coins = load_json(COIN_FILE, {})[uid]
    inv = load_json(INV_FILE, {})[uid]

    msg = f"""
🎮 Gacha Bot

💰 Coins: {coins}
🎴 Characters: {len(inv)}

Commands:
/summon
/store
/inventory
/ranking
/balance
"""

    await update.message.reply_text(msg)


# ================= BALANCE =================


async def balance(update: Update, context):

    uid = str(update.effective_user.id)

    init_user(uid)

    coins = load_json(COIN_FILE, {})[uid]

    await update.message.reply_text(f"💰 Coins: {coins}")


# ================= RARITY =================


def roll_rarity():

    r = random.randint(1, 100)

    total = 0

    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k

    return "Common"


# ================= SUMMON =================


async def summon(update: Update, context):

    uid = str(update.effective_user.id)

    init_user(uid)

    chars = load_json(CHAR_FILE, [])

    if not chars:
        await update.message.reply_text("❌ No characters yet.")
        return

    rarity = roll_rarity()

    pool = [c for c in chars if c["rarity"] == rarity]

    if not pool:
        pool = chars

    char = random.choice(pool)

    inv = load_json(INV_FILE, {})
    inv[uid].append(char)

    save_json(INV_FILE, inv)

    await update.message.reply_photo(
        char["file_id"],
        caption=format_char(char)
    )


# ================= STORE =================


async def send_store(chat_id, context):

    chars = load_json(CHAR_FILE, [])

    if not chars:
        await context.bot.send_message(chat_id, "❌ Store empty.")
        return

    char = random.choice(chars)

    keyboard = [
        [
            InlineKeyboardButton("🛒 Buy", callback_data=f"buy_{char['id']}"),
            InlineKeyboardButton("➡ Next", callback_data="store_next")
        ]
    ]

    await context.bot.send_photo(
        chat_id,
        char["file_id"],
        caption=format_char(char),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def store(update: Update, context):

    await send_store(update.effective_chat.id, context)


async def store_btn(update: Update, context):

    q = update.callback_query
    await q.answer()

    uid = str(q.from_user.id)

    init_user(uid)

    if q.data == "store_next":

        await send_store(q.message.chat_id, context)
        return

    if q.data.startswith("buy_"):

        cid = q.data.split("_")[1]

        chars = load_json(CHAR_FILE, [])

        char = next((c for c in chars if str(c["id"]) == cid), None)

        if not char:
            await q.edit_message_caption("❌ Not found")
            return

        coins = load_json(COIN_FILE, {})
        inv = load_json(INV_FILE, {})

        if coins[uid] < char["price"]:
            await q.edit_message_caption("❌ Not enough coins.")
            return

        coins[uid] -= char["price"]
        inv[uid].append(char)

        save_json(COIN_FILE, coins)
        save_json(INV_FILE, inv)

        await q.edit_message_caption(f"✅ Bought {char['name']}")


# ================= INVENTORY =================


async def inventory(update: Update, context):

    uid = str(update.effective_user.id)

    init_user(uid)

    inv = load_json(INV_FILE, {})[uid]

    if not inv:
        await update.message.reply_text("❌ Empty inventory.")
        return

    counter = Counter([c["id"] for c in inv])

    chars = load_json(CHAR_FILE, [])

    result = []

    for cid, count in counter.items():

        char = next((c for c in chars if c["id"] == cid), None)

        if char:
            result.append((char, count))

    result.sort(
        key=lambda x: RARITY_ORDER.get(x[0]["rarity"], 0),
        reverse=True
    )

    msg = "🎴 Inventory\n\n"

    for i, (c, count) in enumerate(result, 1):

        msg += f"{i}. {c['name']} ({c['rarity']}) x{count}\n"

    await update.message.reply_text(msg)


# ================= RANKING =================


async def ranking(update: Update, context):

    coins = load_json(COIN_FILE, {})
    inv = load_json(INV_FILE, {})

    board = []

    for uid in coins:

        try:
            chat = await context.bot.get_chat(int(uid))
            name = chat.first_name
        except:
            name = uid

        char_count = len(inv.get(uid, []))

        board.append((name, coins[uid], char_count))

    board.sort(key=lambda x: (x[1], x[2]), reverse=True)

    msg = "🏆 Ranking\n\n"

    for i, (name, coin, chars) in enumerate(board[:10], 1):

        msg += f"{i}. {name} | 💰{coin} | 🎴{chars}\n"

    await update.message.reply_text(msg)


# ================= ADMIN =================


async def add_admin(update: Update, context):

    uid = str(update.effective_user.id)

    admins = load_json(ADMINS_FILE, [])

    if not admins:
        admins.append(uid)
        save_json(ADMINS_FILE, admins)

    if uid not in admins:
        return

    if not context.args:
        return

    admins.append(context.args[0])

    save_json(ADMINS_FILE, admins)

    await update.message.reply_text("✅ Admin added.")


# ================= ADD COINS =================


async def addcoins(update: Update, context):

    uid = str(update.effective_user.id)

    if not is_admin(uid):
        return

    if not update.message.reply_to_message:
        return

    target = str(update.message.reply_to_message.from_user.id)

    amount = int(context.args[0])

    coins = load_json(COIN_FILE, {})

    coins[target] = coins.get(target, 0) + amount

    save_json(COIN_FILE, coins)

    await update.message.reply_text("✅ Coins added.")


# ================= PHOTO REGISTER =================


async def photo_handler(update: Update, context):

    uid = str(update.effective_user.id)

    if not is_admin(uid):
        return

    if not update.message.caption:
        return

    data = {}

    for line in update.message.caption.split("\n"):

        if ":" not in line:
            continue

        k, v = line.split(":", 1)

        data[k.strip().lower()] = v.strip()

    need = ["name", "rarity", "faction", "power", "price"]

    if not all(k in data for k in need):
        await update.message.reply_text("❌ Caption format wrong.")
        return

    chars = load_json(CHAR_FILE, [])

    new_char = {
        "id": len(chars) + 1,
        "name": data["name"],
        "rarity": data["rarity"],
        "faction": data["faction"],
        "power": int(data["power"]),
        "price": int(data["price"]),
        "file_id": update.message.photo[-1].file_id
    }

    chars.append(new_char)

    save_json(CHAR_FILE, chars)

    await update.message.reply_text("✅ Character saved.")


# ================= FORMAT =================


def format_char(c):

    return f"""
🆔 {c['id']}
🔥 {c['name']}
⭐ {c['rarity']}
🏰 {c['faction']}
⚔️ {c['power']}
💰 {c['price']} coins
"""


# ================= MAIN =================


def main():

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("store", store))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("ranking", ranking))

    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("addcoins", addcoins))

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    app.add_handler(CallbackQueryHandler(store_btn))

    print("Bot running...")

    app.run_polling()


if __name__ == "__main__":
    main()
