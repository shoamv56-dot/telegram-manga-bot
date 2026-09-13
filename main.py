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

from scraper import (
    ChapterResult,
    MangaResult,
    download_chapter_as_pdf,
    download_chapters_as_pdf,
    get_chapters,
    search_manga,
)

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
MAX_CHAPTERS = max(30, int(os.getenv("MAX_CHAPTERS", "1000")))
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(49 * 1024 * 1024)))
CALLBACK_TTL_TEXT = "انتهت صلاحية هذا الزر. أعد البحث عن المانجا."

SOURCES = {
    "all": ("🌐", "كل المصادر"),
    "swat": ("🔵", "سوات"),
    "azora": ("🔷", "ازورا"),
    "teamx": ("🟣", "تيم اكس"),
    "mangalike": ("🟢", "مانجاليك"),
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegram-manga-bot")


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and ADMIN_CHAT_ID is not None and user.id == ADMIN_CHAT_ID)


async def admin_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


def source_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔵 سوات", callback_data="source:swat"),
            InlineKeyboardButton("🔷 ازورا", callback_data="source:azora"),
        ],
        [
            InlineKeyboardButton("🟣 تيم اكس", callback_data="source:teamx"),
            InlineKeyboardButton("🟢 مانجاليك", callback_data="source:mangalike"),
        ],
        [InlineKeyboardButton("🌐 البحث في الكل", callback_data="source:all")],
    ])


def result_keyboard(results: list[MangaResult]) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(results):
        icon = next((v[0] for k, v in SOURCES.items() if k == source_key(item.source)), "📚")
        rows.append([InlineKeyboardButton(f"{icon} {item.title[:42]} — {item.source}", callback_data=f"manga:{index}")])
    rows.append([InlineKeyboardButton("⚙️ تغيير المصدر", callback_data="choose_source")])
    return InlineKeyboardMarkup(rows)


def source_key(source: str) -> str:
    value = source.casefold()
    if "swat" in value or "سوات" in value:
        return "swat"
    if "azora" in value or "ازورا" in value:
        return "azora"
    if "team" in value or "تيم" in value:
        return "teamx"
    if "like" in value or "ليك" in value or "لايك" in value:
        return "mangalike"
    return "all"


def chapter_ranges(chapters: list[ChapterResult]) -> list[tuple[int, int]]:
    nums = []
    for chapter in chapters:
        try:
            n = float(chapter.number)
            if n.is_integer() and n >= 1:
                nums.append(int(n))
        except (ValueError, TypeError):
            continue
    if not nums:
        return []
    highest = max(nums)
    highest = min(highest, MAX_CHAPTERS)
    ranges = []
    start = 1
    while start <= highest:
        end = min(start + 29, highest)
        ranges.append((start, end))
        start = end + 1
    return ranges


def chapter_keyboard(chapters: list[ChapterResult]) -> InlineKeyboardMarkup:
    rows = []
    ranges = chapter_ranges(chapters)
    for start, end in ranges:
        rows.append([InlineKeyboardButton(f"📦 تنزيل {start} - {end}", callback_data=f"batch:{start}:{end}")])
    rows.append([InlineKeyboardButton("━━━━━━━━ الفصول الأخيرة ━━━━━━━━", callback_data="noop")])
    for index, chapter in enumerate(chapters[:30]):
        label = f"📄 {chapter.number}"
        if chapter.title:
            label += f" — {chapter.title[:30]}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"chapter:{index}")])
    rows.append([
        InlineKeyboardButton("⚙️ المصادر", callback_data="choose_source"),
        InlineKeyboardButton("🔎 بحث جديد", callback_data="new_search"),
    ])
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
    context.user_data.setdefault("selected_source", "all")
    await update.effective_message.reply_text(
        "👋 <b>مرحباً بك في Manga Bot</b>\n\n"
        "اختر مصدر المانجا أولاً، ثم أرسل اسم المانجا.\n\n"
        "📦 يمكنك تنزيل حزم مثل <b>1-30</b> و<b>31-60</b> في ملف PDF واحد.\n"
        "📄 الجودة: متوسطة ومضغوطة لتقليل حجم الملف.\n\n"
        "🔵 سوات • 🔷 ازورا • 🟣 تيم اكس • 🟢 مانجاليك",
        parse_mode=ParseMode.HTML,
        reply_markup=source_keyboard(),
    )


