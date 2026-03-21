#!/usr/bin/env python3
"""
Intel Blog Crawler
Fetches RSS feeds, deduplicates, generates TL;DR via OpenRouter,
categorizes, scores, extracts media, and saves to SQLite.
"""

import feedparser
import sqlite3
import hashlib
import json
import re
import os
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
from typing import Optional
import httpx
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DB_PATH = os.getenv("DB_PATH", "./intel_blog.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REQUEST_TIMEOUT = 20
DELAY_BETWEEN_FEEDS = 2  # seconds, be polite

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("crawler.log"),
    ],
)
log = logging.getLogger(__name__)

# ── Feed Registry ────────────────────────────────────────────────────────────
FEEDS = [
    # --- a16z family ---
    {"name": "a16z Policy",   "url": "https://a16zpolicy.substack.com/feed",   "category_hint": "policy"},
    {"name": "a16z Crypto",   "url": "https://a16zcrypto.substack.com/feed",   "category_hint": "crypto"},
    {"name": "a16z Speedrun", "url": "https://speedrun.substack.com/feed",     "category_hint": "founders"},
    {"name": "a16z News",     "url": "https://www.a16z.news/feed",             "category_hint": "vc"},
    {"name": "a16z Blog",     "url": "https://a16z.com/feed/",                 "category_hint": "vc"},
    # --- AI Research ---
    {"name": "Import AI",        "url": "https://importai.substack.com/feed",              "category_hint": "ai-research"},
    {"name": "Interconnects",    "url": "https://www.interconnects.ai/feed",               "category_hint": "ai-research"},
    {"name": "Ahead of AI",      "url": "https://magazine.sebastianraschka.com/feed",      "category_hint": "ai-research"},
    {"name": "HuggingFace Blog", "url": "https://huggingface.co/blog/feed.xml",            "category_hint": "ai-research"},
    {"name": "AI Supremacy",     "url": "https://aisupremacy.substack.com/feed",           "category_hint": "ai-research"},
    {"name": "One Useful Thing", "url": "https://www.oneusefulthing.org/feed",             "category_hint": "ai-research"},
    {"name": "Latent Space",     "url": "https://www.latent.space/feed",                   "category_hint": "ai-research"},
    # --- VC & Investing ---
    {"name": "The Generalist",   "url": "https://thegeneralist.substack.com/feed",         "category_hint": "vc"},
    {"name": "Not Boring",       "url": "https://www.notboring.co/feed",                   "category_hint": "vc"},
    {"name": "First Round Review","url": "https://review.firstround.com/rss.xml",          "category_hint": "founders"},
    {"name": "Sequoia Articles", "url": "https://www.sequoiacap.com/articles/feed/",       "category_hint": "vc"},
    {"name": "CB Insights",      "url": "https://www.cbinsights.com/research/feed/",       "category_hint": "vc"},
    # --- MENA / Gulf ---
    {"name": "Wamda",            "url": "https://wamda.com/feed",                          "category_hint": "mena"},
]

# ── Categories & Tags ────────────────────────────────────────────────────────
CATEGORIES = [
    "ai-research", "llm-models", "vc-deals", "market-maps",
    "crypto-web3", "policy-reg", "founders", "mena-gulf",
    "frontier-tech", "enterprise-ai", "consumer", "deep-tech",
    "infra-devtools", "product-growth", "safety-alignment",
    "open-source", "investing-ops",
]

