# Telegram Manga Bot

بوت Telegram للبحث عن المانجا عبر MangaDex وقراءة الفصول.

## المميزات
- Inline Search مثل `@اسم_البوت naruto`.
- عرض تفاصيل المانجا وآخر 10 فصول مترجمة بالعربية أو الإنجليزية.
- تحميل صفحات الفصل وإرسالها كـ Telegram media groups.
- تخزين دائم لـ Telegram `file_id` باستخدام SQLite، بحيث يبقى الكاش بعد إعادة تشغيل البوت.
- `BOT_TOKEN` محفوظ في متغير بيئة وليس داخل الكود.
- جاهز للتشغيل عبر Docker.

## التشغيل محليًا

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
python main.py
```

على Windows PowerShell:

```powershell
$env:BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
python main.py
```

## Docker

```bash
docker build -t telegram-manga-bot .
docker run -d --name manga-bot -e BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN" -v manga-data:/app/data telegram-manga-bot
```

إذا أردت حفظ قاعدة الكاش في مجلد مخصص، عيّن `CACHE_DB_PATH`، مثل `/app/data/manga_cache.sqlite3`.

## ملاحظة
استخدام MangaDex وحقوق نشر الفصول يخضع لسياسات MangaDex والقوانين المحلية. البوت هنا يعرض المحتوى المتاح عبر MangaDex ولا يتجاوز أنظمة الوصول أو الحماية.
