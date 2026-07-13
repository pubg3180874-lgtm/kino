"""
Kino qidiruv boti
------------------
- Admin (faqat siz) botga video yuborsangiz, bot o'sha kino uchun
  raqam (kod) so'raydi. Raqamni yuborsangiz, kino shu raqam bilan saqlanadi.
- Oddiy foydalanuvchilar "🔍 Kino qidirish" tugmasini bosib, kino raqamini
  yozganda, o'sha kino avtomatik ravishda ularga yuboriladi.

O'rnatish:
    pip install -r requirements.txt

Sozlash:
    Shu papkadagi ".env" faylini oching va BOT_TOKEN, ADMIN_ID, CHANNEL_USERNAME
    qiymatlarini yozing (bot.py faylini ochish shart emas).

Ishga tushirish:
    python bot.py
"""

import os
import sqlite3
import logging

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============== SOZLAMALAR (.env faylidan o'qiladi) ==============
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
_admin_id_raw = os.getenv("ADMIN_ID")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")  # masalan: @mening_kanalim
DB_PATH = "movies.db"

if not BOT_TOKEN or not _admin_id_raw:
    raise SystemExit(
        "Xatolik: .env faylida BOT_TOKEN va ADMIN_ID to'ldirilmagan. "
        "QOLLANMA.md ga qarang."
    )

ADMIN_ID = int(_admin_id_raw)
# ===================================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------- Baza bilan ishlash ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS movies (
            code TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_type TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def save_movie(code: str, file_id: str, file_type: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO movies (code, file_id, file_type) VALUES (?, ?, ?)",
        (code, file_id, file_type),
    )
    conn.commit()
    conn.close()


def get_movie(code: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT file_id, file_type FROM movies WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    return row  # (file_id, file_type) yoki None


# ---------- Holatlar (har bir user uchun vaqtinchalik yodda saqlash) ----------
# context.user_data ichida quyidagi kalitlardan foydalanamiz:
#   "waiting_for_code_admin" -> admin video yubordi, endi kod kutilmoqda
#   "pending_file_id", "pending_file_type" -> shu videoning ma'lumotlari
#   "waiting_for_search" -> oddiy user kod kiritishini kutyapmiz


MAIN_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔍 Kino qidirish", callback_data="search_movie")]]
)


def get_subscribe_keyboard():
    channel_link = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("1- Kanalga obuna bo'lish", url=channel_link)],
            [InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")],
        ]
    )


async def is_subscribed(bot, user_id: int) -> bool:
    """CHANNEL_USERNAME sozlanmagan bo'lsa, tekshiruv o'tkazilmaydi (True qaytadi)."""
    if not CHANNEL_USERNAME:
        return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except TelegramError as e:
        logger.warning("Obunani tekshirishda xatolik: %s", e)
        # Bot kanalga admin qilib qo'shilmagan bo'lishi mumkin — shunday holatda
        # foydalanuvchini bloklab qo'ymaslik uchun False qaytaramiz va log yozamiz.
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await is_subscribed(context.bot, user_id):
        await update.message.reply_text(
            "Assalomu alekum, Kino korish uchun kodni yuboring",
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        await update.message.reply_text(
            "Botdan foydalanish uchun avval kanalimizga obuna bo'ling, "
            "so'ng \"✅ Tekshirish\" tugmasini bosing.",
            reply_markup=get_subscribe_keyboard(),
        )


async def check_sub_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    if await is_subscribed(context.bot, user_id):
        await query.answer("Obuna tasdiqlandi ✅")
        await query.message.edit_text(
            "Rahmat! Endi kino qidirish uchun pastdagi tugmani bosing 👇",
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        await query.answer("Siz hali obuna bo'lmagansiz ❌", show_alert=True)


async def search_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    if not await is_subscribed(context.bot, user_id):
        await query.answer()
        await query.message.reply_text(
            "Botdan foydalanish uchun avval kanalimizga obuna bo'ling, "
            "so'ng \"✅ Tekshirish\" tugmasini bosing.",
            reply_markup=get_subscribe_keyboard(),
        )
        return

    await query.answer()
    context.user_data["waiting_for_search"] = True
    await query.message.reply_text("Kino raqamini kiriting:")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faqat admin yuborgan video shu yerda ushlanadi."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return  # oddiy userlar video yubora olmaydi (e'tiborga olinmaydi)

    if update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    else:
        return

    context.user_data["pending_file_id"] = file_id
    context.user_data["pending_file_type"] = file_type
    context.user_data["waiting_for_code_admin"] = True

    await update.message.reply_text(
        "Kino qabul qilindi ✅\nEndi shu kino uchun RAQAM (kod) yuboring:"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    # 1) Admin kino uchun kod kiritayotgan bo'lsa
    if user_id == ADMIN_ID and context.user_data.get("waiting_for_code_admin"):
        code = text
        file_id = context.user_data.pop("pending_file_id", None)
        file_type = context.user_data.pop("pending_file_type", None)
        context.user_data["waiting_for_code_admin"] = False

        if not file_id:
            await update.message.reply_text("Xatolik: avval kino yuboring.")
            return

        save_movie(code, file_id, file_type)
        await update.message.reply_text(
            f"Saqlandi ✅\nKino raqami: {code}"
        )
        return

    # 2) Oddiy user kino raqamini qidirayotgan bo'lsa
    if context.user_data.get("waiting_for_search"):
        code = text
        context.user_data["waiting_for_search"] = False

        row = get_movie(code)
        if row is None:
            await update.message.reply_text(
                "❌ Bunday raqamli kino topilmadi.\nQaytadan urinish uchun /start bosing.",
                reply_markup=MAIN_KEYBOARD,
            )
            return

        file_id, file_type = row
        if file_type == "video":
            await update.message.reply_video(file_id, caption=f"🎬 Kino raqami: {code}")
        else:
            await update.message.reply_document(file_id, caption=f"🎬 Kino raqami: {code}")

        await update.message.reply_text(
            "Yana kino qidirish uchun tugmani bosing 👇",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # 3) Aks holda oddiy javob
    await update.message.reply_text(
        "Kino qidirish uchun pastdagi tugmani bosing 👇",
        reply_markup=MAIN_KEYBOARD,
    )


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_sub_button, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(search_button, pattern="^search_movie$"))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
