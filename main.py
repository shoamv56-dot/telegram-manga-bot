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
    Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, TypeHandler, filters,
)

from scraper import (
    ChapterResult, MangaResult, download_chapter_as_file,
    download_chapters_as_file, get_chapters, search_manga,
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
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", os.getenv("MAX_PDF_BYTES", str(49 * 1024 * 1024))))
CALLBACK_TTL_TEXT = "انتهت صلاحية هذا الزر. أعد البحث عن المانجا."
SOURCES = {
    "all": ("🌐", "كل المصادر"), "swat": ("🔵", "سوات"),
    "azora": ("🔷", "ازورا"), "teamx": ("🟣", "تيم اكس"),
    "mangalike": ("🟢", "مانجاليك"),
}
QUALITY = {"low": ("🟢", "خفيفة", "50"), "medium": ("🟡", "متوسطة", "65"), "high": ("🔴", "عالية", "82")}

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
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


def source_key(source: str) -> str:
    value = (source or "").casefold()
    if "swat" in value or "سوات" in value: return "swat"
    if "azora" in value or "ازورا" in value: return "azora"
    if "team" in value or "تيم" in value: return "teamx"
    if "like" in value or "ليك" in value or "لايك" in value: return "mangalike"
    return "all"


def source_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 سوات", callback_data="source:swat"), InlineKeyboardButton("🔷 ازورا", callback_data="source:azora")],
        [InlineKeyboardButton("🟣 تيم اكس", callback_data="source:teamx"), InlineKeyboardButton("🟢 مانجاليك", callback_data="source:mangalike")],
        [InlineKeyboardButton("🌐 البحث في الكل", callback_data="source:all")],
    ])


def result_keyboard(results: list[MangaResult]) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(results):
        icon = SOURCES.get(source_key(item.source), ("📚", ""))[0]
        rows.append([InlineKeyboardButton(f"{icon} {item.title[:40]} — {item.source}", callback_data=f"manga:{index}")])
    rows.append([InlineKeyboardButton("⚙️ تغيير المصدر", callback_data="choose_source")])
    return InlineKeyboardMarkup(rows)


def chapter_ranges(chapters: list[ChapterResult]) -> list[tuple[int, int]]:
    nums = []
    for ch in chapters:
        try:
            n = float(ch.number)
            if n.is_integer() and n >= 1: nums.append(int(n))
        except (ValueError, TypeError): pass
    if not nums: return []
    highest = min(max(nums), MAX_CHAPTERS)
    out = []; start = 1
    while start <= highest:
        end = min(start + 29, highest); out.append((start, end)); start = end + 1
    return out


