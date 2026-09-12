# Telegram Manga Bot

بوت تيليجرام للبحث عن المانجا عبر MangaDex واستعراض أحدث الفصول وإرسال الصفحات كألبومات صور.

## التشغيل
1. أنشئ البوت عبر BotFather وفعّل Inline Mode.
2. `pip install -r requirements.txt`
3. اضبط `BOT_TOKEN` كمتغير بيئة.
4. شغّل `python main.py`.

## Docker
`docker build -t manga-bot .` ثم `docker run --rm -e BOT_TOKEN="$BOT_TOKEN" manga-bot`

## ملاحظات
التخزين المؤقت لـ Telegram `file_id` داخل الذاكرة ويختفي عند إعادة التشغيل. احترم حقوق النشر وشروط MangaDex وTelegram.
