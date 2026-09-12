from __future__ import annotations

import asyncio
import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
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
AZORA_BASE_URL = os.getenv("AZORA_BASE_URL", "https://azorafly.com").rstrip("/")
MANGASWAT_BASE_URL = os.getenv("MANGASWAT_BASE_URL", "https://mangaswat.com").rstrip("/")
USER_AGENT = os.getenv("SCRAPER_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
REQUEST_TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT", "30"))
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


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _absolute(base: str, value: str | None) -> str | None:
    return urljoin(base.rstrip("/") + "/", value) if value else None


def _chapter_number(text: str) -> str:
    match = re.search(r"(?:chapter|chap|ch\.?|الفصل)\s*([0-9]+(?:[.,][0-9]+)?)", text, flags=re.I)
    if match:
        return match.group(1).replace(",", ".")
    match = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)", text)
    return match.group(1).replace(",", ".") if match else "?"


def _chapter_sort_key(item: ChapterResult):
    try:
        return (0, float(item.number))
    except ValueError:
        return (1, item.number.lower())


async def _httpx_get(url: str, *, params=None, headers=None) -> httpx.Response:
    last_error: Exception | None = None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    request_headers.update(headers or {})
    timeout = httpx.Timeout(REQUEST_TIMEOUT, connect=min(15, REQUEST_TIMEOUT))
    for attempt in range(RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=request_headers) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt + 1 < RETRIES:
                await asyncio.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"HTTP request failed: {url}") from last_error


async def _curl_get(url: str, *, referer: str | None = None):
    """Browser-like HTTP client for modern sites; it does not solve CAPTCHA."""
    if CurlAsyncSession is None:
        response = await _httpx_get(url, headers={"Referer": referer} if referer else None)
        return response.text, response.headers
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.8,en;q=0.6",
    }
    if referer:
        headers["Referer"] = referer
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            async with CurlAsyncSession(impersonate="chrome", headers=headers) as session:
                response = await session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}")
                return response.text, response.headers
        except Exception as exc:
            last_error = exc
            if attempt + 1 < RETRIES:
                await asyncio.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Web request failed: {url}") from last_error


def _mangadex_cover(data: dict) -> str | None:
    for relation in data.get("relationships", []):
        if relation.get("type") == "cover_art":
            filename = relation.get("attributes", {}).get("fileName")
            if filename:
                return f"https://uploads.mangadex.org/covers/{data.get('id')}/{filename}"
    return None


async def _search_mangadex(query: str, limit: int) -> list[MangaResult]:
    params = [("title", query), ("limit", str(min(limit, 100))), ("order[relevance]", "desc")]
    response = await _httpx_get(f"{MANGADEX_API}/manga", params=params)
    results = []
    for data in response.json().get("data", []):
        attrs = data.get("attributes", {})
        title_map = attrs.get("title", {})
        title = title_map.get("en") or next(iter(title_map.values()), None) or "Untitled"
        results.append(MangaResult(_clean(title), "MangaDex", f"{MANGADEX_BASE}/title/{data['id']}", _mangadex_cover(data)))
    return results


async def _get_mangadex_chapters(manga_url: str) -> list[ChapterResult]:
    manga_id = urlparse(manga_url).path.rstrip("/").split("/")[-1]
    params = [("manga[]", manga_id), ("limit", "100"), ("order[chapter]", "desc"), ("order[volume]", "desc")]
    response = await _httpx_get(f"{MANGADEX_API}/chapter", params=params)
    chapters = []
    for item in response.json().get("data", []):
        attrs = item.get("attributes", {})
        chapters.append(ChapterResult(_clean(attrs.get("chapter")) or "?", _clean(attrs.get("title")), f"{MANGADEX_BASE}/chapter/{item['id']}", "MangaDex"))
    return chapters