@admin_only
async def source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    if key not in SOURCES:
        await query.edit_message_text(CALLBACK_TTL_TEXT)
        return
    context.user_data["selected_source"] = key
    icon, label = SOURCES[key]
    await query.edit_message_text(
        f"{icon} <b>المصدر المختار: {html.escape(label)}</b>\n\nأرسل اسم المانجا الآن للبحث.",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def choose_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("اختر مصدر البحث:", reply_markup=source_keyboard())


@admin_only
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await perform_search(update, context, " ".join(context.args).strip())


@admin_only
async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_message.text:
        await perform_search(update, context, update.effective_message.text.strip())


async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str) -> None:
    message = update.effective_message
    if not message:
        return
    if not query_text:
        await message.reply_text("اكتب اسم المانجا بعد /search أو أرسل الاسم مباشرة.")
        return
    source = context.user_data.get("selected_source", "all")
    source_label = SOURCES.get(source, SOURCES["all"])[1]
    status = await message.reply_text(f"🔎 جاري البحث في {source_label}...")
    try:
        results = await search_manga(query_text, limit=MAX_RESULTS, source=source)
    except Exception:
        logger.exception("Search failed for %r", query_text)
        await status.edit_text("❌ حدث خطأ أثناء البحث. جرّب اسماً آخر.")
        return
    if not results:
        await status.edit_text("لم أجد نتائج. جرّب الاسم الإنجليزي أو العربي للمانجا أو غيّر المصدر.", reply_markup=source_keyboard())
        return
    context.user_data["search_results"] = results
    await status.edit_text(
        f"📚 نتائج البحث عن: <b>{html.escape(query_text)}</b>\nالمصدر: <b>{html.escape(source_label)}</b>\n\nاختر المانجا:",
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
    await query.edit_message_text("📖 جاري جلب الفصول... قد يستغرق ذلك لحظات.")
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
        f"المصدر: {html.escape(item.source)}\n"
        f"عدد الفصول التي تم العثور عليها: {len(chapters)}\n\n"
        "📦 اختر حزمة للتنزيل أو اختر فصلاً منفرداً:\n"
        "<i>PDF مضغوط بجودة متوسطة.</i>",
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
    status = await query.edit_message_text(
        "⏳ جاري سحب الصور وتحويل الفصل إلى PDF مضغوط...\n"
        "الجودة: متوسطة | الضغط: مفعّل"
    )
    pdf_path = None
    try:
        pdf_path, image_count = await download_chapter_as_pdf(chapter.url, source=chapter.source)
        pdf_size = Path(pdf_path).stat().st_size
        if pdf_size > MAX_PDF_BYTES:
            raise RuntimeError("الـPDF أكبر من الحد المسموح به لإرسال Telegram.")
        caption = (
            f"📖 {manga.title}\n"
            f"📄 الفصل: {chapter.number}"
            + (f" — {chapter.title}" if chapter.title else "")
            + f"\n🖼️ الصفحات: {image_count}\n"
            "📦 PDF مضغوط | جودة متوسطة"
        )
        with open(pdf_path, "rb") as document:
            await query.message.reply_document(
                document=document,
                filename=f"{safe_filename(manga.title)} - {chapter.number}.pdf",
                caption=caption[:1024],
            )
        await status.edit_text("✅ تم إرسال الفصل بنجاح.")
    except Exception as exc:
        logger.exception("Chapter PDF failed for %s", chapter.url)
        await status.edit_text(f"❌ تعذر تجهيز الفصل: {exc}")
    finally:
        if pdf_path:
            try:
                Path(pdf_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove PDF %s", pdf_path)


@admin_only
async def batch_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, start_raw, end_raw = query.data.split(":")
        start, end = int(start_raw), int(end_raw)
        chapters: list[ChapterResult] = context.user_data["chapters"]
        manga: MangaResult = context.user_data["selected_manga"]
    except (KeyError, ValueError, TypeError):
        await query.edit_message_text(CALLBACK_TTL_TEXT)
        return
    selected = []
    for chapter in chapters:
        try:
            number = float(chapter.number)
        except ValueError:
            continue
        if start <= number <= end:
            selected.append(chapter)
    selected.sort(key=lambda c: float(c.number))
    if not selected:
        await query.answer("لا توجد فصول ضمن هذه الحزمة.", show_alert=True)
        return
    status = await query.edit_message_text(
        f"⏳ جاري تجهيز الحزمة {start}-{end}...\n"
        f"الفصول الموجودة: {len(selected)}\n"
        "📦 PDF واحد مضغوط | جودة متوسطة\n"
        "قد تستغرق العملية وقتاً حسب عدد الصفحات."
    )
    pdf_path = None
    try:
        async def progress(done: int, total: int) -> None:
            if done == 1 or done == total or done % 3 == 0:
                try:
                    await status.edit_text(
                        f"⏳ جاري تجهيز الحزمة {start}-{end}...\n"
                        f"الفصول: {done}/{total}\n"
                        "📦 PDF مضغوط | جودة متوسطة"
                    )
                except TelegramError:
                    pass
        pdf_path, chapter_count, page_count = await download_chapters_as_pdf(selected, progress=progress)
        pdf_size = Path(pdf_path).stat().st_size
        if pdf_size > MAX_PDF_BYTES:
            raise RuntimeError(
                f"الحزمة الناتجة حجمها {pdf_size / 1024 / 1024:.1f}MB، وهي أكبر من الحد المسموح. "
                "جرّب حزمة أصغر."
            )
        caption = (
            f"📚 {manga.title}\n"
            f"📦 الحزمة: {start}-{end}\n"
            f"📄 عدد الفصول: {chapter_count}\n"
            f"🖼️ عدد الصفحات: {page_count}\n"
            "📦 PDF مضغوط | جودة متوسطة"
        )
        with open(pdf_path, "rb") as document:
            await query.message.reply_document(
                document=document,
                filename=f"{safe_filename(manga.title)} - {start}-{end}.pdf",
                caption=caption[:1024],
            )
        await status.edit_text("✅ تم إرسال الحزمة بنجاح.")
    except Exception as exc:
        logger.exception("Batch PDF failed for %s-%s", start, end)
        await status.edit_text(f"❌ تعذر تجهيز الحزمة: {exc}")
    finally:
        if pdf_path:
            try:
                Path(pdf_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove batch PDF %s", pdf_path)


@admin_only
async def new_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("search_results", None)
    context.user_data.pop("selected_manga", None)
    context.user_data.pop("chapters", None)
    await query.message.reply_text("🔎 أرسل اسم المانجا التي تريد البحث عنها.", reply_markup=source_keyboard())


@admin_only
async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


def safe_filename(value: str) -> str:
    cleaned = "".join(c for c in value if c.isalnum() or c in " _-").strip()
    return (cleaned or "manga")[:80]


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if ADMIN_CHAT_ID is None:
        raise RuntimeError("ADMIN_CHAT_ID environment variable is required")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(TypeHandler(Update, admin_guard), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CallbackQueryHandler(source_selected, pattern=r"^source:(?:all|swat|azora|teamx|mangalike)$"))
    application.add_handler(CallbackQueryHandler(choose_source, pattern=r"^choose_source$"))
    application.add_handler(CallbackQueryHandler(manga_selected, pattern=r"^manga:\d+$"))
    application.add_handler(CallbackQueryHandler(batch_selected, pattern=r"^batch:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(chapter_selected, pattern=r"^chapter:\d+$"))
    application.add_handler(CallbackQueryHandler(new_search, pattern=r"^new_search$"))
    application.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_search))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    application = build_application()
    logger.info("Bot starting")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