def chapter_keyboard(chapters: list[ChapterResult]) -> InlineKeyboardMarkup:
    rows = []
    for start, end in chapter_ranges(chapters):
        rows.append([InlineKeyboardButton(f"📦 {start} - {end}", callback_data=f"batch:{start}:{end}")])
    rows.append([InlineKeyboardButton("📦 آخر 30 فصل", callback_data="batch:last30")])
    rows.append([InlineKeyboardButton("🎯 اختيار نطاق مخصص", callback_data="custom_range")])
    rows.append([InlineKeyboardButton("━━━━━━━━ الفصول الأخيرة ━━━━━━━━", callback_data="noop")])
    for index, chapter in enumerate(chapters[:30]):
        label = f"📄 {chapter.number}" + (f" — {chapter.title[:30]}" if chapter.title else "")
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"chapter:{index}"), InlineKeyboardButton("👁️", callback_data=f"read:{index}")])
    rows += [[InlineKeyboardButton("⭐ إضافة للمفضلة", callback_data="favorite:add"), InlineKeyboardButton("🔔 متابعة", callback_data="follow:add")],
             [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings"), InlineKeyboardButton("🔎 بحث جديد", callback_data="new_search")]]
    return InlineKeyboardMarkup(rows)


def format_keyboard(context: ContextTypes.DEFAULT_TYPE, prefix: str = "format") -> InlineKeyboardMarkup:
    current = context.user_data.get("file_format", "pdf")
    def mark(k): return "✅ " if current == k else ""
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{mark('pdf')}📄 PDF", callback_data=f"{prefix}:pdf"), InlineKeyboardButton(f"{mark('cbz')}🗜️ CBZ", callback_data=f"{prefix}:cbz")]])


def quality_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    current = context.user_data.get("quality", "medium")
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{'✅ ' if current=='low' else ''}🟢 خفيفة", callback_data="quality:low"), InlineKeyboardButton(f"{'✅ ' if current=='medium' else ''}🟡 متوسطة", callback_data="quality:medium"), InlineKeyboardButton(f"{'✅ ' if current=='high' else ''}🔴 عالية", callback_data="quality:high")], [InlineKeyboardButton("↩️ الإعدادات", callback_data="settings")]])


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📄 نوع الملف", callback_data="setting_format"), InlineKeyboardButton("🎨 جودة الصور", callback_data="setting_quality")], [InlineKeyboardButton("⭐ المفضلة", callback_data="favorites"), InlineKeyboardButton("📥 سجل التنزيلات", callback_data="downloads")], [InlineKeyboardButton("🌐 المصادر", callback_data="choose_source"), InlineKeyboardButton("👑 لوحة المشرف", callback_data="admin_panel")]])


def safe_filename(value: str) -> str:
    cleaned = "".join(c for c in value if c.isalnum() or c in " _-").strip()
    return (cleaned or "manga")[:80]


def selected_format(context) -> str: return context.user_data.get("file_format", "pdf")
def selected_quality(context) -> str: return context.user_data.get("quality", "medium")

def apply_quality(context) -> None:
    os.environ["PDF_JPEG_QUALITY"] = QUALITY[selected_quality(context)][2]


def remember_download(context, label: str) -> None:
    history = context.user_data.setdefault("downloads", [])
    history.insert(0, label); del history[20:]


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update): await admin_guard(update, context); return
        return await func(update, context)
    return wrapper


@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.setdefault("selected_source", "all"); context.user_data.setdefault("file_format", "pdf"); context.user_data.setdefault("quality", "medium")
    await update.effective_message.reply_text("👋 <b>Manga Bot</b>\n\nاختر مصدر البحث ثم أرسل اسم المانجا.\n\n📄 PDF أو 🗜️ CBZ\n🟡 الجودة المتوسطة افتراضياً\n📦 حزم 1-30 و31-60 وغيرها\n\n🔵 سوات • 🔷 ازورا • 🟣 تيم اكس • 🟢 مانجاليك", parse_mode=ParseMode.HTML, reply_markup=source_keyboard())


@admin_only
async def source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q=update.callback_query; await q.answer(); key=q.data.split(":",1)[1]
    if key not in SOURCES: await q.edit_message_text(CALLBACK_TTL_TEXT); return
    context.user_data["selected_source"] = key; icon,label=SOURCES[key]
    await q.edit_message_text(f"{icon} <b>المصدر: {html.escape(label)}</b>\n\nأرسل اسم المانجا الآن.", parse_mode=ParseMode.HTML)


@admin_only
async def choose_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q=update.callback_query; await q.answer(); await q.edit_message_text("🌐 اختر مصدر البحث:", reply_markup=source_keyboard())


@admin_only
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await perform_search(update, context, " ".join(context.args).strip())


@admin_only
async def text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_message.text: await perform_search(update, context, update.effective_message.text.strip())


async def perform_search(update, context, query_text: str) -> None:
    message=update.effective_message
    if not message: return
    if not query_text: await message.reply_text("اكتب اسم المانجا بعد /search أو أرسل الاسم مباشرة."); return
    source=context.user_data.get("selected_source","all"); label=SOURCES.get(source,SOURCES["all"])[1]
    status=await message.reply_text(f"🔎 جاري البحث في {label}...")
    try: results=await search_manga(query_text, limit=MAX_RESULTS, source=source)
    except Exception:
        logger.exception("Search failed"); await status.edit_text("❌ حدث خطأ أثناء البحث."); return
    if not results: await status.edit_text("لم أجد نتائج. جرّب اسماً آخر أو غيّر المصدر.", reply_markup=source_keyboard()); return
    context.user_data["search_results"]=results
    await status.edit_text(f"📚 نتائج: <b>{html.escape(query_text)}</b>\nالمصدر: <b>{html.escape(label)}</b>\n\nاختر المانجا:", parse_mode=ParseMode.HTML, reply_markup=result_keyboard(results))


@admin_only
async def manga_selected(update, context) -> None:
    q=update.callback_query; await q.answer()
    try: item: MangaResult=context.user_data["search_results"][int(q.data.split(":",1)[1])]
    except (KeyError,IndexError,ValueError,TypeError): await q.edit_message_text(CALLBACK_TTL_TEXT); return
    await q.edit_message_text("📖 جاري جلب الفصول...")
    try: chapters=(await get_chapters(item.url, source=item.source))[:MAX_CHAPTERS]
    except Exception:
        logger.exception("Chapter listing failed"); await q.edit_message_text("❌ تعذر جلب الفصول من المصدر حالياً."); return
    if not chapters: await q.edit_message_text(f"❌ لم أجد فصولاً لـ <b>{html.escape(item.title)}</b>.",parse_mode=ParseMode.HTML); return
    context.user_data["selected_manga"]=item; context.user_data["chapters"]=chapters
    await q.edit_message_text(f"📚 <b>{html.escape(item.title)}</b>\n🌐 المصدر: {html.escape(item.source)}\n📚 الفصول: {len(chapters)}\n\nاختر فصلاً أو حزمة:\n📄/🗜️ نوع الملف: <b>{selected_format(context).upper()}</b>\n🎨 الجودة: <b>{QUALITY[selected_quality(context)][1]}</b>",parse_mode=ParseMode.HTML,reply_markup=chapter_keyboard(chapters))


async def send_file_result(q, context, path: str, filename: str, caption: str, success_text: str) -> None:
    try:
        size=Path(path).stat().st_size
        if size > MAX_FILE_BYTES: raise RuntimeError(f"حجم الملف {size/1024/1024:.1f}MB أكبر من الحد المسموح. جرّب جودة أقل أو حزمة أصغر.")
        with open(path,"rb") as f: await q.message.reply_document(document=f, filename=filename, caption=caption[:1024])
        await q.edit_message_text(success_text)
    finally:
        try: Path(path).unlink(missing_ok=True)
        except OSError: pass


@admin_only
async def chapter_selected(update, context) -> None:
    q=update.callback_query; await q.answer()
    try:
        ch:ChapterResult=context.user_data["chapters"][int(q.data.split(":",1)[1])]; manga:MangaResult=context.user_data["selected_manga"]
    except (KeyError,IndexError,ValueError,TypeError): await q.edit_message_text(CALLBACK_TTL_TEXT); return
    fmt=selected_format(context); apply_quality(context); ext=fmt
    status=await q.edit_message_text(f"⏳ جاري سحب الصور وتحويل الفصل إلى {fmt.upper()}...\n🎨 الجودة: {QUALITY[selected_quality(context)][1]}")
    path=None
    try:
        path,count=await download_chapter_as_file(ch,fmt=fmt)
        caption=f"📖 {manga.title}\n📄 الفصل: {ch.number}\n🌐 المصدر: {ch.source}\n🖼️ الصفحات: {count}\n📦 {fmt.upper()} | {QUALITY[selected_quality(context)][1]}"
        await send_file_result(q,context,path,f"{safe_filename(manga.title)} - {ch.number}.{ext}",caption,"✅ تم إرسال الفصل بنجاح.")
        remember_download(context,f"{manga.title} — الفصل {ch.number} ({fmt.upper()})")
    except Exception as exc: logger.exception("Chapter file failed"); await status.edit_text(f"❌ تعذر تجهيز الفصل: {exc}")
    finally:
        if path: Path(path).unlink(missing_ok=True)


@admin_only
async def batch_selected(update, context) -> None:
    q=update.callback_query; await q.answer(); data=q.data
    try:
        parts=data.split(":"); chapters:list[ChapterResult]=context.user_data["chapters"]; manga:MangaResult=context.user_data["selected_manga"]
        if parts[1]=="last30":
            nums=[float(c.number) for c in chapters if str(c.number).replace('.','',1).isdigit()]; high=max(nums); start=max(1,int(high)-29); end=int(high)
        else: start,end=int(parts[1]),int(parts[2])
    except (KeyError,ValueError,TypeError): await q.edit_message_text(CALLBACK_TTL_TEXT); return
    selected=[]
    for c in chapters:
        try:
            n=float(c.number)
            if start<=n<=end: selected.append(c)
        except ValueError: pass
    selected.sort(key=lambda c: float(c.number))
    if not selected: await q.answer("لا توجد فصول ضمن هذه الحزمة.",show_alert=True); return
    fmt=selected_format(context); apply_quality(context)
    status=await q.edit_message_text(f"⏳ تجهيز الحزمة {start}-{end} بصيغة {fmt.upper()}...\n📚 0/{len(selected)} فصل\n🎨 الجودة: {QUALITY[selected_quality(context)][1]}")
    path=None
    try:
        async def progress(done,total):
            if done==1 or done==total or done%3==0:
                try: await status.edit_text(f"⏳ تجهيز الحزمة {start}-{end} بصيغة {fmt.upper()}...\n📚 {done}/{total} فصل\n🎨 الجودة: {QUALITY[selected_quality(context)][1]}")
                except TelegramError: pass
        path,chapter_count,page_count=await download_chapters_as_file(selected,fmt=fmt,progress=progress)
        caption=f"📚 {manga.title}\n📦 الحزمة: {start}-{end}\n🌐 المصدر: {manga.source}\n📄 الفصول: {chapter_count}\n🖼️ الصفحات: {page_count}\n📦 {fmt.upper()} | {QUALITY[selected_quality(context)][1]}"
        await send_file_result(q,context,path,f"{safe_filename(manga.title)} - {start}-{end}.{fmt}",caption,"✅ تم إرسال الحزمة بنجاح.")
        remember_download(context,f"{manga.title} — {start}-{end} ({fmt.upper()})")
    except Exception as exc: logger.exception("Batch file failed"); await status.edit_text(f"❌ تعذر تجهيز الحزمة: {exc}")
    finally:
        if path: Path(path).unlink(missing_ok=True)


@admin_only
async def custom_range(update, context) -> None:
    q=update.callback_query; await q.answer(); context.user_data["awaiting_range"]=True
    await q.edit_message_text("🎯 أرسل النطاق بهذا الشكل:\n\n<code>1-30</code>\nأو\n<code>25-75</code>",parse_mode=ParseMode.HTML)


@admin_only
async def format_selected(update, context) -> None:
    q=update.callback_query; await q.answer(); fmt=q.data.split(":",1)[1]; context.user_data["file_format"]=fmt
    await q.edit_message_text(f"✅ نوع الملف: <b>{fmt.upper()}</b>\n\nسيتم استخدامه في التنزيلات القادمة.",parse_mode=ParseMode.HTML,reply_markup=settings_keyboard())


@admin_only
async def quality_selected(update, context) -> None:
    q=update.callback_query; await q.answer(); key=q.data.split(":",1)[1]; context.user_data["quality"]=key
    await q.edit_message_text(f"✅ الجودة: <b>{QUALITY[key][1]}</b>",parse_mode=ParseMode.HTML,reply_markup=settings_keyboard())


@admin_only
async def settings(update, context) -> None:
    q=update.callback_query; await q.answer(); await q.edit_message_text(f"⚙️ <b>الإعدادات</b>\n\n📄 الملف: <b>{selected_format(context).upper()}</b>\n🎨 الجودة: <b>{QUALITY[selected_quality(context)][1]}</b>\n🗜️ الضغط: مفعّل",parse_mode=ParseMode.HTML,reply_markup=settings_keyboard())


@admin_only
async def setting_format(update, context) -> None:
    q=update.callback_query; await q.answer(); await q.edit_message_text("📄 اختر نوع الملف:",reply_markup=format_keyboard(context,"format"))


@admin_only
async def setting_quality(update, context) -> None:
    q=update.callback_query; await q.answer(); await q.edit_message_text("🎨 اختر جودة الصور:",reply_markup=quality_keyboard(context))


@admin_only
async def favorites(update, context) -> None:
    q=update.callback_query; await q.answer(); fav=context.user_data.get("favorites",[])
    if not fav: await q.edit_message_text("⭐ لا توجد مانجات في المفضلة بعد.",reply_markup=settings_keyboard()); return
    await q.edit_message_text("⭐ <b>المفضلة</b>\n\n"+"\n".join(f"• {html.escape(x)}" for x in fav),parse_mode=ParseMode.HTML,reply_markup=settings_keyboard())


@admin_only
async def favorite_add(update, context) -> None:
    q=update.callback_query; await q.answer(); manga=context.user_data.get("selected_manga")
    if manga:
        fav=context.user_data.setdefault("favorites",[])
        if manga.title not in fav: fav.append(manga.title)
    await q.edit_message_text("⭐ تمت إضافة المانجا إلى المفضلة.",reply_markup=chapter_keyboard(context.user_data.get("chapters",[])))


@admin_only
async def follow_add(update, context) -> None:
    q=update.callback_query; await q.answer(); manga=context.user_data.get("selected_manga")
    if manga: context.user_data.setdefault("following",[]).append(manga.title) if manga.title not in context.user_data.setdefault("following",[]) else None
    await q.edit_message_text("🔔 تمت إضافة المانجا إلى المتابعات.",reply_markup=chapter_keyboard(context.user_data.get("chapters",[])))


@admin_only
async def downloads(update, context) -> None:
    q=update.callback_query; await q.answer(); items=context.user_data.get("downloads",[])
    await q.edit_message_text("📥 <b>آخر التنزيلات</b>\n\n"+("\n".join(f"• {html.escape(x)}" for x in items) if items else "لا توجد تنزيلات بعد."),parse_mode=ParseMode.HTML,reply_markup=settings_keyboard())


@admin_only
async def direct_read(update, context) -> None:
    q=update.callback_query; await q.answer();
    try: ch=context.user_data["chapters"][int(q.data.split(":",1)[1])]
    except (KeyError,IndexError,ValueError): await q.edit_message_text(CALLBACK_TTL_TEXT); return
    await q.message.reply_text(f"👁️ قراءة مباشرة — الفصل {ch.number}\n{ch.url}")


@admin_only
async def new_search(update, context) -> None:
    q=update.callback_query; await q.answer(); context.user_data.pop("search_results",None); context.user_data.pop("selected_manga",None); context.user_data.pop("chapters",None)
    await q.message.reply_text("🔎 أرسل اسم المانجا.",reply_markup=source_keyboard())


@admin_only
async def admin_panel(update, context) -> None:
    q=update.callback_query; await q.answer(); chapters=len(context.user_data.get("chapters",[])); downloads_count=len(context.user_data.get("downloads",[])); fav=len(context.user_data.get("favorites",[]))
    await q.edit_message_text(f"👑 <b>لوحة المشرف</b>\n\n📚 الفصول المحملة في الذاكرة: {chapters}\n📥 سجل التنزيلات: {downloads_count}\n⭐ المفضلة: {fav}\n🌐 المصادر: 4 + MangaDex ضمن البحث العام",parse_mode=ParseMode.HTML,reply_markup=settings_keyboard())


@admin_only
async def noop(update, context): await update.callback_query.answer()


@admin_only
async def range_message(update, context) -> None:
    if not context.user_data.pop("awaiting_range",False): await text_search(update,context); return
    text=(update.effective_message.text or "").strip()
    m=__import__('re').fullmatch(r"\s*(\d+)\s*[-–]\s*(\d+)\s*",text)
    if not m: await update.effective_message.reply_text("❌ الصيغة غير صحيحة. مثال: 1-30"); context.user_data["awaiting_range"]=True; return
    start,end=int(m.group(1)),int(m.group(2))
    if start<1 or end<start or end-start>99: await update.effective_message.reply_text("❌ اختر نطاقاً من 1 إلى 100 فصل."); context.user_data["awaiting_range"]=True; return
    chapters=context.user_data.get("chapters",[])
    selected=[c for c in chapters if c.number.replace('.','',1).isdigit() and start<=float(c.number)<=end]
    if not selected: await update.effective_message.reply_text("❌ لا توجد فصول ضمن هذا النطاق."); return
    # Reuse batch workflow by constructing a temporary callback-like message is unnecessary; expose a direct action button.
    context.user_data["custom_batch"]=(start,end)
    await update.effective_message.reply_text(f"📦 النطاق {start}-{end} جاهز. اختر الصيغة:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📄 PDF",callback_data="customfmt:pdf"),InlineKeyboardButton("🗜️ CBZ",callback_data="customfmt:cbz")]]))


