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
MAX_IMAGES = max(1, int(os.getenv("MAX_CHAPTER_IMAGES", "300"))
@Manga