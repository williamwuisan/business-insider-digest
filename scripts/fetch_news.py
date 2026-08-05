"""
Fetches business news RSS feeds (domestic + global), clusters same-topic
articles from the lookback window, asks Claude to write one summary per
cluster, and writes the result to data/digest.json for the static site.

Run in GitHub Actions on a schedule. Requires ANTHROPIC_API_KEY in the
environment. Safe to re-run: if every feed fails, the previous digest.json
is left untouched instead of being wiped out.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from anthropic import Anthropic
from dateutil import parser as dateutil_parser

REPO_ROOT = Path(__file__).resolve().parent.parent
DIGEST_PATH = REPO_ROOT / "data" / "digest.json"

LOOKBACK_HOURS = 15  # covers a 2x/day schedule with buffer for a missed run
MAX_ARTICLES_PER_CATEGORY = 60  # keep prompt/response size bounded on busy days
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MODEL = "claude-haiku-4-5-20251001"

FEEDS = {
    "domestic": [
        ("CNBC Indonesia", "https://www.cnbcindonesia.com/news/rss"),
        ("CNBC Indonesia", "https://www.cnbcindonesia.com/market/rss/"),
        ("CNN Indonesia", "https://www.cnnindonesia.com/ekonomi/rss"),
        ("CNN Indonesia", "https://www.cnnindonesia.com/nasional/rss"),
        ("Kontan", "https://investasi.kontan.co.id/rss"),
        ("Kontan", "https://nasional.kontan.co.id/rss"),
    ],
    "global": [
        ("CNBC International", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
        ("CNBC International", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ],
}


def entry_published(entry):
    if getattr(entry, "published_parsed", None):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if getattr(entry, "updated_parsed", None):
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if raw:
        try:
            dt = dateutil_parser.parse(raw)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    return None


def fetch_category(category, cutoff):
    articles = []
    for source_name, url in FEEDS[category]:
        try:
            # Fetch with `requests` (not feedparser's own opener) — some of these
            # sites serve an error/challenge page instead of the real feed to
            # bare urllib requests even with a matching User-Agent header.
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
                timeout=15,
            )
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.bozo and not parsed.entries:
                print(f"  [skip] {source_name} ({url}): {parsed.bozo_exception}", file=sys.stderr)
                continue
        except Exception as exc:
            print(f"  [skip] {source_name} ({url}): {exc}", file=sys.stderr)
            continue

        kept = 0
        for entry in parsed.entries:
            published = entry_published(entry)
            if published and published < cutoff:
                continue
            articles.append({
                "source": source_name,
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", ""),
                "snippet": (entry.get("summary", "") or "")[:400],
            })
            kept += 1
        print(f"  [ok] {source_name} ({url}): {kept} recent items", file=sys.stderr)

    if len(articles) > MAX_ARTICLES_PER_CATEGORY:
        articles = articles[:MAX_ARTICLES_PER_CATEGORY]
    return articles


def build_prompt(category_label, articles):
    numbered = "\n".join(
        f"{i+1}. [{a['source']}] {a['title']} — {a['snippet']} (url: {a['url']})"
        for i, a in enumerate(articles)
    )
    return f"""Berikut adalah daftar berita bisnis/ekonomi {category_label} dari beberapa jam terakhir:

{numbered}

Tugas kamu:
1. Kelompokkan berita yang membahas topik/saham/perusahaan yang sama menjadi satu cluster (misalnya beberapa berita berbeda tentang saham BBCA jadi satu cluster).
2. Berita yang topiknya berdiri sendiri (tidak ada berita lain yang mirip) tetap jadi satu cluster sendiri.
3. Untuk tiap cluster, tulis SATU ringkasan singkat berbahasa Indonesia (2-4 kalimat, gaya jurnalistik netral, jangan mengarang fakta yang tidak ada di judul/snippet sumber).
4. Beri "tag" pendek tiap cluster (contoh: "Saham • BBCA", "Makroekonomi", "The Fed", "Teknologi").
5. Sertakan SEMUA url sumber yang termasuk cluster tersebut.

Balas HANYA dengan JSON array valid, tanpa markdown fence, format persis:
[
  {{
    "tag": "...",
    "title": "judul ringkas untuk cluster ini",
    "summary": "...",
    "sources": [{{"name": "...", "url": "..."}}]
  }}
]

Jika daftar berita di atas kosong, balas dengan array kosong: []"""


def summarize(client, category, category_label, articles):
    if not articles:
        return []
    prompt = build_prompt(category_label, articles)
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        clusters = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"  [error] could not parse Claude response for {category}: {exc}", file=sys.stderr)
        print(text, file=sys.stderr)
        return None  # signals "something went wrong", distinct from "genuinely no articles"

    for c in clusters:
        c["category"] = category
    return clusters


def load_previous_items(category):
    if not DIGEST_PATH.exists():
        return []
    try:
        previous = json.loads(DIGEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [i for i in previous.get("items", []) if i.get("category") == category]


def main():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    print("Fetching domestic feeds...", file=sys.stderr)
    domestic_articles = fetch_category("domestic", cutoff)
    print("Fetching global feeds...", file=sys.stderr)
    global_articles = fetch_category("global", cutoff)

    if not domestic_articles and not global_articles:
        print("No articles fetched from any feed; leaving existing digest.json untouched.", file=sys.stderr)
        return

    client = Anthropic()

    print(f"Summarizing {len(domestic_articles)} domestic articles...", file=sys.stderr)
    domestic_items = summarize(client, "domestic", "dalam negeri (Indonesia)", domestic_articles)
    print(f"Summarizing {len(global_articles)} global articles...", file=sys.stderr)
    global_items = summarize(client, "global", "luar negeri / global", global_articles)

    if domestic_items is None:
        print("  [fallback] keeping previous domestic items (summarization failed this run)", file=sys.stderr)
        domestic_items = load_previous_items("domestic")
    if global_items is None:
        print("  [fallback] keeping previous global items (summarization failed this run)", file=sys.stderr)
        global_items = load_previous_items("global")

    digest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "items": domestic_items + global_items,
    }

    DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_PATH.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(digest['items'])} digest items to {DIGEST_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
