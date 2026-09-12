from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup
from PIL import Image


@dataclass(frozen=True)
class MangaResult:
    title: str
    url: str
    cover_url: str | None = None


@dataclass(frozen=True)
class ChapterResult:
    number: str
    title: str
    url: str


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# Current Azora domain. MangaSwat can be supplied through MANGASWAT_BASE_URL.
AZORA_BASE_URL = "https://azorafly.com"
MANGASWAT_BASE_URL = "https://mangaswat.com"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _absolute(base: str, value: str | None) -> str | None:
    if not value:
        return None
    return urljoin(base, value)


def _chapter_number(text: str) -> str:
    match = re.search(r"(?:chapter|الفصل|ch\.?)[^0-9]*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    return match.group(1) if match else "?"


class MangaWebScraper:
    def __init__(
        self,
        base_url: str,
        search_url_template: str | None = None,
        timeout: float = 25,
        retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.search_url_template = search_url_template
        self.timeout = timeout
        self.retries = retries
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.8,en;q=0.5",
            "Referer": self.base_url + "/",
            "Cache-Control": "no-cache",
        }

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = await client.get(url, headers=self.headers, follow_redirects=True)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"تعذر الوصول إلى الموقع: {self.base_url}") from last_error

    def _search_url(self, query: str) -> str:
        if self.search_url_template:
            return self.search_url_template.format(query=quote_plus(query))
        # Azora/WordPress-style sites commonly accept /?s=query; override with env if needed.
        return f"{self.base_url}/?s={quote_plus(query)}"

    async def search(self, query: str, limit: int = 10) -> list[MangaResult]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await self._get(client, self._search_url(query))
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[MangaResult] = []
        seen: set[str] = set()

        selectors = [
            "article a[href]", ".row.c-tabs-item__content a[href]", ".page-item-detail a[href]",
            ".manga-item a[href]", ".c-tabs-item__content a[href]", "h2 a[href]", "h3 a[href]",
        ]
        for selector in selectors:
            for link in soup.select(selector):
                href = _absolute(self.base_url, link.get("href"))
                if not href or href in seen or href.rstrip("/") == self.base_url:
                    continue
                title = _clean(link.get_text(" "))
                if not title:
                    image = link.find("img")
                    title = _clean((image.get("alt") if image else "") or "")
                if not title or len(title) < 2:
                    continue
                image = link.find("img") or (link.parent.find("img") if link.parent else None)
                cover = None
                if image:
                    cover = _absolute(self.base_url, image.get("data-src") or image.get("src"))
                seen.add(href)
                results.append(MangaResult(title=title[:200], url=href, cover_url=cover))
                if len(results) >= limit:
                    return results
        return results

    async def chapters(self, manga_url: str) -> list[ChapterResult]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await self._get(client, manga_url)
        soup = BeautifulSoup(response.text, "html.parser")
        chapters: list[ChapterResult] = []
        seen: set[str] = set()
        selectors = [
            ".wp-manga-chapter a[href]", ".listing-chapters_wrap a[href]", ".chapter-item a[href]",
            ".chapters-list a[href]", "a[href*='/chapter/']", "a[href*='/chapters/']",
        ]
        for selector in selectors:
            for link in soup.select(selector):
                href = _absolute(manga_url, link.get("href"))
                if not href or href in seen:
                    continue
                text = _clean(link.get_text(" "))
                number = _chapter_number(text + " " + href)
                if number == "?":
                    continue
                title = text[:180]
                seen.add(href)
                chapters.append(ChapterResult(number=number, title=title, url=href))
        chapters.sort(key=lambda item: float(item.number) if item.number.replace(".", "", 1).isdigit() else -1, reverse=True)
        return chapters

    async def chapter_images(self, chapter_url: str) -> list[str]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await self._get(client, chapter_url)
        soup = BeautifulSoup(response.text, "html.parser")
        images: list[str] = []
        seen: set[str] = set()
        for image in soup.select("img"):
            src = image.get("data-src") or image.get("data-lazy-src") or image.get("src")
            if not src:
                continue
            absolute = _absolute(chapter_url, src)
            if not absolute or absolute in seen:
                continue
            alt = _clean(image.get("alt", ""))
            classes = " ".join(image.get("class", []))
            if any(token in (alt + " " + classes).lower() for token in ["logo", "avatar", "icon", "banner", "advert"]):
                continue
            if not re.search(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", absolute, re.I) and "image" not in classes.lower():
                continue
            seen.add(absolute)
            images.append(absolute)
        return images


async def search_manga(query: str, source: str = "azora") -> list[MangaResult]:
    source = source.lower()
    if source == "mangaswat":
        base = MANGASWAT_BASE_URL
        template = None
    else:
        base = AZORA_BASE_URL
        template = None
    scraper = MangaWebScraper(base, template)
    return await scraper.search(query)


async def get_chapters(manga_url: str, source: str = "azora") -> list[ChapterResult]:
    base = MANGASWAT_BASE_URL if source.lower() == "mangaswat" else AZORA_BASE_URL
    return await MangaWebScraper(base).chapters(manga_url)


async def get_chapter_images(chapter_url: str, source: str = "azora") -> list[str]:
    base = MANGASWAT_BASE_URL if source.lower() == "mangaswat" else AZORA_BASE_URL
    return await MangaWebScraper(base).chapter_images(chapter_url)


async def download_images_to_pdf(image_urls: list[str], output_path: str) -> int:
    if not image_urls:
        raise RuntimeError("لم يتم العثور على صور للفصل")
    timeout = httpx.Timeout(30.0, connect=15.0)
    headers = {"User-Agent": USER_AGENT, "Referer": AZORA_BASE_URL + "/"}
    files: list[Image.Image] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for index, url in enumerate(image_urls, start=1):
            response = None
            for attempt in range(3):
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    break
                except (httpx.HTTPError, OSError):
                    if attempt == 2:
                        response = None
                    else:
                        await asyncio.sleep(1.5 * (attempt + 1))
            if response is None:
                continue
            try:
                image = Image.open(BytesIO(response.content)).convert("RGB")
                files.append(image)
            except Exception:
                continue
    if not files:
        raise RuntimeError("تعذر تنزيل صور الفصل")
    first, *rest = files
    first.save(output_path, "PDF", save_all=True, append_images=rest, resolution=100.0)
    for image in files:
        image.close()
    return len(files)
