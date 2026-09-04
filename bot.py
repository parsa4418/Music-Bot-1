import asyncio
import logging
import sqlite3
import secrets
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== تنظیمات ==================
BOT_TOKEN = "8295625829:AAFhOF5mb9Qwtf1Eja8X7fPHyEiTlK3bip8"   # توکن ربات رو از @BotFather بگیر و اینجا بذار
ADMIN_ID = 8904869158                    # آیدی عددی ادمین
DB_PATH = "bot.db"
# ===============================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# در حافظه نگه می‌داریم که ادمین در چه مرحله‌ای از وارد کردن اطلاعات هست
admin_state: dict[int, str] = {}


# ================== دیتابیس ==================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        """CREATE TABLE IF NOT EXISTS songs (
            code TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_type TEXT NOT NULL,
            added_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS pending (
            user_id INTEGER PRIMARY KEY,
            code TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


def set_setting(key, value):
    conn = db()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_setting(key):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def delete_setting_prefix(prefix):
    conn = db()
    conn.execute("DELETE FROM settings WHERE key LIKE ?", (prefix + "%",))
    conn.commit()
    conn.close()


def save_song(code, file_id, file_type):
    conn = db()
    conn.execute(
        "INSERT INTO songs(code, file_id, file_type, added_at) VALUES (?, ?, ?, ?)",
        (code, file_id, file_type, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_song(code):
    conn = db()
    row = conn.execute("SELECT * FROM songs WHERE code=?", (code,)).fetchone()
    conn.close()
    return row


def set_pending(user_id, code):
    conn = db()
    conn.execute(
        "INSERT INTO pending(user_id, code) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET code=excluded.code",
        (user_id, code),
    )
    conn.commit()
    conn.close()


def get_pending(user_id):
    conn = db()
    row = conn.execute("SELECT code FROM pending WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["code"] if row else None


def clear_pending(user_id):
    conn = db()
    conn.execute("DELETE FROM pending WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


# ================== کیبوردها ==================
def admin_menu_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎵 ثبت آهنگ", callback_data="admin_add_song")],
            [InlineKeyboardButton("📌 تنظیم جوین اجباری", callback_data="admin_set_join")],
            [InlineKeyboardButton("📋 مشاهده تنظیمات فعلی", callback_data="admin_view_join")],
            [InlineKeyboardButton("🗑 حذف همه‌ی جوین اجباری‌ها", callback_data="admin_clear_join")],
        ]
    )


def join_submenu_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("1️⃣ تنظیم کانال", callback_data="set_channel")],
            [InlineKeyboardButton("2️⃣ تنظیم گروه", callback_data="set_group")],
            [InlineKeyboardButton("3️⃣ تنظیم ربات", callback_data="set_bot")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
        ]
    )


def get_all_join_buttons():
    buttons = []
    channel_link = get_setting("channel_link")
    if channel_link:
        buttons.append([InlineKeyboardButton("📢 عضویت در کانال", url=channel_link)])
    group_link = get_setting("group_link")
    if group_link:
        buttons.append([InlineKeyboardButton("👥 عضویت در گروه", url=group_link)])
    bot_link = get_setting("bot_link")
    if bot_link:
        buttons.append([InlineKeyboardButton("🤖 استارت ربات", url=bot_link)])
    return buttons


def build_join_status_text():
    lines = ["📋 تنظیمات فعلی جوین اجباری:"]
    lines.append(f"کانال: {get_setting('channel_id') or '❌ تنظیم نشده'}")
    lines.append(f"گروه: {get_setting('group_id') or '❌ تنظیم نشده'}")
    lines.append(f"ربات: {get_setting('bot_link') or '❌ تنظیم نشده'}")
    return "\n".join(lines)


# ================== منطق جوین اجباری ==================
async def is_member(context: ContextTypes.DEFAULT_TYPE, chat_id_str: str, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id_str, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("خطا در بررسی عضویت %s برای کاربر %s: %s", chat_id_str, user_id, e)
        return False


async def has_missing_join(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """فقط کانال و گروه چک میشن. ربات چون قابل چک شدن نیست، بلاک‌کننده نیست."""
    channel_id = get_setting("channel_id")
    if channel_id and not await is_member(context, channel_id, user_id):
        return True

    group_id = get_setting("group_id")
    if group_id and not await is_member(context, group_id, user_id):
        return True

    return False


async def ask_join(chat_id, context: ContextTypes.DEFAULT_TYPE):
    kb_rows = get_all_join_buttons()
    kb_rows.append([InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")])
    await context.bot.send_message(
        chat_id,
        "برای دریافت آهنگ، ابتدا در موارد زیر عضو شوید و بعد روی «عضو شدم» بزنید:",
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )


async def send_song(chat_id, context: ContextTypes.DEFAULT_TYPE, song):
    if song["file_type"] == "audio":
        await context.bot.send_audio(chat_id, song["file_id"])
    else:
        await context.bot.send_document(chat_id, song["file_id"])


async def deliver_or_ask_join(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    user = update.effective_user
    chat_id = update.effective_chat.id

    song = get_song(code)
    if not song:
        await update.effective_message.reply_text("این لینک معتبر نیست یا آهنگ حذف شده.")
        return

    if user.id == ADMIN_ID:
        await send_song(chat_id, context, song)
        return

    if await has_missing_join(context, user.id):
        set_pending(user.id, code)
        await ask_join(chat_id, context)
    else:
        await send_song(chat_id, context, song)


# ================== هندلرها ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if args:
        code = args[0]
        await deliver_or_ask_join(update, context, code)
        return

    if user.id == ADMIN_ID:
        await update.message.reply_text("پنل مدیریت 👇", reply_markup=admin_menu_kb())
    else:
        await update.message.reply_text("سلام! برای دریافت آهنگ، از لینکی که در اختیارتون قرار گرفته استفاده کنید.")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    await update.message.reply_text("پنل مدیریت 👇", reply_markup=admin_menu_kb())


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    if await has_missing_join(context, user.id):
        await query.answer("هنوز عضو نشدید! لطفا ابتدا عضو بشید.", show_alert=True)
        return

    code = get_pending(user.id)
    if not code:
        await query.answer("لینکی برای شما ثبت نشده.", show_alert=True)
        return

    song = get_song(code)
    if not song:
        await query.answer("آهنگ پیدا نشد.", show_alert=True)
        return

    await query.answer("عضویت تایید شد ✅")
    await send_song(query.message.chat_id, context, song)
    clear_pending(user.id)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if user.id != ADMIN_ID:
        await query.answer("شما دسترسی ندارید.", show_alert=True)
        return

    data = query.data
    await query.answer()

    if data == "admin_add_song":
        admin_state[user.id] = "awaiting_song"
        await query.message.reply_text("لطفا فایل آهنگ رو ارسال کنید (Audio یا فایل).")

    elif data == "admin_set_join":
        await query.message.reply_text("کدوم مورد رو تنظیم می‌کنید؟", reply_markup=join_submenu_kb())

    elif data == "set_channel":
        admin_state[user.id] = "awaiting_channel_id"
        await query.message.reply_text(
            "آیدی عددی یا یوزرنیم کانال رو بفرست (ربات باید ادمین کانال باشه).\n"
            "مثال: @mychannel یا -1001234567890"
        )

    elif data == "set_group":
        admin_state[user.id] = "awaiting_group_id"
        await query.message.reply_text(
            "آیدی عددی یا یوزرنیم گروه رو بفرست (ربات باید ادمین گروه باشه).\n"
            "مثال: @mygroup یا -1001234567890"
        )

    elif data == "set_bot":
        admin_state[user.id] = "awaiting_bot_link"
        await query.message.reply_text(
            "لینک استارت ربات مورد نظر رو بفرست.\nمثال: https://t.me/somebot?start=xxx"
        )

    elif data == "admin_view_join":
        await query.message.reply_text(build_join_status_text())

    elif data == "admin_clear_join":
        delete_setting_prefix("channel_")
        delete_setting_prefix("group_")
        delete_setting_prefix("bot_")
        await query.message.reply_text("تمام تنظیمات جوین اجباری حذف شد.")

    elif data == "admin_back":
        await query.message.reply_text("پنل مدیریت 👇", reply_markup=admin_menu_kb())


async def admin_song_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID or admin_state.get(user.id) != "awaiting_song":
        return

    message = update.message
    file_id = None
    file_type = None

    if message.audio:
        file_id = message.audio.file_id
        file_type = "audio"
    elif message.voice:
        file_id = message.voice.file_id
        file_type = "document"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"

    if not file_id:
        await message.reply_text("لطفا فایل آهنگ رو به صورت Audio یا فایل ارسال کنید.")
        return

    # کد یکتا و همیشگی برای این آهنگ (منقضی نمیشه)
    code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
    save_song(code, file_id, file_type)
    admin_state.pop(user.id, None)

    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={code}"
    await message.reply_text(
        f"✅ آهنگ ثبت شد.\nاین لینک آهنگ شماست:\n{link}\n\n"
        "هرکی با این لینک بیاد همین آهنگ رو دریافت می‌کنه و لینک هیچ‌وقت منقضی نمیشه.",
        reply_markup=admin_menu_kb(),
    )


async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    state = admin_state.get(user.id)
    if not state:
        return

    text = (update.message.text or "").strip()

    if state == "awaiting_channel_id":
        set_setting("channel_id", text)
        admin_state[user.id] = "awaiting_channel_link"
        await update.message.reply_text("حالا لینک عضویت کانال رو بفرست (مثلا https://t.me/mychannel):")

    elif state == "awaiting_channel_link":
        set_setting("channel_link", text)
        admin_state.pop(user.id, None)
        await update.message.reply_text("✅ کانال با موفقیت تنظیم شد.", reply_markup=admin_menu_kb())

    elif state == "awaiting_group_id":
        set_setting("group_id", text)
        admin_state[user.id] = "awaiting_group_link"
        await update.message.reply_text("حالا لینک عضویت گروه رو بفرست:")

    elif state == "awaiting_group_link":
        set_setting("group_link", text)
        admin_state.pop(user.id, None)
        await update.message.reply_text("✅ گروه با موفقیت تنظیم شد.", reply_markup=admin_menu_kb())

    elif state == "awaiting_bot_link":
        set_setting("bot_link", text)
        admin_state.pop(user.id, None)
        await update.message.reply_text("✅ ربات با موفقیت تنظیم شد.", reply_markup=admin_menu_kb())


def main():
    # پایتون 3.14 دیگه خودش event loop نمی‌سازه، پس دستی می‌سازیمش
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|set_)"))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.ALL, admin_song_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler))

    logger.info("ربات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
