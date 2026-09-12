from __future__ import annotations

import html
import logging
import os
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from scraper import ChapterResult, MangaResult, download_chapter_as_pdf, get_chapters, search_manga

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID_RAW = os.getenv("ADMIN_CHAT_ID")

if ADMIN_CHAT_ID_RAW:
    try:
        ADMIN_CHAT_ID = int(ADMIN_CHAT_ID_RAW)
    except ValueError as exc:
        raise RuntimeError("ADMIN_CHAT_ID must be a numeric Telegram user/chat ID") from exc
else:
    ADMIN_CHAT_ID = None

MAX_RESULTS = 10
MAX_CHAPTERS = 30
CALLBACK_TTL_TEXT = "انتهت صلاحية هذا الزر. أعد البحث عن المانجا."

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegram-manga-bot")


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and ADMIN_CHAT_ID is not None and user.id == ADMIN_CHAT_ID)


async def admin_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global first-stage guard: reject every non-admin update immediately."""
    if is_admin(update):
        return
    text = "عذراً، هذا البوت خاص بالمشرف فقط."
    try:
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text(text)
    finally:
        raise ApplicationHandlerStop


def result_keyboard(results: list[MangaResult]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{index}. {item.title[:45]}", callback_data=f"manga:{index}")]
        for index, item in enumerate(results)
    ])


def chapter_keyboard(chapters: list[ChapterResult]) -> InlineKeyboardMarkup:
    rows = []
    for index, chapter in enumerate(chapters):
        label = chapter.number
        if chapter.title:
            label += f" — {chapter.title[:30]}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"chapter:{index}")])
    rows.append([InlineKeyboardButton("🔎 بحث جديد", callback_data="new_search")])
    return InlineKeyboardMarkup(rows)


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            await admin_guard(update, context)
            return
        return await func(update, context)
    return wrapper


@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "📚 أهلاً بك في Manga Bot الخاص بك.\n\n"
        "أرسل اسم المانجا/المانهوا مباشرة، أو استخدم:\n"
        "/search اسم المانجا\n\n"
        "سأبحث في MangaDex وAzora وMangaSwat، ثم أعرض الفصول المتاحة "
        "وأحوّل الفصل المختار إلى PDF مؤقتاً وأرسله لك."
    )


@admin_only
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await perform_search(update, context, " ".join(context.args).strip())


@admin_only
async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_message.text:
        await perform_search(update, context, update.effective_message.text.strip())


async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    message = update.effective_message
    if not message:
        return
    if not query:
        await message.reply_text("اكتب اسم المانجا بعد /search أو أرسل الاسم مباشرة.")
        return

    status = await message.reply_text("🔎 جاري البحث في المصادر...")
    try:
        results = await search_manga(query, limit=MAX_RESULTS)
    except Exception:
        logger.exception("Search failed for %r", query)
        await status.edit_text("❌ حدث خطأ أثناء البحث. جرّب اسماً آخر.")
        return

    if not results:
        await status.edit_text("لم أجد نتائج. جرّب الاسم الإنجليزي أو العربي للمانجا.")
        return

    context.user_data["search_results"] = results
    await status.edit_text(
        f"📚 نتائج البحث عن: <b>{html.escape(query)}</b>\nاختر المانجا:",
        parse_mode=ParseMode.HTML,
        reply_markup=result_keyboard(results),
    )


@admin_only
async def manga_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.split(":", 1)[1])
        item: MangaResult = context.user_data["search_results"][index]
    except (KeyError, IndexError, ValueError, TypeError):
        await query.edit_message_text(CALLBACK_TTL_TEXT)
        return

    await query.edit_message_text("📖 جاري جلب الفصول...")
    try:
        chapters = await get_chapters(item.url, source=item.source)
    except Exception:
        logger.exception("Chapter listing failed for %s", item.url)
        await query.edit_message_text("❌ تعذر جلب الفصول من المصدر حالياً.")
        return

    if not chapters:
        await query.edit_message_text(
            f"❌ لم أجد فصولاً متاحة لـ <b>{html.escape(item.title)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    chapters = chapters[:MAX_CHAPTERS]
    context.user_data["selected_manga"] = item
    context.user_data["chapters"] = chapters
    await query.edit_message_text(
        f"📚 <b>{html.escape(item.title)}</b>\n"
        f"المصدر: {html.escape(item.source)}\n\nاختر الفصل:",
        parse_mode=ParseMode.HTML,
        reply_markup=chapter_keyboard(chapters),
    )


@admin_only
async def chapter_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.split(":", 1)[1])
        chapter: ChapterResult = context.user_data["chapters"][index]
        manga: MangaResult = context.user_data["selected_manga"]
    except (KeyError, IndexError, ValueError, TypeError):
        await query.edit_message_text(CALLBACK_TTL_TEXT)
        return

    await query.edit_message_text(
        "⏳ جاري سحب الصور وتحويل الفصل إلى PDF...\n"
        "قد تستغرق العملية وقتاً حسب عدد الصفحات وحجم الصور."
    )

    pdf_path: str | None = None
    try:
        pdf_path, image_count = await download_chapter_as_pdf(chapter.url, source=chapter.source)
        pdf_size = Path(pdf_path).stat().st_size
        max_bytes = int(os.getenv("MAX_PDF_BYTES", str(49 * 1024 * 1024)))
        if pdf_size > max_bytes:
            raise RuntimeError("الـPDF الناتج أكبر من الحد الآمن للإرسال عبر Telegram.")

        caption = (
            f"📖 {manga.title}\n"
            f"الفصل: {chapter.number}"
            + (f" — {chapter.title}" if chapter.title else "")
            + f"\n🖼️ الصفحات: {image_count}"
        )
        with open(pdf_path, "rb") as document:
            await query.message.reply_document(
                document=document,
                filename=f"{safe_filename(manga.title)} - {chapter.number}.pdf",
                caption=caption[:1024],
            )
        await query.message.reply_text("✅ تم إرسال الفصل. تم تنظيف الملفات المؤقتة.")
    except Exception as exc:
        logger.exception("Chapter PDF failed for %s", chapter.url)
        await query.message.reply_text(f"❌ تعذر تجهيز الفصل: {exc}")
    finally:
        if pdf_path:
            try:
                Path(pdf_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove PDF %s", pdf_path)


@admin_only
async def new_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("search_results", None)
    context.user_data.pop("selected_manga", None)
    context.user_data.pop("chapters", None)
    await query.message.reply_text("🔎 أرسل اسم المانجا التي تريد البحث عنها.")


def safe_filename(value: str) -> str:
    cleaned = "".join(c for c in value if c.isalnum() or c in " _-").strip()
    return (cleaned or "manga")[:80]


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)
    if isinstance(context.error, TelegramError):
        logger.error("Telegram error: %s", context.error)


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if ADMIN_CHAT_ID is None:
        raise RuntimeError("ADMIN_CHAT_ID environment variable is required")

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(TypeHandler(Update, admin_guard), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CallbackQueryHandler(manga_selected, pattern=r"^manga:\d+$"))
    application.add_handler(CallbackQueryHandler(chapter_selected, pattern=r"^chapter:\d+$"))
    application.add_handler(CallbackQueryHandler(new_search, pattern=r"^new_search$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_search))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    application = build_application()
    logger.info("Bot starting")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
