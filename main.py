import os
import json
import random
from typing import Tuple, List, Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # set env var BOT_TOKEN

CHAR_FILE = "characters.json"   # expects a list of character dicts
INV_FILE = "inventory.json"    # expects a dict: { "user_id": [char_obj, ...], ... }
COIN_FILE = "coins.json"       # expects a dict: { "user_id": coins, ... }
ADMINS_FILE = "admins.json"    # expects a list: [ "12345", "67890", ... ]

START_COINS = 100
ITEMS_PER_PAGE = 5

RARITY_RATE = {
    "Common": 60,
    "Rare": 25,
    "Epic": 10,
    "Legendary": 5
}

RARITY_ORDER = {"Legendary": 4, "Epic": 3, "Rare": 2, "Common": 1}

# ---------------- JSON helpers ----------------
def ensure_file(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=4)

def load_json(path: str, default):
    ensure_file(path, default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# ---------------- Admin check ----------------
def is_admin(uid: str) -> bool:
    admins = load_json(ADMINS_FILE, [])
    # store admin ids as strings
    return str(uid) in admins

# ---------------- User init ----------------
def init_user(uid: str):
    coins = load_json(COIN_FILE, {})
    inv = load_json(INV_FILE, {})
    if uid not in coins:
        coins[uid] = START_COINS
    if uid not in inv:
        inv[uid] = []
    save_json(COIN_FILE, coins)
    save_json(INV_FILE, inv)

# ---------------- Format char ----------------
def format_char(c: Dict[str, Any]) -> str:
    # Defensive: provide defaults if missing
    return (
        f"🆔 {c.get('id','?')}\n"
        f"🔥 {c.get('name','Unknown')}\n"
        f"⭐ {c.get('rarity','Unknown')}\n"
        f"🏰 {c.get('faction','-')}\n"
        f"⚔️ {c.get('power',0)}\n"
        f"💰 {c.get('price',0)} coins"
    )

# ---------------- Rarity roll ----------------
def roll_rarity() -> str:
    r = random.randint(1, 100)
    total = 0
    for k, v in RARITY_RATE.items():
        total += v
        if r <= total:
            return k
    return "Common"

# ---------------- START / BALANCE ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    init_user(uid)
    coins = load_json(COIN_FILE, {}).get(uid, 0)
    inv = load_json(INV_FILE, {}).get(uid, [])
    msg = (
        f"🎮 Gacha Bot\n\n"
        f"💰 Coins: {coins}\n"
        f"🎴 Characters: {len(inv)}\n\n"
        "Commands:\n"
        "/summon\n/store\n/inventory\n/ranking\n/balance\n"
    )
    await update.message.reply_text(msg)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    init_user(uid)
    coins = load_json(COIN_FILE, {}).get(uid, 0)
    await update.message.reply_text(f"💰 Coins: {coins}")

# ---------------- SUMMON ----------------
async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    init_user(uid)
    chars = load_json(CHAR_FILE, [])
    if not chars:
        await update.message.reply_text("❌ No characters available.")
        return
    rarity = roll_rarity()
    pool = [c for c in chars if c.get("rarity") == rarity]
    if not pool:
        # fallback to all chars if none match rarity
        pool = chars
    char = random.choice(pool)
    inv = load_json(INV_FILE, {})
    inv.setdefault(uid, []).append(char)
    save_json(INV_FILE, inv)
    await update.message.reply_photo(char.get("file_id"), caption=format_char(char))

# ---------------- STORE (simple random + buy) ----------------
async def send_store(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    chars = load_json(CHAR_FILE, [])
    if not chars:
        await context.bot.send_message(chat_id, "❌ Store empty.")
        return
    char = random.choice(chars)
    kb = [
        [
            InlineKeyboardButton("🛒 Buy", callback_data=f"buy_{char.get('id')}"),
            InlineKeyboardButton("➡ Next", callback_data="store_next")
        ]
    ]
    await context.bot.send_photo(chat_id, char.get("file_id"), caption=format_char(char),
                                 reply_markup=InlineKeyboardMarkup(kb))

async def store_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_store(update.effective_chat.id, context)

async def store_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = str(q.from_user.id)
    init_user(uid)
    if q.data == "store_next":
        await send_store(q.message.chat_id, context)
        return
    if q.data.startswith("buy_"):
        cid = q.data.split("_", 1)[1]
        chars = load_json(CHAR_FILE, [])
        char = next((c for c in chars if str(c.get("id")) == str(cid)), None)
        if not char:
            await q.edit_message_caption("❌ Character not found.")
            return
        coins = load_json(COIN_FILE, {})
        inv = load_json(INV_FILE, {})
        user_coins = coins.get(uid, 0)
        price = int(char.get("price", 0))
        if user_coins < price:
            await q.edit_message_caption("❌ Not enough coins.")
            return
        coins[uid] = user_coins - price
        inv.setdefault(uid, []).append(char)
        save_json(COIN_FILE, coins)
        save_json(INV_FILE, inv)
        await q.edit_message_caption(f"✅ Bought {char.get('name')}")

# ---------------- INVENTORY (group same id + sort rarity + pagination) ----------------
def count_chars(inv_list: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    """
    Returns list of dicts: { 'char': char_obj, 'count': n }
    grouped by character id.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for c in inv_list:
        cid = str(c.get("id"))
        if cid not in groups:
            groups[cid] = {"char": c, "count": 1}
        else:
            groups[cid]["count"] += 1
    return list(groups.values())

def build_inv_pages(counted: List[Dict[str,Any]], page: int) -> Tuple[str, InlineKeyboardMarkup]:
    total_pages = max(1, (len(counted) - 1) // ITEMS_PER_PAGE + 1)
    page = max(1, min(page, total_pages))
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    msg = f"🎴 Inventory (Page {page}/{total_pages})\n\n"
    for idx, entry in enumerate(counted[start:end], start + 1):
        c = entry["char"]
        msg += f"{idx}. {c.get('name','Unknown')} ({c.get('rarity','?')}) x{entry['count']}\n"
    kb = []
    if page > 1:
        kb.append(InlineKeyboardButton("⬅ Previous", callback_data=f"inv_{page-1}"))
    if page < total_pages:
        kb.append(InlineKeyboardButton("➡ Next", callback_data=f"inv_{page+1}"))
    reply = InlineKeyboardMarkup([kb]) if kb else None
    return msg, reply

async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    init_user(uid)
    inv_list = load_json(INV_FILE, {}).get(uid, [])
    if not inv_list:
        await update.message.reply_text("❌ Empty inventory.")
        return
    counted = count_chars(inv_list)
    # sort by rarity desc then name
    counted.sort(key=lambda x: (RARITY_ORDER.get(x["char"].get("rarity"), 0), x["char"].get("name","")), reverse=True)
    msg, reply = build_inv_pages(counted, page=1)
    if reply:
        await update.message.reply_text(msg, reply_markup=reply)
    else:
        await update.message.reply_text(msg)

async def inventory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if not data.startswith("inv_"):
        return
    page = int(data.split("_",1)[1])
    # Determine whose inventory to show: use callback user
    uid = str(q.from_user.id)
    init_user(uid)
    inv_list = load_json(INV_FILE, {}).get(uid, [])
    if not inv_list:
        await q.edit_message_text("❌ Empty inventory.")
        return
    counted = count_chars(inv_list)
    counted.sort(key=lambda x: (RARITY_ORDER.get(x["char"].get("rarity"), 0), x["char"].get("name","")), reverse=True)
    msg, reply = build_inv_pages(counted, page=page)
    # edit the message with new page
    try:
        await q.edit_message_text(msg, reply_markup=reply)
    except:
        # fallback if editing fails (e.g., not text message)
        await context.bot.send_message(q.message.chat_id, msg, reply_markup=reply)

# ---------------- RANKING (with player names) ----------------
async def build_user_name_map(context: ContextTypes.DEFAULT_TYPE, uids: List[str]) -> Dict[str,str]:
    """
    Given list of user ids (strings), attempt to fetch display name via Telegram get_chat.
    Returns mapping uid -> display_name (first_name + optionally last_name or username)
    Errors fall back to uid string.
    """
    mapping: Dict[str,str] = {}
    for uid in uids:
        try:
            chat = await context.bot.get_chat(int(uid))
            # prefer full name then username fallback
            parts = []
            if getattr(chat, "first_name", None):
                parts.append(chat.first_name)
            if getattr(chat, "last_name", None):
                parts.append(chat.last_name)
            if parts:
                mapping[uid] = " ".join(parts)
            elif getattr(chat, "username", None):
                mapping[uid] = f"@{chat.username}"
            else:
                mapping[uid] = str(uid)
        except Exception:
            mapping[uid] = str(uid)
    return mapping

async def ranking_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # send default coin ranking with toggle buttons
    await send_ranking_message(update.effective_chat.id, context, mode="coin")

async def send_ranking_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str="coin"):
    coins = load_json(COIN_FILE, {})
    inv = load_json(INV_FILE, {})
    # gather all users keys (union)
    uids = list(set(list(coins.keys()) + list(inv.keys())))
    # prepare board entries
    board = []
    for u in uids:
        board.append({"uid": u, "coins": int(coins.get(u, 0)), "chars": len(inv.get(u, []))})
    if mode == "coin":
        board.sort(key=lambda x: (x["coins"], x["chars"]), reverse=True)
        title = "💰 Coin Ranking"
    else:
        board.sort(key=lambda x: (x["chars"], x["coins"]), reverse=True)
        title = "🎴 Character Ranking"
    # fetch display names
    name_map = await build_user_name_map(context, uids)
    # build message
    msg = f"🏆 {title}\n\n"
    for i, entry in enumerate(board[:20], start=1):
        name = name_map.get(entry["uid"], entry["uid"])
        if mode == "coin":
            msg += f"{i}. {name} — {entry['coins']} 💰 | {entry['chars']} 🎴\n"
        else:
            msg += f"{i}. {name} — {entry['chars']} 🎴 | {entry['coins']} 💰\n"
    kb = [
        [InlineKeyboardButton("💰 Coins", callback_data="rank_coin"),
         InlineKeyboardButton("🎴 Characters", callback_data="rank_char")]
    ]
    await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb))

async def ranking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "rank_coin":
        await send_ranking_message(q.message.chat_id, context, mode="coin")
    elif q.data == "rank_char":
        await send_ranking_message(q.message.chat_id, context, mode="char")

# ---------------- Admin commands ----------------
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = load_json(ADMINS_FILE, [])
    # allow first caller to become owner/admin if list empty
    if not admins:
        admins.append(uid)
        save_json(ADMINS_FILE, admins)
        await update.message.reply_text("✅ You are set as first admin (owner).")
        return
    if uid not in admins:
        await update.message.reply_text("❌ Only existing admin can add admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /add_admin <user_id>")
        return
    target = str(context.args[0])
    if target in admins:
        await update.message.reply_text("User already admin.")
        return
    admins.append(target)
    save_json(ADMINS_FILE, admins)
    await update.message.reply_text("✅ Admin added.")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = load_json(ADMINS_FILE, [])
    if uid not in admins:
        await update.message.reply_text("❌ Only admin can remove admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove_admin <user_id>")
        return
    target = str(context.args[0])
    if target not in admins:
        await update.message.reply_text("User not in admin.")
        return
    admins.remove(target)
    save_json(ADMINS_FILE, admins)
    await update.message.reply_text("✅ Admin removed.")

# ---------------- Add coins / add char (reply support) ----------------
async def addcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("❌ Only admins can use this.")
        return
    # support reply to user OR /addcoins <user_id> <amount>
    if update.message.reply_to_message and context.args:
        target = str(update.message.reply_to_message.from_user.id)
        try:
            amount = int(context.args[0])
        except:
            await update.message.reply_text("Invalid amount.")
            return
    elif len(context.args) == 2:
        target = str(context.args[0])
        try:
            amount = int(context.args[1])
        except:
            await update.message.reply_text("Invalid amount.")
            return
    else:
        await update.message.reply_text("Usage: reply to a user with /addcoins <amount> OR /addcoins <user_id> <amount>")
        return
    coins = load_json(COIN_FILE, {})
    coins[target] = coins.get(target, 0) + amount
    save_json(COIN_FILE, coins)
    await update.message.reply_text(f"✅ Added {amount} coins to {target}.")

async def addchar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        await update.message.reply_text("❌ Only admins can use this.")
        return
    # reply mode or args: /addchar <char_id>
    if update.message.reply_to_message and context.args:
        target = str(update.message.reply_to_message.from_user.id)
    elif context.args and len(context.args) == 1:
        # allow /addchar <user_id> <char_id> if two args provided, but simplest is reply
        await update.message.reply_text("Please reply to the target user and run: /addchar <char_id>")
        return
    else:
        await update.message.reply_text("Usage: reply to a user with /addchar <char_id>")
        return
    cid = str(context.args[0])
    chars = load_json(CHAR_FILE, [])
    char = next((c for c in chars if str(c.get("id")) == cid), None)
    if not char:
        await update.message.reply_text("❌ Character id not found.")
        return
    inv = load_json(INV_FILE, {})
    inv.setdefault(target, []).append(char)
    save_json(INV_FILE, inv)
    await update.message.reply_text(f"✅ Given {char.get('name')} to user.")

# ---------------- Photo register (admin) ----------------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not is_admin(uid):
        return
    if not update.message.caption:
        await update.message.reply_text("❌ Please include caption with fields: name, rarity, faction, power, price")
        return
    data: Dict[str,str] = {}
    for line in update.message.caption.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip().lower()] = v.strip()
    required = ["name", "rarity", "faction", "power", "price"]
    if not all(k in data for k in required):
        await update.message.reply_text("❌ Caption missing fields. Required: name, rarity, faction, power, price")
        return
    chars = load_json(CHAR_FILE, [])
    try:
        power = int(data["power"])
        price = int(data["price"])
    except:
        await update.message.reply_text("❌ power and price must be integers.")
        return
    new_char = {
        "id": (chars[-1].get("id") + 1) if chars else 1,
        "name": data["name"],
        "rarity": data["rarity"],
        "faction": data["faction"],
        "power": power,
        "price": price,
        "file_id": update.message.photo[-1].file_id
    }
    chars.append(new_char)
    save_json(CHAR_FILE, chars)
    await update.message.reply_text(f"✅ Character saved: {new_char['name']} (id: {new_char['id']})")

# ---------------- Main ----------------
def main():
    # ensure DB files exist with correct defaults
    ensure_file(CHAR_FILE, [])
    ensure_file(INV_FILE, {})
    ensure_file(COIN_FILE, {})
    ensure_file(ADMINS_FILE, [])

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("summon", summon))
    app.add_handler(CommandHandler("store", store_command))
    app.add_handler(CommandHandler("inventory", inventory_command))
    app.add_handler(CommandHandler("ranking", ranking_command))

    # admin
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("addcoins", addcoins_command))
    app.add_handler(CommandHandler("addchar", addchar_command))

    # photo register (admin)
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # callback handlers (patterned)
    app.add_handler(CallbackQueryHandler(store_callback, pattern="^store_|^buy_"))
    app.add_handler(CallbackQueryHandler(inventory_callback, pattern="^inv_"))
    app.add_handler(CallbackQueryHandler(ranking_callback, pattern="^rank_"))

    print("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