def _generic_manga_results(html: str, base_url: str, limit: int) -> list[MangaResult]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = ["article a[href]", ".row.c-tabs-item__content a[href]", ".c-tabs-item__content a[href]", ".page-item-detail a[href]", ".manga-item a[href]", ".manga-list a[href]", "h2 a[href]", "h3 a[href]", "h4 a[href]"]
    results: list[MangaResult] = []
    seen: set[str] = set()
    for selector in selectors:
        for link in soup.select(selector):
            href = _absolute(base_url, link.get("href"))
            if not href or href in seen or href.rstrip("/") == base_url.rstrip("/"):
                continue
            text = _clean(link.get_text(" ", strip=True))
            image = link.find("img") or (link.parent.find("img") if link.parent else None)
            title = text or _clean(image.get("alt") if image else "")
            if not title or len(title) < 2 or len(title) > 180:
                continue
            cover = _absolute(base_url, image.get("data-src") or image.get("data-lazy-src") or image.get("src")) if image else None
            seen.add(href)
            results.append(MangaResult(title, "", href, cover))
            if len(results) >= limit:
                return results
    return results


async def _search_site(base_url: str, query: str, source: str, limit: int) -> list[MangaResult]:
    html, _ = await _curl_get(f"{base_url}/?s={quote_plus(query)}", referer=base_url + "/")
    return [MangaResult(r.title, source, r.url, r.cover_url) for r in _generic_manga_results(html, base_url, limit)]


async def search_manga(query: str, limit: int = 10) -> list[MangaResult]:
    query = _clean(query)
    if not query:
        return []
    gathered = await asyncio.gather(
        _search_mangadex(query, limit),
        _search_site(AZORA_BASE_URL, query, "Azora", limit),
        _search_site(MANGASWAT_BASE_URL, query, "MangaSwat", limit),
        return_exceptions=True,
    )
    results: list[MangaResult] = []
    seen: set[tuple[str, str]] = set()
    for source_results in gathered:
        if isinstance(source_results, Exception):
            continue
        for item in source_results:
            key = (item.source.lower(), item.url.rstrip("/").lower())
            if key not in seen:
                seen.add(key)
                results.append(item)
    q = query.casefold()
    results.sort(key=lambda item: (0 if item.title.casefold() == q else 1))
    return results[:limit]


def _generic_chapters(html: str, manga_url: str, source: str) -> list[ChapterResult]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [".wp-manga-chapter a[href]", ".listing-chapters_wrap a[href]", ".chapter-item a[href]", ".chapters-list a[href]", "a[href*='/chapter/']", "a[href*='/chapters/']"]
    chapters: list[ChapterResult] = []
    seen: set[str] = set()
    for selector in selectors:
        for link in soup.select(selector):
            href = _absolute(manga_url, link.get("href"))
            if not href or href in seen:
                continue
            text = _clean(link.get_text(" ", strip=True))
            number = _chapter_number(text + " " + href)
            if number == "?":
                continue
            seen.add(href)
            chapters.append(ChapterResult(number, text, href, source))
    if not chapters:
        for link in soup.select("a[href]"):
            href = _absolute(manga_url, link.get("href"))
            text = _clean(link.get_text(" ", strip=True))
            if href and href not in seen and re.search(r"(chapter|chap|ch\.?|الفصل)", f"{text} {href}", re.I):
                number = _chapter_number(text + " " + href)
                if number != "?":
                    seen.add(href)
                    chapters.append(ChapterResult(number, text, href, source))
    unique = {}
    for chapter in chapters:
        unique.setdefault(chapter.url, chapter)
    return sorted(unique.values(), key=_chapter_sort_key, reverse=True)


async def get_chapters(manga_url: str, source: str | None = None) -> list[ChapterResult]:
    source = source or _source_from_url(manga_url)
    if source.lower() == "mangadex":
        return await _get_mangadex_chapters(manga_url)
    base = MANGASWAT_BASE_URL if source.lower() == "mangaswat" else AZORA_BASE_URL
    html, _ = await _curl_get(manga_url, referer=base + "/")
    return _generic_chapters(html, manga_url, source)


def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "mangadex" in host:
        return "MangaDex"
    if "mangaswat" in host:
        return "MangaSwat"
    return "Azora"


