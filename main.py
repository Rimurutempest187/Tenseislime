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

BOT_TOKEN = "8372081478:AAH-2T20JP0LBH2SQMFJbUbObl_DJqfjB2w"

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

# ================= DB =================

def load_json(file, default=None):
    if default is None:
        default = {}
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump(default, f)
    with open(file, "r") as f:
        try:
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text("❌ No characters for this rarity.")
        return
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
            InlineKeyboardButton("➡ Next", callback_data="next")
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
    if q.data == "next":
        await send_store(q.message.chat.id, context)
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

def count_chars(inv_list):
    count_dict = {}
    for c in inv_list:
        cid = c['id']
        if cid not in count_dict:
            count_dict[cid] = {"char": c, "count": 1}
        else:
            count_dict[cid]["count"] += 1
    return count_dict

async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE, page=1):
    uid = str(update.effective_user.id)
    init_user(uid)
    inv_list = load_json(INV_FILE, {}).get(uid, [])
    if not inv_list:
        await update.message.reply_text("❌ Empty inventory.")
        return
    counted = list(count_chars(inv_list).values())
    # sort by rarity
    rarity_order = {"Legendary":4,"Epic":3,"Rare":2,"Common":1}
    counted.sort(key=lambda x: rarity_order.get(x["char"]["rarity"],0), reverse=True)
    total_pages = (len(counted)-1)//ITEMS_PER_PAGE + 1
    start = (page-1)*ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    msg = f"🎴 Inventory (Page {page}/{total_pages})\n\n"
    for i, val in enumerate(counted[start:end], start+1):
        c = val["char"]
        msg += f"{i}. {c['name']} ({c['rarity']}) x{val['count']}\n"
    keyboard = []
    if page>1:
        keyboard.append(InlineKeyboardButton("⬅ Previous", callback_data=f"inv_{page-1}"))
    if page<total_pages:
        keyboard.append(InlineKeyboardButton("➡ Next", callback_data=f"inv_{page+1}"))
    reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
    await update.message.reply_text(msg, reply_markup=reply_markup)

async def inv_btn(update: Update, context):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith("inv_"):
        page = int(data.split("_")[1])
        await inventory(update, context, page=page)

# ================= RANKING =================

async def ranking(update: Update, context: ContextTypes.DEFAULT_TYPE, mode="coin"):
    coins = load_json(COIN_FILE, {})
    inv = load_json(INV_FILE, {})
    board = []
    for u in coins:
        board.append({"uid": u, "coins": coins[u], "chars": len(inv.get(u, []))})
    if mode=="coin":
        board.sort(key=lambda x:(x["coins"],x["chars"]), reverse=True)
        title="💰 Coin Ranking"
    else:
        board.sort(key=lambda x:(x["chars"],x["coins"]), reverse=True)
        title="🎴 Character Ranking"
    msg = f"🏆 {title}\n\n"
    for i, u in enumerate(board[:10],1):
        msg += f"{i}. {u['uid']} | {u['coins']}💰 | {u['chars']}🎴\n" if mode=="coin" else f"{i}. {u['uid']} | {u['chars']}🎴 | {u['coins']}💰\n"
    keyboard = [
        [InlineKeyboardButton("💰 Coins", callback_data="rank_coin"), InlineKeyboardButton("🎴 Characters", callback_data="rank_char")]
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def rank_btn(update: Update, context):
    q = update.callback_query
    await q.answer()
    if q.data=="rank_coin":
        await ranking(update, context, mode="coin")
    elif q.data=="rank_char":
        await ranking(update, context, mode="char")

# ================= ADMIN COMMANDS =================

async def add_admin(update: Update, context):
    uid = str(update.effective_user.id)
    admins = load_json(ADMINS_FILE, [])
    if uid not in admins:
        return
    if not context.args:
        return
    admins.append(context.args[0])
    save_json(ADMINS_FILE, admins)
    await update.message.reply_text("✅ Admin added.")

async def remove_admin(update: Update, context):
    uid = str(update.effective_user.id)
    admins = load_json(ADMINS_FILE, [])
    if uid not in admins:
        return
    if not context.args:
        return
    target = context.args[0]
    if target in admins:
        admins.remove(target)
        save_json(ADMINS_FILE, admins)
        await update.message.reply_text("✅ Admin removed.")

# ================= ADD COINS =================

async def addcoins(update: Update, context):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        return
    if not update.message.reply_to_message or not context.args:
        return
    target = str(update.message.reply_to_message.from_user.id)
    try:
        amount = int(context.args[0])
    except:
        return
    coins = load_json(COIN_FILE, {})
    coins[target] = coins.get(target,0)+amount
    save_json(COIN_FILE, coins)
    await update.message.reply_text("✅ Coins added.")

# ================= ADD CHAR =================

async def addchar(update: Update, context):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        return
    if not update.message.reply_to_message or not context.args:
        return
    target = str(update.message.reply_to_message.from_user.id)
    cid = context.args[0]
    chars = load_json(CHAR_FILE, [])
    char = next((c for c in chars if str(c["id"])==cid), None)
    if not char:
        return
    inv = load_json(INV_FILE, {})
    inv[target].append(char)
    save_json(INV_FILE, inv)
    await update.message.reply_text("✅ Character given.")

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
        k,v=line.split(":",1)
        data[k.strip().lower()]=v.strip()
    need = ["name","rarity","faction","power","price"]
    if not all(k in data for k in need):
        await update.message.reply_text("❌ Caption format wrong.")
        return
    chars = load_json(CHAR_FILE, [])
    data["id"]=len(chars)+1
    data["file_id"]=update.message.photo[-1].file_id
    data["price"]=int(data["price"])
    data["power"]=int(data["power"])
    chars.append(data)
    save_json(CHAR_FILE, chars)
    await update.message.reply_text("✅ Character saved.")

# ================= FORMAT =================

def format_char(c):
    return f"""🆔 {c['id']}
🔥 {c['name']}
⭐ {c['rarity']}
🏰 {c['faction']}
⚔️ {c['power']}
💰 {c['price']} coins"""

# ================= MAIN =================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("store", store))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("ranking", ranking))

    # Admin
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("addcoins", addcoins))
    app.add_handler(CommandHandler("addchar", addchar))

    # Photo
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # Callback
    app.add_handler(CallbackQueryHandler(store_btn))
    app.add_handler(CallbackQueryHandler(inv_btn))
    app.add_handler(CallbackQueryHandler(rank_btn))

    print("Bot running...")
    app.run_polling()

if __name__=="__main__":
    main()
