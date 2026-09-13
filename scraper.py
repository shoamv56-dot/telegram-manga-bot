from __future__ import annotations

import asyncio
import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
import img2pdf
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
except ImportError:
    CurlAsyncSession = None

MANGADEX_API = "https://api.mangadex.org"
MANGADEX_BASE = "https://mangadex.org"
SWAT_BASE_URL = os.getenv("SWAT_BASE_URL", os.getenv("MANGASWAT_BASE_URL", "https://mangaswat.com")).rstrip("/")
AZORA_BASE_URL = os.getenv("AZORA_BASE_URL", "https://azorafly.com").rstrip("/")
TEAMX_BASE_URL = os.getenv("TEAMX_BASE_URL", "https://teamxmanga.com").rstrip("/")
MANGALIKE_BASE_URL = os.getenv("MANGALIKE_BASE_URL", "https://like-manga.net").rstrip("/")
SOURCES = {"swat": SWAT_BASE_URL, "azora": AZORA_BASE_URL, "teamx": TEAMX_BASE_URL, "mangalike": MANGALIKE_BASE_URL}
USER_AGENT = os.getenv("SCRAPER_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36")
TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT", "30"))
RETRIES = max(1, int(os.getenv("SCRAPER_RETRIES", "3")))
MAX_IMAGES = max(1, int(os.getenv("MAX_CHAPTER_IMAGES", "300")))

@dataclass(frozen=True)
class MangaResult:
    title: str
    source: str
    url: str
    cover_url: str | None = None

@dataclass(frozen=True)
class ChapterResult:
    number: str
    title: str
    url: str
    source: str


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def absolute(base: str, value: str | None) -> str | None:
    return urljoin(base.rstrip("/") + "/", value) if value else None


def chapter_number(text: str) -> str:
    m = re.search(r"(?:chapter|chap|ch\.?|الفصل)\s*([0-9]+(?:[.,][0-9]+)?)", text, re.I)
    if not m:
        m = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)", text)
    return m.group(1).replace(",", ".") if m else "?"


def source_key(source: str) -> str:
    s = (source or "").casefold()
    if "mangadex" in s: return "mangadex"
    if "swat" in s or "سوات" in s: return "swat"
    if "azora" in s or "ازورا" in s: return "azora"
    if "team" in s or "تيم" in s: return "teamx"
    if "like" in s or "ليك" in s or "لايك" in s: return "mangalike"
    return "azora"


def source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "mangadex" in host: return "MangaDex"
    if "swat" in host: return "سوات"
    if "azora" in host: return "ازورا"
    if "teamxmanga" in host or "olympustaff" in host: return "تيم اكس"
    if "like-manga" in host or "manga-like" in host or "mangalik" in host: return "مانجاليك"
    return "ازورا"


async def http_get(url: str, params=None, headers=None) -> httpx.Response:
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}; h.update(headers or {})
    last = None
    for attempt in range(RETRIES):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=min(15, TIMEOUT)), follow_redirects=True, headers=h) as c:
                r = await c.get(url, params=params); r.raise_for_status(); return r
        except (httpx.HTTPError, OSError) as e:
            last = e
            if attempt + 1 < RETRIES: await asyncio.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"HTTP request failed: {url}") from last


async def web_get(url: str, referer: str | None = None):
    if CurlAsyncSession is None:
        r = await http_get(url, headers={"Referer": referer} if referer else None); return r.text, r.headers
    h = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", "Accept-Language": "ar,en;q=0.8"}
    if referer: h["Referer"] = referer
    last = None
    for attempt in range(RETRIES):
        try:
            async with CurlAsyncSession(impersonate="chrome", headers=h) as c:
                r = await c.get(url, timeout=TIMEOUT, allow_redirects=True)
                if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code}")
                return r.text, r.headers
        except Exception as e:
            last = e
            if attempt + 1 < RETRIES: await asyncio.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Web request failed: {url}") from last


async def search_mangadex(query: str, limit: int) -> list[MangaResult]:
    r = await http_get(f"{MANGADEX_API}/manga", params=[("title", query), ("limit", str(min(limit, 100))), ("order[relevance]", "desc")])
    out = []
    for d in r.json().get("data", []):
        attrs = d.get("attributes", {}); titles = attrs.get("title", {}); title = titles.get("en") or next(iter(titles.values()), "Untitled")
        cover = None
        for rel in d.get("relationships", []):
            if rel.get("type") == "cover_art":
                fn = rel.get("attributes", {}).get("fileName")
                if fn: cover = f"https://uploads.mangadex.org/covers/{d['id']}/{fn}"
        out.append(MangaResult(clean(title), "MangaDex", f"{MANGADEX_BASE}/title/{d['id']}", cover))
    return out