def _generic_images(html: str, chapter_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    images: list[str] = []
    seen: set[str] = set()
    for image in soup.select("img"):
        raw = image.get("data-src") or image.get("data-lazy-src") or image.get("data-original") or image.get("src")
        url = _absolute(chapter_url, raw)
        if not url or url in seen:
            continue
        alt = _clean(image.get("alt"))
        classes = " ".join(image.get("class", []))
        probe = f"{url} {alt} {classes}".lower()
        if any(token in probe for token in ("logo", "avatar", "icon", "banner", "advert")):
            continue
        if not re.search(r"\.(?:jpe?g|png|webp|gif|avif)(?:\?.*)?$", url, re.I):
            continue
        seen.add(url)
        images.append(url)
        if len(images) >= MAX_IMAGES:
            break
    return images


async def _mangadex_image_urls(chapter_url: str) -> list[str]:
    chapter_id = urlparse(chapter_url).path.rstrip("/").split("/")[-1]
    response = await _httpx_get(f"{MANGADEX_API}/at-home/server/{chapter_id}")
    payload = response.json()
    base = payload["baseUrl"]
    chapter = payload["chapter"]
    quality = os.getenv("MANGADEX_IMAGE_QUALITY", "data")
    if quality not in {"data", "data-saver"}:
        quality = "data"
    return [f"{base}/{quality}/{chapter['hash']}/{filename}" for filename in chapter.get("data", [])]


async def get_chapter_images(chapter_url: str, source: str | None = None) -> list[str]:
    source = source or _source_from_url(chapter_url)
    if source.lower() == "mangadex":
        return await _mangadex_image_urls(chapter_url)
    base = MANGASWAT_BASE_URL if source.lower() == "mangaswat" else AZORA_BASE_URL
    html, _ = await _curl_get(chapter_url, referer=base + "/")
    return _generic_images(html, chapter_url)


async def _download_bytes(client, url: str, referer: str) -> bytes:
    last_error: Exception | None = None
    headers = {"Referer": referer, "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"}
    for attempt in range(RETRIES):
        try:
            response = await client.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            content = response.content
            if len(content) < 100:
                raise RuntimeError("Downloaded image is unexpectedly small")
            return content
        except Exception as exc:
            last_error = exc
            if attempt + 1 < RETRIES:
                await asyncio.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Image download failed: {url}") from last_error


def _write_pdf(image_bytes_list: list[bytes], output_path: str) -> int:
    prepared: list[bytes] = []
    try:
        for raw in image_bytes_list:
            with Image.open(io.BytesIO(raw)) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=92, optimize=True)
                prepared.append(buffer.getvalue())
        with open(output_path, "wb") as pdf:
            pdf.write(img2pdf.convert(prepared))
        return len(prepared)
    finally:
        prepared.clear()


async def download_chapter_as_pdf(chapter_url: str, source: str | None = None) -> tuple[str, int]:
    source = source or _source_from_url(chapter_url)
    image_urls = await get_chapter_images(chapter_url, source=source)
    if not image_urls:
        raise RuntimeError("لم يتم العثور على صور صفحات الفصل.")
    referer = chapter_url
    image_bytes: list[bytes] = []
    try:
        if source.lower() == "mangadex":
            async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=15), follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
                for url in image_urls:
                    image_bytes.append(await _download_bytes(client, url, referer))
        elif CurlAsyncSession is not None:
            async with CurlAsyncSession(impersonate="chrome", headers={"User-Agent": USER_AGENT, "Referer": referer}) as client:
                for url in image_urls:
                    image_bytes.append(await _download_bytes(client, url, referer))
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=15), follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
                for url in image_urls:
                    image_bytes.append(await _download_bytes(client, url, referer))

        with tempfile.NamedTemporaryFile(prefix="manga_", suffix=".pdf", delete=False) as tmp:
            output_path = tmp.name
        try:
            count = await asyncio.to_thread(_write_pdf, image_bytes, output_path)
            return output_path, count
        except Exception:
            Path(output_path).unlink(missing_ok=True)
            raise
    finally:
        image_bytes.clear()
