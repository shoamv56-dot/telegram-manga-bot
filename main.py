from __future__ import annotations

import html
import logging
import os
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from scraper import MangaResult, ChapterResult, download_images_to_pdf, get_chapter_images, get_chapters, search_manga

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"]) if os.environ.get("ADMIN_USER_ID") else None
SCRAPER_SOURCE = os.environ.get("SCRAPER_SOURCE", "azora")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def is_admin(update: Update) -> bool:
    return ADMIN_USER_ID is None or bool(update.effective_user and update.effective_user.id == ADMIN_USER_ID)


def result_text(item: MangaResult) -> str:
    return f"📚 <b>{html.escape(item.title)}</b>\n\n{html.escape(item.url)}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📚 <b>Manga Bot</b>\n\nأرسل:\n<code>/search اسم المانجا</code>\n\nسيتم البحث مباشرة في موقع المانجا ثم عرض الفصول.\nالمصدر الحالي: "
        + html.escape(SCRAPER_SOURCE),
        parse_mode=ParseMode.HTML,
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("⛔ هذا الأمر متاح للأدمن فقط.")
        return
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("استخدم: /search اسم المانجا")
        return
    status = await update.message.reply_text("🔎 جاري البحث في الموقع...")
    try:
        results = await search_manga(query, SCRAPER_SOURCE)
        if not results:
            await status.edit_text("لم أجد نتائج. جرّب اسماً آخر.")
            return
        buttons = [[InlineKeyboardButton(item.title[:60], callback_data=f"manga:{i}")] for i, item in enumerate(results)]
        context.user_data["search_results"] = {str(i): item.__dict__ for i, item in enumerate(results)}
        await status.edit_text("اختر المانجا:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        logger.exception("Scraper search failed")
        await status.edit_text("تعذر الوصول للموقع حالياً. تأكد من أن الموقع متاح.")


async def manga_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update):
        await query.edit_message_text("⛔ هذا الأمر متاح للأدمن فقط.")
        return
    item = context.user_data.get("search_results", {}).get(query.data.split(":", 1)[1])
    if not item:
        await query.edit_message_text("انتهت صلاحية النتيجة. أعد البحث.")
        return
    await query.edit_message_text("📚 جاري سحب الفصول من الموقع...")
    try:
        chapters = await get_chapters(item["url"], SCRAPER_SOURCE)
        if not chapters:
            await query.edit_message_text("لم أجد فصولاً في صفحة المانجا.")
            return
        context.user_data["chapters"] = {str(i): chapter.__dict__ for i, chapter in enumerate(chapters[:30])}
        buttons = [[InlineKeyboardButton(f"الفصل {c.number}" + (f" — {c.title[:25]}" if c.title else ""), callback_data=f"chapter:{i}")] for i, c in enumerate(chapters[:30])]
        await query.edit_message_text(f"<b>{html.escape(item['title'])}</b>\n\nاختر الفصل:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        logger.exception("Chapter listing failed")
        await query.edit_message_text("تعذر سحب الفصول من الموقع حالياً.")


async def chapter_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update):
        await query.edit_message_text("⛔ هذا الأمر متاح للأدمن فقط.")
        return
    item = context.user_data.get("chapters", {}).get(query.data.split(":", 1)[1])
    if not item:
        await query.edit_message_text("انتهت صلاحية الفصل. أعد البحث.")
        return
    await query.edit_message_text("⏳ جاري سحب الفصل وتحميل الصور من الموقع...", parse_mode=ParseMode.HTML)
    temp_path = None
    try:
        image_urls = await get_chapter_images(item["url"], SCRAPER_SOURCE)
        if not image_urls:
            raise RuntimeError("No images")
        with tempfile.NamedTemporaryFile(prefix="manga_", suffix=".pdf", delete=False) as tmp:
            temp_path = Path(tmp.name)
        count = await download_images_to_pdf(image_urls, str(temp_path))
        if temp_path.stat().st_size > 49 * 1024 * 1024:
            raise RuntimeError("PDF too large for Telegram")
        await query.message.reply_document(
            document=str(temp_path),
            caption=f"📖 الفصل {html.escape(item['number'])}\nعدد الصفحات: {count}",
            parse_mode=ParseMode.HTML,
        )
        await query.edit_message_text("✅ تم سحب الفصل وإرساله كملف PDF.")
    except Exception:
        logger.exception("Chapter PDF failed")
        await query.edit_message_text("❌ تعذر إنشاء PDF للفصل. قد تكون صور الموقع محمية أو غير متاحة حالياً.")
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(manga_selected, pattern=r"^manga:"))
    app.add_handler(CallbackQueryHandler(chapter_selected, pattern=r"^chapter:"))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