def generic_manga_results(html: str, base: str, limit: int, source: str) -> list[MangaResult]:
    soup = BeautifulSoup(html, "html.parser"); out = []; seen = set()
    selectors = ["article a[href]", ".page-item-detail a[href]", ".manga-item a[href]", ".manga-list a[href]", "h2 a[href]", "h3 a[href]", "h4 a[href]"]
    for selector in selectors:
        for a in soup.select(selector):
            href = absolute(base, a.get("href"))
            if not href or href in seen or href.rstrip("/") == base.rstrip("/"): continue
            img = a.find("img") or (a.parent.find("img") if a.parent else None)
            title = clean(a.get_text(" ", strip=True)) or clean(img.get("alt") if img else "")
            if not (2 <= len(title) <= 180): continue
            cover = absolute(base, (img.get("data-src") or img.get("data-lazy-src") or img.get("src")) if img else None)
            seen.add(href); out.append(MangaResult(title, source, href, cover))
            if len(out) >= limit: return out
    return out


async def search_site(base: str, query: str, source: str, limit: int) -> list[MangaResult]:
    html, _ = await web_get(f"{base}/?s={quote_plus(query)}", base + "/")
    return generic_manga_results(html, base, limit, source)


async def search_manga(query: str, limit: int = 10, source: str = "all") -> list[MangaResult]:
    query = clean(query); source = source.lower().strip()
    if not query: return []
    tasks = []
    if source in {"all", "mangadex"}: tasks.append(search_mangadex(query, limit))
    if source in {"all", "swat"}: tasks.append(search_site(SWAT_BASE_URL, query, "سوات", limit))
    if source in {"all", "azora"}: tasks.append(search_site(AZORA_BASE_URL, query, "ازورا", limit))
    if source in {"all", "teamx"}: tasks.append(search_site(TEAMX_BASE_URL, query, "تيم اكس", limit))
    if source in {"all", "mangalike"}: tasks.append(search_site(MANGALIKE_BASE_URL, query, "مانجاليك", limit))
    if not tasks: return []
    gathered = await asyncio.gather(*tasks, return_exceptions=True); out=[]; seen=set()
    for items in gathered:
        if isinstance(items, Exception): continue
        for item in items:
            key=(item.source.lower(), item.url.rstrip("/").lower())
            if key not in seen: seen.add(key); out.append(item)
    q=query.casefold(); out.sort(key=lambda x: 0 if x.title.casefold()==q else 1)
    return out[:limit]


def generic_chapters(html: str, manga_url: str, source: str) -> list[ChapterResult]:
    soup=BeautifulSoup(html,"html.parser"); out=[]; seen=set()
    selectors=[".wp-manga-chapter a[href]", ".listing-chapters_wrap a[href]", ".chapter-item a[href]", ".chapters-list a[href]", "a[href*='/chapter/']", "a[href*='/chapters/']"]
    for selector in selectors:
        for a in soup.select(selector):
            href=absolute(manga_url,a.get("href")); text=clean(a.get_text(" ",strip=True))
            n=chapter_number(text+" "+(href or ""))
            if not href or href in seen or n=="?": continue
            seen.add(href); out.append(ChapterResult(n,text,href,source))
    if not out:
        for a in soup.select("a[href]"):
            href=absolute(manga_url,a.get("href")); text=clean(a.get_text(" ",strip=True)); probe=f"{text} {href}"
            if href and href not in seen and re.search(r"chapter|chap|ch\.?|الفصل",probe,re.I):
                n=chapter_number(probe)
                if n!="?": seen.add(href); out.append(ChapterResult(n,text,href,source))
    unique={x.url:x for x in out}
    return sorted(unique.values(), key=lambda x: float(x.number) if x.number.replace('.','',1).isdigit() else -1, reverse=True)


async def get_chapters(manga_url: str, source: str | None = None) -> list[ChapterResult]:
    source=source or source_from_url(manga_url)
    if source_key(source)=="mangadex":
        manga_id=urlparse(manga_url).path.rstrip("/").split("/")[-1]
        r=await http_get(f"{MANGADEX_API}/chapter", params=[("manga[]",manga_id),("limit","100"),("order[chapter]","desc"),("order[volume]","desc")])
        return [ChapterResult(clean(x.get("attributes",{}).get("chapter")) or "?", clean(x.get("attributes",{}).get("title")), f"{MANGADEX_BASE}/chapter/{x['id']}", "MangaDex") for x in r.json().get("data",[])]
    base=SOURCES.get(source_key(source), AZORA_BASE_URL); html,_=await web_get(manga_url,base+"/")
    return generic_chapters(html,manga_url,source)