# ── Database ─────────────────────────────────────────────────────────────────
def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash      TEXT UNIQUE NOT NULL,
            source        TEXT NOT NULL,
            title         TEXT NOT NULL,
            url           TEXT NOT NULL,
            raw_summary   TEXT,
            tldr          TEXT,
            full_text     TEXT,
            category      TEXT,
            tags          TEXT,        -- JSON array
            score         INTEGER,     -- 1-10
            must_read     INTEGER DEFAULT 0,
            image_url     TEXT,
            images        TEXT,        -- JSON array of all images
            author        TEXT,
            published_at  TEXT,
            ingested_at   TEXT DEFAULT (datetime('now')),
            ai_processed  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS crawl_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT,
            fetched     INTEGER DEFAULT 0,
            new_articles INTEGER DEFAULT 0,
            errors      INTEGER DEFAULT 0,
            ran_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_published   ON articles(published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_source      ON articles(source);
        CREATE INDEX IF NOT EXISTS idx_category    ON articles(category);
        CREATE INDEX IF NOT EXISTS idx_score       ON articles(score DESC);
        CREATE INDEX IF NOT EXISTS idx_must_read   ON articles(must_read);
        CREATE INDEX IF NOT EXISTS idx_ai_processed ON articles(ai_processed);
    """)
    conn.commit()
    return conn


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()[:16]


# ── Media Extraction ──────────────────────────────────────────────────────────
def extract_images_from_html(html: str, base_url: str = "") -> list[str]:
    """Pull all img src values from HTML, resolve relative URLs."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    imgs = []
    for tag in soup.find_all("img"):
        src = tag.get("src") or tag.get("data-src") or ""
        if src:
            if src.startswith("http"):
                imgs.append(src)
            elif base_url:
                imgs.append(urljoin(base_url, src))
    # also og:image from meta
    for meta in soup.find_all("meta", property="og:image"):
        content = meta.get("content", "")
        if content and content not in imgs:
            imgs.insert(0, content)
    return list(dict.fromkeys(imgs))  # deduplicate preserving order


def pick_best_image(images: list[str]) -> Optional[str]:
    """Prefer images that look like article thumbnails (not icons/logos)."""
    if not images:
        return None
    for img in images:
        low = img.lower()
        if any(skip in low for skip in ["logo", "icon", "avatar", "badge", "pixel", "1x1", "tracking"]):
            continue
        if any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
            return img
    return images[0] if images else None


def fetch_og_image(url: str) -> Optional[str]:
    """Fetch the article page and extract og:image."""
    try:
        r = httpx.get(url, timeout=REQUEST_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; IntelBotCrawler/1.0)"})
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og:
            return og.get("content")
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw:
            return tw.get("content")
    except Exception:
        pass
    return None


# ── OpenRouter AI ─────────────────────────────────────────────────────────────
def ai_enrich(title: str, summary: str, category_hint: str) -> dict:
    """
    Call OpenRouter to generate TL;DR, pick category/tags, score the article.
    Falls back to sensible defaults if API call fails.
    """
    default = {
        "tldr": summary[:200] if summary else title,
        "category": category_hint or "ai-research",
        "tags": [category_hint] if category_hint else ["ai-research"],
        "score": 5,
        "must_read": False,
    }

    if not OPENROUTER_API_KEY:
        log.warning("OPENROUTER_API_KEY not set — skipping AI enrichment")
        return default

    categories_str = ", ".join(CATEGORIES)
    prompt = f"""You are an AI analyst for a tech/VC intelligence blog. Analyze this article and return ONLY valid JSON (no markdown, no extra text).

Title: {title}
Summary: {summary[:600] if summary else 'N/A'}
Source hint: {category_hint}

Return exactly this JSON structure:
{{
  "tldr": "<one punchy sentence, max 140 chars, no jargon>",
  "category": "<pick ONE from: {categories_str}>",
  "tags": ["<tag1>", "<tag2>", "<tag3>"],
  "score": <integer 1-10, where 10 = must-read for a tech founder/investor>,
  "must_read": <true if score >= 8, else false>
}}

Scoring guide:
- 9-10: Landmark analysis, major model release, significant funding, paradigm shift
- 7-8: Strong insight, useful framework, notable deal
- 5-6: Good context, industry update
- 3-4: Minor update, incremental news
- 1-2: Low signal, generic content

Tags should be short kebab-case phrases relevant to the article."""

    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://intel-blog.app",
                "X-Title": "Intel Blog Crawler",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemma-3-12b-it:free",  # free model on OpenRouter
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.2,
            },
            timeout=30,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        # Validate / sanitise
        return {
            "tldr": str(data.get("tldr", default["tldr"]))[:200],
            "category": data.get("category", default["category"]) if data.get("category") in CATEGORIES else default["category"],
            "tags": [str(t)[:40] for t in data.get("tags", default["tags"])][:5],
            "score": max(1, min(10, int(data.get("score", 5)))),
            "must_read": bool(data.get("must_read", False)),
        }
    except json.JSONDecodeError as e:
        log.warning(f"AI response JSON parse error: {e}")
    except Exception as e:
        log.warning(f"OpenRouter API error: {e}")

    return default


