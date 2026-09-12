import html
import logging
import os
from collections import defaultdict
from typing import Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, InlineQueryHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANGADEX_API = "https://api.mangadex.org"
MANGADEX_COVER = "https://uploads.mangadex.org/covers"
PAGE_SIZE = 10
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
http_client: httpx.AsyncClient | None = None
image_cache: dict[str, list[str]] = defaultdict(list)

def title_of(m: dict[str, Any]) -> str:
    return next(iter(m.get("attributes", {}).get("title", {}).values()), "Untitled manga")

def description_of(m: dict[str, Any]) -> str:
    return next(iter(m.get("attributes", {}).get("description", {}).values()), "No description available.").replace("\n", " ")[:700]

def cover_url(m: dict[str, Any]) -> str | None:
    cover = next((r for r in m.get("relationships", []) if r.get("type") == "cover_art"), None)
    fn = cover.get("attributes", {}).get("fileName") if cover else None
    return f"{MANGADEX_COVER}/{m['id']}/{fn}.256.jpg" if fn else None

async def api_get(path: str, params=None):
    if http_client is None: raise RuntimeError("HTTP client is not initialized")
    r = await http_client.get(MANGADEX_API + path, params=params); r.raise_for_status(); return r.json()

async def search_manga(q: str):
    return (await api_get("/manga", {"title": q, "limit": 10, "includes[]": "cover_art"})).get("data", [])

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = (update.inline_query.query or "").strip()
    if not q: return
    try:
        results=[]
        for m in await search_manga(q):
            title, desc = title_of(m), description_of(m)
            results.append(InlineQueryResultArticle(id=m["id"], title=title[:64], description=desc[:200], thumbnail_url=cover_url(m), input_message_content=InputTextMessageContent(f"<b>{html.escape(title)}</b>\n\n{html.escape(desc)}", parse_mode=ParseMode.HTML), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📚 الفصول", callback_data=f"manga:{m['id']}")]])))
        await update.inline_query.answer(results, cache_time=30, is_personal=True)
    except Exception:
        logger.exception("Inline search failed"); await update.inline_query.answer([], cache_time=5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 <b>Manga Bot</b>\n\nابحث عبر: <code>@اسم_البوت اسم المانجا</code>\n\nاختر النتيجة ثم اضغط «📚 الفصول».", parse_mode=ParseMode.HTML)

async def manga_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); mid=q.data.split(":",1)[1]
    try:
        m=(await api_get(f"/manga/{mid}", {"includes[]":"cover_art"}))["data"]
        await show_chapters(q, mid, title_of(m), description_of(m))
    except Exception:
        logger.exception("Manga details failed"); await q.edit_message_text("تعذر جلب تفاصيل المانجا حالياً.")

async def show_chapters(q, mid, title, desc):
    data=await api_get("/chapter", {"manga[]":mid,"limit":PAGE_SIZE,"order[chapter]":"desc","translatedLanguage[]":["en","ar"],"contentRating[]":["safe","suggestive"]})
    buttons=[]
    for c in data.get("data",[]):
        a=c.get("attributes",{}); num=a.get("chapter") or "?"; name=a.get("title") or ""
        buttons.append([InlineKeyboardButton((f"الفصل {num}" + (f" — {name[:28]}" if name else ""))[:64], callback_data=f"chapter:{c['id']}")])
    buttons.append([InlineKeyboardButton("🔄 تحديث", callback_data=f"manga:{mid}")])
    await q.edit_message_text(f"<b>{html.escape(title)}</b>\n\n{html.escape(desc)}\n\n<b>أحدث الفصول:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))

async def read_chapter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer("جاري جلب الصفحات…"); cid=q.data.split(":",1)[1]
    try:
        from telegram import InputMediaPhoto
        ids=image_cache.get(cid)
        if ids:
            for i in range(0,len(ids),10): await q.message.reply_media_group([InputMediaPhoto(x) for x in ids[i:i+10]])
            return
        d=await api_get(f"/at-home/server/{cid}"); ch=d.get("chapter",{}); base=d.get("baseUrl"); h=ch.get("hash"); pages=ch.get("data",[])
        if not base or not h or not pages: raise RuntimeError("No chapter pages")
        sent=[]
        for i in range(0,len(pages),10):
            msgs=await q.message.reply_media_group([InputMediaPhoto(f"{base}/data/{h}/{p}") for p in pages[i:i+10]])
            sent.extend([m.photo[-1].file_id for m in msgs if m.photo])
        image_cache[cid]=sent
    except Exception:
        logger.exception("Chapter read failed"); await q.message.reply_text("تعذر جلب صفحات الفصل حالياً.")

async def error_handler(update, context): logger.error("Unhandled exception", exc_info=context.error)
async def post_init(app):
    global http_client; http_client=httpx.AsyncClient(timeout=30, headers={"User-Agent":"MangaBot/1.0"})
async def post_shutdown(app):
    if http_client: await http_client.aclose()

def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN environment variable is required")
    app=Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start",start)); app.add_handler(InlineQueryHandler(inline_query)); app.add_handler(CallbackQueryHandler(manga_details,pattern=r"^manga:")); app.add_handler(CallbackQueryHandler(read_chapter,pattern=r"^chapter:")); app.add_error_handler(error_handler); app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__": main()
