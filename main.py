import json
import random
import os
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

BOT_TOKEN = "8372081478:AAHK5cw9n-TL6QJ4vRXYMSauJC2yX-uart8"

CHAR_FILE = "characters.json"
INV_FILE = "inventory.json"
COIN_FILE = "coins.json"
ADMINS_FILE = "admins.json"

DAILY_START_COINS = 100
MAX_BUY = 5


RARITY_RATE = {
    "Common": 60,
    "Rare": 25,
    "Epic": 10,
    "Legendary": 5
}


# ================= DB =================

def load_json(file):
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump({}, f)
    with open(file, "r") as f:
        return json.load(f)


def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)


# ================= ADMIN =================

def is_admin(uid):
    admins = load_json(ADMINS_FILE)
    return str(uid) in admins


# ================= USER INIT =================

def init_user(uid):
    coins = load_json(COIN_FILE)
    inv = load_json(INV_FILE)

    if uid not in coins:
        coins[uid] = DAILY_START_COINS

    if uid not in inv:
        inv[uid] = []

    save_json(COIN_FILE, coins)
    save_json(INV_FILE, inv)


# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = str(update.effective_user.id)

    init_user(uid)

    coins = load_json(COIN_FILE)[uid]
    inv = load_json(INV_FILE)[uid]

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

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = str(update.effective_user.id)

    init_user(uid)

    coins = load_json(COIN_FILE)[uid]

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

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = str(update.effective_user.id)

    init_user(uid)

    chars = load_json(CHAR_FILE)

    if not chars:
        await update.message.reply_text("No characters yet.")
        return

    rarity = roll_rarity()

    pool = [c for c in chars if c["rarity"] == rarity]

    if not pool:
        await update.message.reply_text("No characters for this rarity.")
        return

    char = random.choice(pool)

    inv = load_json(INV_FILE)
    inv[uid].append(char)
    save_json(INV_FILE, inv)

    caption = format_char(char)

    await update.message.reply_photo(char["file_id"], caption=caption)


# ================= STORE =================

async def store(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chars = load_json(CHAR_FILE)

    if not chars:
        await update.message.reply_text("Store empty.")
        return

    char = random.choice(chars)

    keyboard = [
        [
            InlineKeyboardButton("Buy", callback_data=f"buy_{char['id']}"),
            InlineKeyboardButton("Next", callback_data="next")
        ]
    ]

    await update.message.reply_photo(
        char["file_id"],
        caption=format_char(char),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def store_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    data = q.data
    uid = str(q.from_user.id)

    init_user(uid)

    if data == "next":
        await store(update, context)
        return

    if data.startswith("buy_"):

        cid = data.split("_")[1]

        chars = load_json(CHAR_FILE)
        char = next((c for c in chars if str(c["id"]) == cid), None)

        if not char:
            await q.edit_message_text("Not found")
            return

        coins = load_json(COIN_FILE)
        inv = load_json(INV_FILE)

        if coins[uid] < char["price"]:
            await q.edit_message_text("Not enough coins.")
            return

        coins[uid] -= char["price"]
        inv[uid].append(char)

        save_json(COIN_FILE, coins)
        save_json(INV_FILE, inv)

        await q.edit_message_caption(f"✅ Bought {char['name']}")


# ================= INVENTORY =================

async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = str(update.effective_user.id)

    init_user(uid)

    inv = load_json(INV_FILE)[uid]

    if not inv:
        await update.message.reply_text("Empty inventory.")
        return

    msg = "🎴 Inventory\n\n"

    for i, c in enumerate(inv, 1):
        msg += f"{i}. {c['name']} ({c['rarity']})\n"

    await update.message.reply_text(msg)


# ================= RANKING =================

async def ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):

    coins = load_json(COIN_FILE)
    inv = load_json(INV_FILE)

    board = []

    for u in coins:
        board.append((u, coins[u], len(inv.get(u, []))))

    board.sort(key=lambda x: (x[1], x[2]), reverse=True)

    msg = "🏆 Ranking\n\n"

    for i, u in enumerate(board[:10], 1):
        msg += f"{i}. {u[1]} coins | {u[2]} chars\n"

    await update.message.reply_text(msg)


# ================= ADMIN =================

async def add_admin(update: Update, context):

    uid = str(update.effective_user.id)

    if not is_admin(uid):
        return

    if not context.args:
        return

    target = context.args[0]

    admins = load_json(ADMINS_FILE)
    admins.append(target)

    save_json(ADMINS_FILE, admins)

    await update.message.reply_text("Admin added.")


async def remove_admin(update: Update, context):

    uid = str(update.effective_user.id)

    if not is_admin(uid):
        return

    target = context.args[0]

    admins = load_json(ADMINS_FILE)
    admins.remove(target)

    save_json(ADMINS_FILE, admins)

    await update.message.reply_text("Admin removed.")


# ================= ADD COINS =================

async def addcoins(update: Update, context):

    uid = str(update.effective_user.id)

    if not is_admin(uid):
        return

    if not update.message.reply_to_message:
        return

    target = str(update.message.reply_to_message.from_user.id)

    amount = int(context.args[0])

    coins = load_json(COIN_FILE)

    coins[target] = coins.get(target, 0) + amount

    save_json(COIN_FILE, coins)

    await update.message.reply_text("Coins added.")


# ================= ADD CHAR =================

async def addchar(update: Update, context):

    uid = str(update.effective_user.id)

    if not is_admin(uid):
        return

    if not update.message.reply_to_message:
        return

    target = str(update.message.reply_to_message.from_user.id)

    cid = context.args[0]

    chars = load_json(CHAR_FILE)
    char = next((c for c in chars if str(c["id"]) == cid), None)

    if not char:
        return

    inv = load_json(INV_FILE)
    inv[target].append(char)

    save_json(INV_FILE, inv)

    await update.message.reply_text("Character given.")


# ================= PHOTO REGISTER =================

async def photo_handler(update: Update, context):

    uid = str(update.effective_user.id)

    if not is_admin(uid):
        return

    if not update.message.caption:
        return

    data = {}

    for line in update.message.caption.split("\n"):
        k, v = line.split(":")
        data[k.strip().lower()] = v.strip()

    chars = load_json(CHAR_FILE)

    data["id"] = len(chars) + 1
    data["file_id"] = update.message.photo[-1].file_id

    data["price"] = int(data["price"])
    data["power"] = int(data["power"])

    chars.append(data)

    save_json(CHAR_FILE, chars)

    await update.message.reply_text("Character saved.")


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
    app.add_handler(CommandHandler("remove_admin", remove_admin))

    app.add_handler(CommandHandler("addcoins", addcoins))
    app.add_handler(CommandHandler("addchar", addchar))

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    app.add_handler(CallbackQueryHandler(store_btn))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
