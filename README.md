# Telegram Manga Bot

بوت Telegram خاص بالمشرف للبحث عن المانجا وسحب الفصول وتحويلها إلى PDF مضغوط بجودة متوسطة.

## المزايا

- 🔒 وصول المشرف فقط عبر `ADMIN_CHAT_ID`.
- 🔎 اختيار مصدر البحث من أزرار داخل Telegram:
  - 🔵 سوات
  - 🔷 ازورا
  - 🟣 تيم اكس
  - 🟢 مانجاليك
  - 🌐 البحث في جميع المصادر
- 📚 عرض مصدر كل نتيجة.
- 📄 تنزيل فصل منفرد كـ PDF.
- 📦 تنزيل حزم مثل `1-30` و`31-60` و`61-90` في PDF واحد.
- 🗜️ ضغط الصور تلقائيًا بجودة متوسطة لتقليل حجم الـPDF.
- 🧹 حذف الملفات المؤقتة بعد إرسالها.
- 🐳 يعمل محليًا أو عبر Docker.

## التشغيل محليًا

```bash
git clone https://github.com/shoamv56-dot/telegram-manga-bot.git
cd telegram-manga-bot
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

في Windows أنشئ `.env` يدويًا إذا لم يتوفر `cp`.

## متغيرات البيئة

```env
BOT_TOKEN=...
ADMIN_CHAT_ID=...
SWAT_BASE_URL=https://mangaswat.com
AZORA_BASE_URL=https://azorafly.com
TEAMX_BASE_URL=https://www.olympustaff.com
MANGALIKE_BASE_URL=https://like-manga.net
PDF_JPEG_QUALITY=65
PDF_MAX_WIDTH=1600
PDF_MAX_HEIGHT=2400
MAX_PDF_BYTES=51380224
```

لا تضع `BOT_TOKEN` في GitHub أو داخل الكود.

## طريقة الاستخدام

1. أرسل `/start`.
2. اختر المصدر: سوات، ازورا، تيم اكس، مانجاليك، أو الكل.
3. أرسل اسم المانجا.
4. اختر العمل من النتائج.
5. ستظهر حزم الفصول تلقائيًا حسب الموجود، مثل:
   - `📦 تنزيل 1 - 30`
   - `📦 تنزيل 31 - 60`
   - `📦 تنزيل 61 - 90`
6. أو اختر فصلًا منفردًا.
7. البوت يحول الصفحات إلى PDF مضغوط بجودة متوسطة ثم يرسله ويحذف الملف المؤقت.

## ملاحظات مهمة

- حجم الحزمة يعتمد على عدد الصفحات وحجمها الأصلي. إذا تجاوز الـPDF الحد المحدد في `MAX_PDF_BYTES` سيطلب البوت استخدام حزمة أصغر.
- جودة الضغط الافتراضية متوسطة: JPEG quality 65 مع حد أقصى 1600×2400 للصورة.
- مواقع المانجا قد تغير HTML أو تمنع الطلبات الآلية. في هذه الحالة يحتاج الـscraper إلى تحديث selectors أو رابط المصدر.
- `curl_cffi` يستخدم توافقًا شبيهًا بالمتصفح للطلبات؛ لا يستخدم لتجاوز CAPTCHA أو ضوابط الوصول.
- مصادر Team X وMangaLike وAzora وSwat قابلة لتغيير النطاق من `.env` دون تعديل الكود.

## Docker

```bash
docker build -t telegram-manga-bot .
docker run --env-file .env telegram-manga-bot
```

## Telegram Bot API

البوت يستخدم long polling، لذلك لا يحتاج إلى فتح Port عام أو Webhook.
