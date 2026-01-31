import os, random, sqlite3, logging, time, json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 1812962224  # <-- Put your Telegram ID here
DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/gacha.db"
START_COINS = 100
SUMMON_COST = 20
TEN_SUMMON_COST = 180
DAILY_REWARD = 50
INV_PAGE = 10

RARITY_RATE = {"Common": 60, "Rare":25, "Epic":10, "Legendary":5}
RARITY_ORDER = {"Legendary":4,"Epic":3,"Rare":2,"Common":1}

if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)

# ================= LOGGER =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
logger = logging.getLogger()

# ================= DATABASE =================
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, coins INTEGER, lvl INTEGER, exp INTEGER, last_daily INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS characters (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, rarity TEXT, faction TEXT, power INTEGER, price INTEGER, file_id TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS inventory (user_id INTEGER, char_id INTEGER, count INTEGER, FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(char_id) REFERENCES characters(id))''')
c.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
conn.commit()

# ================= HELPERS =================
def is_owner(uid): return uid==OWNER_ID
def is_admin(uid): c.execute("SELECT 1 FROM admins WHERE user_id=?",(uid,)); return is_owner(uid) or c.fetchone() is not None
def init_user(uid): c.execute("SELECT * FROM users WHERE id=?",(uid,)); 
if not c.fetchone(): c.execute("INSERT INTO users (id,coins,lvl,exp,last_daily) VALUES (?,?,?,?,?)",(uid,START_COINS,1,0,0)); conn.commit()
def roll_rarity(): r=random.randint(1,100); t=0; 
for k,v in RARITY_RATE.items(): t+=v; 
if r<=t: return k
return "Common"
def format_char(c): return f"🆔 {c['id']}\n🔥 {c['name']}\n⭐ {c['rarity']}\n🏰 {c['faction']}\n⚔️ {c['power']}\n💰 {c['price']} coins"
def add_exp(uid,amt): c.execute("SELECT lvl,exp FROM users WHERE id=?",(uid,)); lvl,exp=c.fetchone(); exp+=amt
while exp>=lvl*100: exp-=lvl*100; lvl+=1
c.execute("UPDATE users SET lvl=?, exp=? WHERE id=?",(lvl,exp,uid)); conn.commit()

# ================= MIGRATION FROM character.json =================
def migrate_characters():
    if os.path.exists("characters.json"):
        with open("characters.json","r") as f:
            chars=json.load(f)
        for char in chars:
            c.execute("INSERT OR IGNORE INTO characters (id,name,rarity,faction,power,price,file_id) VALUES (?,?,?,?,?,?,?)",
                      (char["id"],char["name"],char["rarity"],char["faction"],int(char["power"]),int(char["price"]),char["file_id"]))
        conn.commit()
        print("✅ character.json migrated to DB")

# ================= PHOTO UPLOAD HANDLER =================
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    if not update.message.caption: return
    
    data = {}
    for line in update.message.caption.split("\n"):
        if ":" not in line: continue
        k,v = line.split(":",1)
        data[k.strip().lower()]=v.strip()
    
    need=["name","rarity","faction","power","price"]
    if not all(k in data for k in need):
        await update.message.reply_text("❌ Caption format wrong")
        return
    
    c.execute("INSERT INTO characters (name,rarity,faction,power,price,file_id) VALUES (?,?,?,?,?,?)",
              (data["name"],data["rarity"],data["faction"],int(data["power"]),int(data["price"]),update.message.photo[-1].file_id))
    conn.commit()
    await update.message.reply_text("✅ Character saved to DB")

# ================= SUMMON =================
async def summon(update:Update, context:ContextTypes.DEFAULT_TYPE): await _summon(update,1)
async def summon10(update:Update, context:ContextTypes.DEFAULT_TYPE): await _summon(update,10)
async def _summon(update,times):
    uid=update.effective_user.id; init_user(uid)
    cost=SUMMON_COST if times==1 else TEN_SUMMON_COST
    c.execute("SELECT coins FROM users WHERE id=?",(uid,)); coins=c.fetchone()[0]
    if coins<cost: await update.message.reply_text("❌ Not enough coins"); return
    c.execute("UPDATE users SET coins=coins-? WHERE id=?",(cost,uid))
    c.execute("SELECT * FROM characters"); chars=c.fetchall()
    if not chars: await update.message.reply_text("❌ No characters"); return
    msg="🎉 Summon Result\n\n" if times>1 else ""
    for _ in range(times):
        rarity=roll_rarity(); pool=[ch for ch in chars if ch[2]==rarity]; pool=pool or chars
        char=random.choice(pool)
        c.execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?",(uid,char[0])); res=c.fetchone()
        if res: c.execute("UPDATE inventory SET count=count+1 WHERE user_id=? AND char_id=?",(uid,char[0]))
        else: c.execute("INSERT INTO inventory (user_id,char_id,count) VALUES (?,?,?)",(uid,char[0],1))
        add_exp(uid,10)
        if times>1: msg+=f"⭐ {char[1]} ({char[2]})\n"
        else: await update.message.reply_photo(char[6], caption=format_char({'id':char[0],'name':char[1],'rarity':char[2],'faction':char[3],'power':char[4],'price':char[5]}))
    if times>1: await update.message.reply_text(msg)
    conn.commit()

# ================= STORE =================
async def store(update:Update, context:ContextTypes.DEFAULT_TYPE): await send_store(update.effective_chat.id,context)
async def send_store(chat_id,context):
    c.execute("SELECT * FROM characters"); chars=c.fetchall()
    if not chars: await context.bot.send_message(chat_id,"❌ Store empty"); return
    char=random.choice(chars)
    keyboard=[[InlineKeyboardButton("🛒 Buy",callback_data=f"buy_{char[0]}"),InlineKeyboardButton("➡ Next",callback_data="next_store")]]
    await context.bot.send_photo(chat_id,char[6],caption=format_char({'id':char[0],'name':char[1],'rarity':char[2],'faction':char[3],'power':char[4],'price':char[5]}),reply_markup=InlineKeyboardMarkup(keyboard))

async def store_btn(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id; init_user(uid)
    if q.data=="next_store": await send_store(q.message.chat_id,context); return
    if q.data.startswith("buy_"):
        cid=int(q.data.split("_")[1])
        c.execute("SELECT * FROM characters WHERE id=?",(cid,)); char=c.fetchone()
        if not char: await q.edit_message_caption("❌ Not found"); return
        c.execute("SELECT coins FROM users WHERE id=?",(uid,)); coins=c.fetchone()[0]
        if coins<char[5]: await q.edit_message_caption("❌ Not enough coins"); return
        c.execute("UPDATE users SET coins=coins-? WHERE id=?",(char[5],uid))
        c.execute("SELECT count FROM inventory WHERE user_id=? AND char_id=?",(uid,cid)); res=c.fetchone()
        if res: c.execute("UPDATE inventory SET count=count+1 WHERE user_id=? AND char_id=?",(uid,cid))
        else: c.execute("INSERT INTO inventory (user_id,char_id,count) VALUES (?,?,?)",(uid,cid,1))
        conn.commit(); await q.edit_message_caption(f"✅ Bought {char[1]}")

# ================= INVENTORY PAGINATION =================
async def inventory(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; init_user(uid)
    c.execute("SELECT char_id,count FROM inventory WHERE user_id=?",(uid,)); items=c.fetchall()
    if not items: await update.message.reply_text("❌ Empty inventory"); return
    pages=[items[i:i+INV_PAGE] for i in range(0,len(items),INV_PAGE)]
    await show_inv_page(update,update.effective_chat.id,pages,0)

async def show_inv_page(update,chat_id,pages,page):
    msg="🎴 Inventory (Page {}/{})\n\n".format(page+1,len(pages))
    for i,(cid,count) in enumerate(pages[page],1):
        c.execute("SELECT * FROM characters WHERE id=?",(cid,)); ch=c.fetchone()
        msg+=f"{i}. {ch[1]} ({ch[2]}) x{count}\n"
    keyboard=[]
    if page>0: keyboard.append(InlineKeyboardButton("⬅ Prev",callback_data=f"inv_{page-1}"))
    if page<len(pages)-1: keyboard.append(InlineKeyboardButton("Next ➡",callback_data=f"inv_{page+1}"))
    reply_markup=InlineKeyboardMarkup([keyboard]) if keyboard else None
    await update.message.reply_text(msg,reply_markup=reply_markup)

async def inv_btn(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    page=int(q.data.split("_")[1])
    uid=q.from_user.id
    c.execute("SELECT char_id,count FROM inventory WHERE user_id=?",(uid,)); items=c.fetchall()
    pages=[items[i:i+INV_PAGE] for i in range(0,len(items),INV_PAGE)]
    await show_inv_page(update,q.message.chat_id,pages,page)

# ================= ADMIN =================
async def addcoins(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid) or not update.message.reply_to_message or not context.args: return
    target=update.message.reply_to_message.from_user.id
    try: amount=int(context.args[0])
    except: return
    init_user(target)
    c.execute("UPDATE users SET coins=coins+? WHERE id=?",(amount,target)); conn.commit()
    await update.message.reply_text("✅ Coins added.")

async def addadmin(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_owner(uid) or not update.message.reply_to_message: return
    target=update.message.reply_to_message.from_user.id
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)",(target,)); conn.commit()
    await update.message.reply_text("✅ Admin added.")

async def deladmin(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_owner(uid) or not update.message.reply_to_message: return
    target=update.message.reply_to_message.from_user.id
    c.execute("DELETE FROM admins WHERE user_id=?",(target,)); conn.commit()
    await update.message.reply_text("✅ Admin removed.")

async def admins(update:Update,context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_owner(uid): return
    c.execute("SELECT user_id FROM admins"); lst=c.fetchall()
    msg="👑 Admin List\n\n"; msg+="\n".join(str(u[0]) for u in lst)
    await update.message.reply_text(msg)

# ================= MAIN =================
def main():
    migrate_characters()  # migrate old character.json
    if not BOT_TOKEN: raise Exception("BOT_TOKEN missing")
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    # commands
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("balance",balance))
    app.add_handler(CommandHandler("daily",lambda u,c: daily(u,c)))
    app.add_handler(CommandHandler("summon",summon))
    app.add_handler(CommandHandler("summon10",summon10))
    app.add_handler(CommandHandler("inventory",inventory))
    app.add_handler(CallbackQueryHandler(inv_btn,pattern=r"inv_\d+"))
    app.add_handler(CommandHandler("store",store))
    app.add_handler(CallbackQueryHandler(store_btn,pattern=r"(buy_\d+|next_store)"))
    app.add_handler(MessageHandler(filters.PHOTO,photo_handler))
    app.add_handler(CommandHandler("addcoins",addcoins))
    app.add_handler(CommandHandler("addadmin",addadmin))
    app.add_handler(CommandHandler("deladmin",deladmin))
    app.add_handler(CommandHandler("admins",admins))
    print("Bot running...")
    app.run_polling()

if __name__=="__main__":
    main()
