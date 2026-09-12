# Telegram Manga Bot

بوت تليجرام شخصي للبحث في مواقع المانجا العربية وسحب الفصول مباشرة من الموقع ثم تحويل صفحات الفصل إلى PDF وإرساله للمستخدم.

## المزايا
- `/search اسم المانجا` للبحث المباشر في الموقع.
- عرض نتائج البحث كأزرار.
- سحب قائمة الفصول من صفحة المانجا.
- تنزيل صور الفصل مباشرة من صفحة القراءة.
- تحويل الصور إلى PDF مؤقتاً ثم إرسال الملف وحذفه بعد الإرسال.
- `httpx + BeautifulSoup4` مع User-Agent وTimeout وRetry.
- مصدر Azora مفعّل افتراضياً، ويمكن تبديله إلى MangaSwat.

## إعداد البيئة

### المتغيرات
- `BOT_TOKEN`: توكن BotFather.
- `ADMIN_USER_ID`: اختياري، رقم Telegram ID للأدمن. إذا لم تضفه يصبح `/search` متاحاً للجميع.
- `SCRAPER_SOURCE`: `azora` افتراضياً أو `mangaswat`.

### تشغيل محلي
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
export ADMIN_USER_ID="YOUR_TELEGRAM_USER_ID"
python main.py
```

PowerShell:
```powershell
$env:BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
$env:ADMIN_USER_ID="YOUR_TELEGRAM_USER_ID"
python main.py
```

### Docker
```bash
docker build -t telegram-manga-bot .
docker run -d --name manga-bot -e BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN" -e ADMIN_USER_ID="YOUR_TELEGRAM_USER_ID" telegram-manga-bot
```

## ملاحظات عن المواقع
- `scraper.py` يعتمد على HTML الفعلي للموقع، لذلك قد تحتاج CSS selectors إلى تحديث إذا غيّر الموقع تصميمه.
- نطاق Azora الحالي مضبوط على `https://azorafly.com`.
- نطاق MangaSwat مضبوط على `https://mangaswat.com`.
- لا يستخدم المشروع أدوات لتجاوز Cloudflare أو CAPTCHA. إذا كان الموقع يمنع الطلبات الآلية، يجب احترام الحماية أو استخدام API/وسيلة وصول يسمح بها الموقع.
- لا تضع `BOT_TOKEN` داخل GitHub أو داخل الكود.
