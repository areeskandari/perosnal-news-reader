#!/usr/bin/env python3
"""
Export SQLite data to JSON files consumed by the static frontend (web/public/data).
Run after crawler.py to keep the web/public/data/ directory fresh.
"""

import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "./intel_blog.db")
OUT_DIR = Path("../web/public/data")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def row_to_dict(row) -> dict:
    d = dict(row)
    # Parse JSON fields
    for field in ("tags", "images"):
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                d[field] = []
    d["must_read"] = bool(d.get("must_read", 0))
    return d


def export_all():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # --- articles.json (last 200 articles) ---
    rows = conn.execute("""
        SELECT id, source, title, url, tldr, category, tags, score,
               must_read, image_url, author, published_at, ingested_at
        FROM articles
        ORDER BY published_at DESC
        LIMIT 200
    """).fetchall()
    articles = [row_to_dict(r) for r in rows]

    with open(OUT_DIR / "articles.json", "w") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(articles)} articles")

    # --- sources.json ---
    sources = conn.execute("""
        SELECT source, COUNT(*) as count,
               MAX(published_at) as latest,
               ROUND(AVG(score), 1) as avg_score
        FROM articles
        GROUP BY source
        ORDER BY count DESC
    """).fetchall()
    with open(OUT_DIR / "sources.json", "w") as f:
        json.dump([dict(r) for r in sources], f, ensure_ascii=False, indent=2)

    # --- categories.json ---
    cats = conn.execute("""
        SELECT category, COUNT(*) as count,
               ROUND(AVG(score), 1) as avg_score
        FROM articles
        WHERE category IS NOT NULL
        GROUP BY category
        ORDER BY count DESC
    """).fetchall()
    with open(OUT_DIR / "categories.json", "w") as f:
        json.dump([dict(r) for r in cats], f, ensure_ascii=False, indent=2)

    # --- must_reads.json (top 20 must-reads) ---
    must = conn.execute("""
        SELECT id, source, title, url, tldr, category, tags, score,
               image_url, author, published_at
        FROM articles
        WHERE must_read = 1
        ORDER BY score DESC, published_at DESC
        LIMIT 20
    """).fetchall()
    with open(OUT_DIR / "must_reads.json", "w") as f:
        json.dump([row_to_dict(r) for r in must], f, ensure_ascii=False, indent=2)

    # --- stats.json ---
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_articles,
            COUNT(DISTINCT source) as total_sources,
            SUM(must_read) as must_reads,
            ROUND(AVG(score), 1) as avg_score,
            MAX(ingested_at) as last_updated
        FROM articles
    """).fetchone()
    with open(OUT_DIR / "stats.json", "w") as f:
        json.dump(dict(stats), f, ensure_ascii=False, indent=2)

    conn.close()
    print(f"Export complete → {OUT_DIR}")


if __name__ == "__main__":
    export_all()
