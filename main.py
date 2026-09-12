import html
import logging
import os
import sqlite3
from contextlib import closing
from typing import Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, InlineQueryHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANGADEX_API = "https://api.mangadex.org"
MANGADEX_COVER = "https://uploads.mangadex.org/covers"
PAGE_SIZE = 10
DB_PATH = os.environ.get("CACHE_DB_PATH", "manga_cache.sqlite3")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
http_client: httpx.AsyncClient | None = None


def db_init() -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS image_cache (chapter_id TEXT PRIMARY KEY, file_ids TEXT NOT NULL)")
        db.commit()


def cache_get(chapter_id: str) -> list[str] | None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        row = db.execute("SELECT file_ids FROM image_cache WHERE chapter_id = ?", (chapter_id,)).fetchone()
    if not row:
        return None
    return row[0].split("\n") if row[0] else []


def cache_put(chapter_id: str, file_ids: list[str]) -> None:
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute("INSERT OR REPLACE INTO image_cache (chapter_id, file_ids) VALUES (?, ?)", (chapter_id, "\n".join(file_ids)))
        db.commit()


def title_of(manga: dict[str, Any]) -> str:
    titles = manga.get("attributes", {}).get("title", {})
    return next(iter(titles.values()), "Untitled manga")


def description_of(manga: dict[str, Any]) -> str:
    descriptions = manga.get("attributes", {}).get("description", {})
    return next(iter(descriptions.values()), "No description available.").replace("\n", " ")[:700]


def cover_url(manga: dict[str, Any]) -> str | None:
    for relation in manga.get("relationships", []):
        if relation.get("type") == "cover_art":
            filename = relation.get("attributes", {}).get("fileName")
            if filename:
                return f"{MANGADEX_COVER}/{manga['id']}/{filename}.256.jpg"
    return None


async def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if http_client is None:
        raise RuntimeError("HTTP client is not initialized")
    response = await http_client.get(MANGADEX_API + path, params=params)
    response.raise_for_status()
    return response.json()


async def search_manga(query: str) -> list[dict[str, Any]]:
    data = await api_get("/manga", {"title": query, "limit": 10, "includes[]": "cover_art", "contentRating[]": ["safe", "suggestive"]})
    return data.get("data", [])


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = (update.inline_query.query or "").strip()
    if not query:
        return
    try:
        results = []
        for manga in await search_manga(query):
            title = title_of(manga)
            desc = description_of(manga)
            results.append(
                InlineQueryResultArticle(
                    id=manga["id"],
                    title=title[:64],
                    description=desc[:200],
                    thumbnail_url=cover_url(manga),
                    input_message_content=InputTextMessageContent(f"<b>{html.escape(title)}</b>\n\n{html.escape(desc)}", parse_mode=ParseMode.HTML),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📚 الفصول", callback_data=f"manga:{manga['id']}")]]),
                )
            )
        await update.inline_query.answer(results, cache_time=30, is_personal=True)
    except Exception:
        logger.exception("Inline search failed")
        await update.inline_query.answer([], cache_time=5)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📚 <b>Manga Bot</b>\n\nابحث عن المانجا عبر الوضع Inline:\n<code>@اسم_البوت naruto</code>\n\nثم اختر المانجا واضغط «📚 الفصول».",
        parse_mode=ParseMode.HTML,
    )


async def manga_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    manga_id = query.data.split(":", 1)[1]
    try:
        manga = (await api_get(f"/manga/{manga_id}", {"includes[]": "cover_art"})).get("data", {})
        await show_chapters(query, manga_id, title_of(manga), description_of(manga))
    except Exception:
        logger.exception("Manga details failed")
        await query.edit_message_text("تعذر جلب بيانات المانجا حاليًا.")


async def show_chapters(query, manga_id: str, title: str, desc: str) -> None:
    data = await api_get("/chapter", {
        "manga[]": manga_id,
        "limit": PAGE_SIZE,
        "order[chapter]": "desc",
        "translatedLanguage[]": ["ar", "en"],
        "contentRating[]": ["safe", "suggestive"],
        "includes[]": "scanlation_group",
    })
    buttons = []
    for chapter in data.get("data", []):
        attrs = chapter.get("attributes", {})
        number = attrs.get("chapter") or "?"
        name = attrs.get("title") or ""
        label = f"الفصل {number}" + (f" — {name[:28]}" if name else "")
        buttons.append([InlineKeyboardButton(label[:64], callback_data=f"chapter:{chapter['id']}")])
    buttons.append([InlineKeyboardButton("🔄 تحديث الفصول", callback_data=f"manga:{manga_id}")])
    text = f"<b>{html.escape(title)}</b>\n\n{html.escape(desc)}\n\n<b>آخر الفصول:</b>"
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def read_chapter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("جاري تجهيز الفصل…")
    chapter_id = query.data.split(":", 1)[1]
    try:
        cached = cache_get(chapter_id)
        if cached:
            for start in range(0, len(cached), 10):
                from telegram import InputMediaPhoto
                await query.message.reply_media_group([InputMediaPhoto(file_id) for file_id in cached[start:start + 10]])
            return

        data = await api_get(f"/at-home/server/{chapter_id}")
        chapter = data.get("chapter", {})
        base_url = data.get("baseUrl")
        chapter_hash = chapter.get("hash")
        pages = chapter.get("data", [])
        if not base_url or not chapter_hash or not pages:
            raise RuntimeError("No chapter pages")

        from telegram import InputMediaPhoto
        file_ids: list[str] = []
        for start in range(0, len(pages), 10):
            media = [InputMediaPhoto(f"{base_url}/data/{chapter_hash}/{page}") for page in pages[start:start + 10]]
            messages = await query.message.reply_media_group(media)
            file_ids.extend([m.photo[-1].file_id for m in messages if m.photo])
        if file_ids:
            cache_put(chapter_id, file_ids)
        await query.message.reply_text("✅ تم إرسال الفصل. في المرة القادمة سيُستخدم التخزين المؤقت لتسريع الإرسال.")
    except Exception:
        logger.exception("Chapter read failed")
        await query.message.reply_text("تعذر تحميل صفحات الفصل حاليًا. حاول مرة أخرى لاحقًا.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)


async def post_init(app: Application) -> None:
    global http_client
    db_init()
    http_client = httpx.AsyncClient(timeout=30, headers={"User-Agent": "MangaBot/1.1"})


async def post_shutdown(app: Application) -> None:
    global http_client
    if http_client:
        await http_client.aclose()
        http_client = None


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(manga_details, pattern=r"^manga:"))
    app.add_handler(CallbackQueryHandler(read_chapter, pattern=r"^chapter:"))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