@admin_only
async def custom_format(update, context) -> None:
    q=update.callback_query; await q.answer(); fmt=q.data.split(":",1)[1]; context.user_data["file_format"]=fmt
    start,end=context.user_data.get("custom_batch",(1,1)); chapters=context.user_data.get("chapters",[]); manga=context.user_data.get("selected_manga")
    selected=[c for c in chapters if c.number.replace('.','',1).isdigit() and start<=float(c.number)<=end]
    if not selected: await q.edit_message_text("❌ لا توجد فصول."); return
    apply_quality(context); status=await q.edit_message_text(f"⏳ تجهيز {start}-{end} بصيغة {fmt.upper()}..."); path=None
    try:
        path,cc,pc=await download_chapters_as_file(selected,fmt=fmt)
        await send_file_result(q,context,path,f"{safe_filename(manga.title)} - {start}-{end}.{fmt}",f"📚 {manga.title}\n📦 {start}-{end}\n📄 الفصول: {cc}\n🖼️ الصفحات: {pc}\n📦 {fmt.upper()}","✅ تم إرسال الحزمة.")
        remember_download(context,f"{manga.title} — {start}-{end} ({fmt.upper()})")
    except Exception as exc: logger.exception("Custom batch failed"); await status.edit_text(f"❌ {exc}")
    finally:
        if path: Path(path).unlink(missing_ok=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None: logger.error("Unhandled exception",exc_info=context.error)


def build_application() -> Application:
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN environment variable is required")
    if ADMIN_CHAT_ID is None: raise RuntimeError("ADMIN_CHAT_ID environment variable is required")
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(TypeHandler(Update,admin_guard),group=-1)
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("search",search_command))
    app.add_handler(CallbackQueryHandler(source_selected,pattern=r"^source:(?:all|swat|azora|teamx|mangalike)$"))
    app.add_handler(CallbackQueryHandler(choose_source,pattern=r"^choose_source$")); app.add_handler(CallbackQueryHandler(manga_selected,pattern=r"^manga:\d+$"))
    app.add_handler(CallbackQueryHandler(batch_selected,pattern=r"^batch:(?:\d+:\d+|last30)$")); app.add_handler(CallbackQueryHandler(chapter_selected,pattern=r"^chapter:\d+$"))
    app.add_handler(CallbackQueryHandler(custom_range,pattern=r"^custom_range$")); app.add_handler(CallbackQueryHandler(format_selected,pattern=r"^format:(?:pdf|cbz)$")); app.add_handler(CallbackQueryHandler(quality_selected,pattern=r"^quality:(?:low|medium|high)$"))
    app.add_handler(CallbackQueryHandler(settings,pattern=r"^settings$")); app.add_handler(CallbackQueryHandler(setting_format,pattern=r"^setting_format$")); app.add_handler(CallbackQueryHandler(setting_quality,pattern=r"^setting_quality$"))
    app.add_handler(CallbackQueryHandler(favorites,pattern=r"^favorites$")); app.add_handler(CallbackQueryHandler(favorite_add,pattern=r"^favorite:add$")); app.add_handler(CallbackQueryHandler(follow_add,pattern=r"^follow:add$")); app.add_handler(CallbackQueryHandler(downloads,pattern=r"^downloads$")); app.add_handler(CallbackQueryHandler(direct_read,pattern=r"^read:\d+$"))
    app.add_handler(CallbackQueryHandler(new_search,pattern=r"^new_search$")); app.add_handler(CallbackQueryHandler(admin_panel,pattern=r"^admin_panel$")); app.add_handler(CallbackQueryHandler(noop,pattern=r"^noop$")); app.add_handler(CallbackQueryHandler(custom_format,pattern=r"^customfmt:(?:pdf|cbz)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,range_message))
    app.add_error_handler(error_handler); return app


def main() -> None:
    app=build_application(); logger.info("Bot starting"); app.run_polling(allowed_updates=Update.ALL_TYPES,drop_pending_updates=True)

if __name__=="__main__": main()
