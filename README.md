# Telegram Manga Bot

بوت Telegram شخصي (Admin-Only) للبحث عن المانجا والمانهوا وتصفح الفصول وتحويل الفصل المختار إلى PDF وإرساله مباشرة إلى حساب المشرف.

## المزايا

- حماية شاملة: لا يستجيب إلا لمعرف `ADMIN_CHAT_ID`.
- البحث من MangaDex API وAzora وMangaSwat.
- أزرار تفاعلية لاختيار المانجا والفصل.
- تنزيل صور الفصل بالترتيب وتحويلها إلى PDF واحد.
- حذف ملف PDF المؤقت بعد إرساله.
- مهلات وإعادة محاولات للطلبات.
- دعم `curl_cffi` مع محاكاة متصفح Chrome لتحسين توافق HTTP مع المواقع الحديثة.
- لا يحاول حل CAPTCHA أو تجاوز أنظمة الوصول التي تمنع الأتمتة؛ إذا رفض الموقع الطلب يفشل المصدر مع بقاء بقية المصادر متاحة.

## التشغيل محلياً

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# عدّل .env وضع BOT_TOKEN و ADMIN_CHAT_ID
python main.py
```

في Windows PowerShell استخدم `py -m venv .venv` ثم `.\\.venv\\Scripts\\Activate.ps1`.

`python-dotenv` يقرأ ملف `.env` تلقائياً عند التشغيل المحلي. في الاستضافة يمكنك استخدام Environment Variables مباشرة.

## إعداد Telegram

1. افتح `@BotFather`.
2. استخدم `/newbot` وأنشئ البوت.
3. انسخ `BOT_TOKEN`.
4. احصل على معرف Telegram الرقمي لحسابك (`ADMIN_CHAT_ID`).
5. لا ترسل التوكن إلى GitHub أو لأي شخص.

## المتغيرات

| المتغير | مطلوب | الوصف |
|---|---|---|
| `BOT_TOKEN` | نعم | توكن البوت |
| `ADMIN_CHAT_ID` | نعم | Telegram numeric user/chat ID المسموح له |
| `AZORA_BASE_URL` | لا | نطاق Azora |
| `MANGASWAT_BASE_URL` | لا | نطاق MangaSwat |
| `SCRAPER_TIMEOUT` | لا | مهلة طلب HTTP بالثواني |
| `SCRAPER_RETRIES` | لا | عدد المحاولات |
| `MAX_CHAPTER_IMAGES` | لا | الحد الأقصى لصور الفصل |
| `MAX_PDF_BYTES` | لا | الحد الأقصى لحجم PDF قبل إرساله |
| `MANGADEX_IMAGE_QUALITY` | لا | `data` أو `data-saver` |
| `LOG_LEVEL` | لا | مستوى السجل |

## GitHub Secrets

من **Repository → Settings → Secrets and variables → Actions → New repository secret** أضف:

- `BOT_TOKEN`
- `ADMIN_CHAT_ID`

GitHub Secrets لا تجعل المتغيرات متاحة تلقائياً لخدمة استضافة خارج GitHub. عند استخدام Render/Railway/Koyeb، أضف نفس المتغيرات في إعدادات الخدمة نفسها.

## Docker

```bash
docker build -t telegram-manga-bot .
docker run -d --name telegram-manga-bot --restart unless-stopped \
  -e BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN" \
  -e ADMIN_CHAT_ID="YOUR_TELEGRAM_USER_ID" \
  telegram-manga-bot
```

## Render

المشروع يحتوي على `render.yaml` كإعداد أولي لـ Background Worker. اربط المستودع، أنشئ الخدمة، وأدخل `BOT_TOKEN` و`ADMIN_CHAT_ID` كـ Environment Variables ثم Deploy.

## الاستخدام

- `/start` — رسالة الترحيب.
- `/search One Piece` — بحث مباشر.
- أو أرسل اسم المانجا كنص عادي.
- اختر المانجا ثم الفصل.
- انتظر إنشاء PDF وسيُرسل الملف إلى المحادثة.

## ملاحظات

- الـPDF والبيانات المؤقتة لا يتم تخزينها بشكل دائم بواسطة البوت.
- بعد إرسال الـPDF يحاول البوت حذف الملف المؤقت فوراً.
- بعض المواقع تغيّر HTML أو تمنع الطلبات الآلية؛ عندها قد تحتاج محددات HTML محدثة في `scraper.py`.
- `curl_cffi` هنا لتحسين توافق HTTP مع المواقع التي تتطلب بصمة متصفح حديثة، وليس لتجاوز CAPTCHA أو التحايل على صلاحيات الوصول.
- احترم شروط استخدام المواقع وحقوق نشر المحتوى الذي تصل إليه.

## هيكل المشروع

```text
telegram-manga-bot/
├── main.py
├── scraper.py
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── render.yaml
└── README.md
```