# ── RSS Parsing ───────────────────────────────────────────────────────────────
def clean_html(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    soup = BeautifulSoup(html or "", "html.parser")
    return re.sub(r"\s+", " ", soup.get_text()).strip()


def parse_date(entry) -> str:
    """Extract published date from feed entry."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def crawl_feed(feed_meta: dict, conn: sqlite3.Connection) -> tuple[int, int]:
    """
    Fetch one feed, parse entries, save new ones.
    Returns (fetched_count, new_count).
    """
    source = feed_meta["name"]
    url = feed_meta["url"]
    category_hint = feed_meta.get("category_hint", "ai-research")

    log.info(f"Crawling: {source}")
    fetched = 0
    new = 0

    try:
        feed = feedparser.parse(
            url,
            agent="Mozilla/5.0 (compatible; IntelBotCrawler/1.0)",
            request_headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
        )
        if feed.bozo and not feed.entries:
            log.warning(f"  Feed parse error for {source}: {feed.bozo_exception}")
            return 0, 0

        for entry in feed.entries[:20]:  # max 20 per crawl
            fetched += 1
            article_url = entry.get("link", "").strip()
            if not article_url:
                continue

            h = url_hash(article_url)

            # Deduplication check
            existing = conn.execute("SELECT id FROM articles WHERE url_hash = ?", (h,)).fetchone()
            if existing:
                continue

            title = clean_html(entry.get("title", "Untitled"))
            raw_summary = entry.get("summary") or entry.get("description") or ""
            full_text = ""

            # Try content first (some feeds include full article)
            if hasattr(entry, "content"):
                for c in entry.content:
                    if c.get("type", "").startswith("text"):
                        full_text = c.get("value", "")
                        break

            content_html = full_text or raw_summary

            # Extract images from feed content
            images = extract_images_from_html(content_html, article_url)

            # If no images in feed, try fetching OG image (skip for high-volume sources)
            if not images and source not in ("CB Insights",):
                og = fetch_og_image(article_url)
                if og:
                    images = [og]

            image_url = pick_best_image(images)
            clean_summary = clean_html(raw_summary)

            # AI enrichment
            enriched = ai_enrich(title, clean_summary, category_hint)

            author = ""
            if hasattr(entry, "author"):
                author = entry.author
            elif hasattr(entry, "authors") and entry.authors:
                author = entry.authors[0].get("name", "")

            conn.execute("""
                INSERT OR IGNORE INTO articles
                    (url_hash, source, title, url, raw_summary, tldr, full_text,
                     category, tags, score, must_read, image_url, images,
                     author, published_at, ai_processed)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
            """, (
                h, source, title, article_url,
                clean_summary[:2000], enriched["tldr"], clean_html(full_text)[:5000],
                enriched["category"], json.dumps(enriched["tags"]),
                enriched["score"], int(enriched["must_read"]),
                image_url, json.dumps(images[:10]),
                author, parse_date(entry),
            ))
            new += 1
            log.info(f"  + [{enriched['score']}/10] {title[:70]}")

            # Rate limit AI calls
            if OPENROUTER_API_KEY:
                time.sleep(0.5)

        conn.commit()

    except Exception as e:
        log.error(f"  ERROR crawling {source}: {e}", exc_info=True)
        return fetched, new

    return fetched, new


# ── Main Entry ────────────────────────────────────────────────────────────────
def run():
    log.info("=" * 60)
    log.info(f"Intel Blog Crawler starting — {datetime.now().isoformat()}")
    log.info("=" * 60)

    conn = init_db(DB_PATH)
    total_fetched = total_new = total_errors = 0

    for feed_meta in FEEDS:
        try:
            fetched, new = crawl_feed(feed_meta, conn)
            total_fetched += fetched
            total_new += new

            conn.execute("""
                INSERT INTO crawl_log (source, fetched, new_articles, errors)
                VALUES (?, ?, ?, 0)
            """, (feed_meta["name"], fetched, new))
            conn.commit()

        except Exception as e:
            log.error(f"Fatal error on feed {feed_meta['name']}: {e}")
            total_errors += 1

        time.sleep(DELAY_BETWEEN_FEEDS)

    log.info("=" * 60)
    log.info(f"Done. Fetched: {total_fetched} | New: {total_new} | Errors: {total_errors}")
    log.info("=" * 60)
    conn.close()


if __name__ == "__main__":
    run()
