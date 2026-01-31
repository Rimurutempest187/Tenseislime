import json
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters, CallbackContext

# ==== CONFIGURATION ====
TOKEN = "YOUR_BOT_TOKEN"
ADMIN_IDS = [123456789]  # Default admins

CHAR_FILE = "characters.json"
INV_FILE = "inventory.json"
COIN_FILE = "coins.json"
ADMINS_FILE = "admins.json"

# ==== UTILS ====
def load_json(file_path):
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except:
        return {}

def save_json(file_path, data):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

# ==== CHARACTER CAPTION ====
def get_character_caption(char_data):
    return (
        f"ID: {char_data['id']}\n"
        f"Name: {char_data['name']}\n"
        f"Rarity: {char_data['rarity']}\n"
        f"Faction: {char_data['faction']}\n"
        f"Power: {char_data['power']}\n"
        f"Price: {char_data['price']} Coins"
    )

# ==== ADMIN CHECK ====
def is_admin(user_id):
    admins = load_json(ADMINS_FILE)
    return str(user_id) in admins

# ==== COINS ====
def get_coins(user_id):
    coins = load_json(COIN_FILE)
    return coins.get(str(user_id), 0)

def add_coins(user_id, amount):
    coins = load_json(COIN_FILE)
    coins[str(user_id)] = coins.get(str(user_id), 0) + amount
    save_json(COIN_FILE, coins)

def deduct_coins(user_id, amount):
    coins = load_json(COIN_FILE)
    if coins.get(str(user_id), 0) >= amount:
        coins[str(user_id)] -= amount
        save_json(COIN_FILE, coins)
        return True
    return False

# ==== INVENTORY ====
def add_to_inventory(user_id, char_data):
    inv = load_json(INV_FILE)
    user_inv = inv.get(str(user_id), [])
    user_inv.append(char_data)
    inv[str(user_id)] = user_inv
    save_json(INV_FILE, inv)

def get_inventory(user_id):
    inv = load_json(INV_FILE)
    return inv.get(str(user_id), [])

# ==== COMMANDS ====
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Welcome! Use /summon to summon characters or /store to buy.")

# ---- SUMMON ----
def summon(update: Update, context: CallbackContext):
    characters = list(load_json(CHAR_FILE).values())
    if not characters:
        update.message.reply_text("No characters available.")
        return
    char = random.choice(characters)
    add_to_inventory(update.message.from_user.id, char)
    caption = get_character_caption(char)
    context.bot.send_photo(chat_id=update.message.chat_id, photo=char['file_id'], caption=caption)

# ---- STORE ----
def store(update: Update, context: CallbackContext):
    characters = list(load_json(CHAR_FILE).values())
    if not characters:
        update.message.reply_text("No characters in store.")
        return

    page = int(context.args[0]) if context.args else 0
    char = characters[page]
    caption = get_character_caption(char)

    buttons = [
        [InlineKeyboardButton("Buy", callback_data=f"buy_{char['id']}_{page}")],
    ]
    if page + 1 < len(characters):
        buttons.append([InlineKeyboardButton("Next", callback_data=f"store_{page+1}")])
    reply_markup = InlineKeyboardMarkup(buttons)
    context.bot.send_photo(chat_id=update.message.chat_id, photo=char['file_id'], caption=caption, reply_markup=reply_markup)

def store_button(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    characters = list(load_json(CHAR_FILE).values())

    if data.startswith("store_"):
        page = int(data.split("_")[1])
        char = characters[page]
        caption = get_character_caption(char)
        buttons = [
            [InlineKeyboardButton("Buy", callback_data=f"buy_{char['id']}_{page}")],
        ]
        if page + 1 < len(characters):
            buttons.append([InlineKeyboardButton("Next", callback_data=f"store_{page+1}")])
        reply_markup = InlineKeyboardMarkup(buttons)
        query.edit_message_media(media=None)  # Clear media to prevent duplicate
        context.bot.send_photo(chat_id=query.message.chat_id, photo=char['file_id'], caption=caption, reply_markup=reply_markup)

    elif data.startswith("buy_"):
        _, char_id, page = data.split("_")
        char_data = load_json(CHAR_FILE)[char_id]
        price = char_data['price']
        if deduct_coins(query.from_user.id, price):
            add_to_inventory(query.from_user.id, char_data)
            query.answer(f"You bought {char_data['name']}!")
        else:
            query.answer("Not enough coins!")

# ---- INVENTORY ----
def inventory(update: Update, context: CallbackContext):
    user_inv = get_inventory(update.message.from_user.id)
    if not user_inv:
        update.message.reply_text("Inventory is empty.")
        return
    for char in user_inv:
        caption = get_character_caption(char)
        context.bot.send_photo(chat_id=update.message.chat_id, photo=char['file_id'], caption=caption)

# ---- COINS ----
def coins_cmd(update: Update, context: CallbackContext):
    coins = get_coins(update.message.from_user.id)
    update.message.reply_text(f"You have {coins} Coins.")

# ---- RANKING ----
def ranking(update: Update, context: CallbackContext):
    inv = load_json(INV_FILE)
    coins_db = load_json(COIN_FILE)
    leaderboard = []
    for uid in inv:
        leaderboard.append((uid, coins_db.get(uid, 0), len(inv[uid])))
    leaderboard.sort(key=lambda x: (x[1], x[2]), reverse=True)
    msg = "🏆 Leaderboard 🏆\n"
    for i, (uid, coin, count) in enumerate(leaderboard[:10], start=1):
        msg += f"{i}. User {uid} - Coins: {coin}, Characters: {count}\n"
    update.message.reply_text(msg)

# ---- ADMIN ----
def add_admin(update: Update, context: CallbackContext):
    if update.message.from_user.id not in ADMIN_IDS:
        update.message.reply_text("You are not admin.")
        return
    if not context.args:
        update.message.reply_text("Usage: /add_admin user_id")
        return
    user_id = context.args[0]
    admins = load_json(ADMINS_FILE)
    admins[user_id] = True
    save_json(ADMINS_FILE, admins)
    update.message.reply_text(f"User {user_id} added as admin.")

def remove_admin(update: Update, context: CallbackContext):
    if update.message.from_user.id not in ADMIN_IDS:
        update.message.reply_text("You are not admin.")
        return
    if not context.args:
        update.message.reply_text("Usage: /remove_admin user_id")
        return
    user_id = context.args[0]
    admins = load_json(ADMINS_FILE)
    if user_id in admins:
        del admins[user_id]
        save_json(ADMINS_FILE, admins)
        update.message.reply_text(f"User {user_id} removed from admin.")
    else:
        update.message.reply_text("User not found in admins.")

# ==== MAIN ====
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # Commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("summon", summon))
    dp.add_handler(CommandHandler("store", store))
    dp.add_handler(CommandHandler("inventory", inventory))
    dp.add_handler(CommandHandler("coins", coins_cmd))
    dp.add_handler(CommandHandler("ranking", ranking))
    dp.add_handler(CommandHandler("add_admin", add_admin))
    dp.add_handler(CommandHandler("remove_admin", remove_admin))

    # Callback query
    dp.add_handler(CallbackQueryHandler(store_button))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