async def chapter_images(url: str, source: str) -> list[str]:
    if source_key(source)=="mangadex":
        cid=urlparse(url).path.rstrip("/").split("/")[-1]; r=await http_get(f"{MANGADEX_API}/at-home/server/{cid}"); p=r.json(); q=os.getenv("MANGADEX_IMAGE_QUALITY","data"); q=q if q in {"data","data-saver"} else "data"
        return [f"{p['baseUrl']}/{q}/{p['chapter']['hash']}/{fn}" for fn in p['chapter'].get('data',[])]
    base=SOURCES.get(source_key(source),AZORA_BASE_URL); html,_=await web_get(url,base+"/"); soup=BeautifulSoup(html,"html.parser"); out=[]; seen=set()
    for img in soup.select("img"):
        raw=img.get("data-src") or img.get("data-lazy-src") or img.get("data-original") or img.get("src"); u=absolute(url,raw)
        probe=f"{u} {img.get('alt','')} {' '.join(img.get('class',[]))}".lower()
        if not u or u in seen or any(x in probe for x in ("logo","avatar","icon","banner","advert")) or not re.search(r"\.(?:jpe?g|png|webp|gif|avif)(?:\?.*)?$",u,re.I): continue
        seen.add(u); out.append(u)
        if len(out)>=MAX_IMAGES: break
    return out


async def download_bytes(client, url: str, referer: str) -> bytes:
    h={"Referer":referer,"Accept":"image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}; last=None
    for attempt in range(RETRIES):
        try:
            r=await client.get(url,headers=h,timeout=TIMEOUT)
            if r.status_code>=400: raise RuntimeError(f"HTTP {r.status_code}")
            if len(r.content)<100: raise RuntimeError("Downloaded image is unexpectedly small")
            return r.content
        except Exception as e:
            last=e
            if attempt+1<RETRIES: await asyncio.sleep(1.2*(attempt+1))
    raise RuntimeError(f"Image download failed: {url}") from last


def prepare_image(raw: bytes, path: str) -> None:
    quality=max(35,min(85,int(os.getenv("PDF_JPEG_QUALITY","65")))); mw=max(800,int(os.getenv("PDF_MAX_WIDTH","1600"))); mh=max(1000,int(os.getenv("PDF_MAX_HEIGHT","2400")))
    with Image.open(io.BytesIO(raw)) as im:
        im=ImageOps.exif_transpose(im).convert("RGB"); im.thumbnail((mw,mh),Image.Resampling.LANCZOS); im.save(path,"JPEG",quality=quality,optimize=True,progressive=True)


def write_pdf(paths: list[str], output: str) -> None:
    with open(output,"wb") as f: f.write(img2pdf.convert(paths))


async def chapter_to_files(chapter: ChapterResult, temp_dir: str) -> list[str]:
    urls=await chapter_images(chapter.url,chapter.source)
    if not urls: raise RuntimeError("لم يتم العثور على صور صفحات الفصل.")
    paths=[]
    if source_key(chapter.source)=="mangadex" or CurlAsyncSession is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT,connect=15),follow_redirects=True,headers={"User-Agent":USER_AGENT}) as c:
            for i,u in enumerate(urls):
                p=os.path.join(temp_dir,f"{len(paths):06d}.jpg"); prepare_image(await download_bytes(c,u,chapter.url),p); paths.append(p)
    else:
        async with CurlAsyncSession(impersonate="chrome",headers={"User-Agent":USER_AGENT,"Referer":chapter.url}) as c:
            for i,u in enumerate(urls):
                p=os.path.join(temp_dir,f"{len(paths):06d}.jpg"); prepare_image(await download_bytes(c,u,chapter.url),p); paths.append(p)
    return paths


async def download_chapter_as_pdf(chapter_url: str, source: str | None = None) -> tuple[str,int]:
    source=source or source_from_url(chapter_url); temp=tempfile.mkdtemp(prefix="manga_"); out=None
    try:
        paths=await chapter_to_files(ChapterResult("?","",chapter_url,source),temp)
        with tempfile.NamedTemporaryFile(prefix="manga_",suffix=".pdf",delete=False) as f: out=f.name
        await asyncio.to_thread(write_pdf,paths,out); return out,len(paths)
    except Exception:
        if out: Path(out).unlink(missing_ok=True)
        raise
    finally:
        for p in Path(temp).glob("*.jpg"): p.unlink(missing_ok=True)
        Path(temp).rmdir()


async def download_chapters_as_pdf(chapters: list[ChapterResult], progress: Callable[[int,int],Awaitable[None]]|None=None) -> tuple[str,int,int]:
    if not chapters: raise RuntimeError("لم يتم تحديد فصول للتنزيل.")
    temp=tempfile.mkdtemp(prefix="manga_batch_"); out=None; all_paths=[]; pages=0
    try:
        for done,ch in enumerate(chapters,1):
            paths=await chapter_to_files(ch,temp); all_paths.extend(paths); pages+=len(paths)
            if progress: await progress(done,len(chapters))
        with tempfile.NamedTemporaryFile(prefix="manga_batch_",suffix=".pdf",delete=False) as f: out=f.name
        await asyncio.to_thread(write_pdf,all_paths,out); return out,len(chapters),pages
    except Exception:
        if out: Path(out).unlink(missing_ok=True)
        raise
    finally:
        for p in Path(temp).glob("*.jpg"): p.unlink(missing_ok=True)
        Path(temp).rmdir()
