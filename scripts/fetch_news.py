"""
Fetches business news RSS feeds (domestic + global), clusters same-topic
articles from the lookback window, asks Claude to write one summary per
cluster, and writes the result to data/digest.json for the static site.

Run in GitHub Actions on a schedule. Requires ANTHROPIC_API_KEY in the
environment. Safe to re-run: if every feed fails, the previous digest.json
is left untouched instead of being wiped out.
"""

import json
import re
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
MIN_HOURS_BETWEEN_RUNS = 4  # skip if digest.json is already fresher than this
MAX_ARTICLES_PER_CATEGORY = 25  # keep worst-case (no clustering) output under max_tokens
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MODEL = "claude-haiku-4-5-20251001"


def google_news(site_query):
    # Indonesian news sites here block GitHub Actions' cloud IP ranges on
    # their own /rss endpoints (confirmed via direct testing: 403 from the
    # Actions runner, 200 from elsewhere). Routing through Google News RSS
    # (scoped to the same site with `site:`) reaches them instead. Per
    # Google's own feed terms this is for personal, non-commercial reading
    # use, which matches this project.
    return f"https://news.google.com/rss/search?q=when:1d+{site_query}&hl=id&gl=ID&ceid=ID:id"


FEEDS = {
    "domestic": [
        ("CNBC Indonesia", google_news("site:cnbcindonesia.com/market")),
        ("CNBC Indonesia", google_news("site:cnbcindonesia.com/news")),
        ("CNN Indonesia", google_news("site:cnnindonesia.com/ekonomi")),
        ("Kontan", google_news("site:investasi.kontan.co.id")),
        ("Kontan", google_news("site:nasional.kontan.co.id")),
        ("Bisnis.com", google_news("site:bisnis.com")),
        ("Investor.id", google_news("site:investor.id")),
    ],
    "global": [
        # CNBC's general "International Top News" feed (id 100727362) was dropped:
        # it's general-interest, not business-scoped, and let non-business items
        # (geopolitics, travel, etc.) through. Business News (10001147) stays.
        ("CNBC International", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ],
}

PERSONAL_TICKERS = ["ANTM", "BIPI", "WIFI", "BRMS", "BUMI", "SUPA", "BMRI"]
MAX_ARTICLES_PER_TICKER = 5


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
            title = entry.get("title", "").strip()
            # Google News appends " - <Publisher>" to every title; drop it
            # since we already track the source separately.
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            snippet = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "").strip()
            if snippet == title:
                snippet = ""
            articles.append({
                "source": source_name,
                "title": title,
                "url": entry.get("link", ""),
                "snippet": snippet[:400],
            })
            kept += 1
        print(f"  [ok] {source_name} ({url}): {kept} recent items", file=sys.stderr)

    if len(articles) > MAX_ARTICLES_PER_CATEGORY:
        articles = articles[:MAX_ARTICLES_PER_CATEGORY]
    return articles


SUBMIT_DIGEST_TOOL = {
    "name": "submit_digest",
    "description": "Submit the clustered, summarized news digest.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string", "description": "Tag pendek, contoh: 'Saham • BBCA', 'Makroekonomi'"},
                        "title": {"type": "string", "description": "Judul ringkas untuk cluster ini"},
                        "summary": {"type": "string", "description": "Ringkasan 2-4 kalimat berbahasa Indonesia"},
                        "sources": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "url": {"type": "string"},
                                },
                                "required": ["name", "url"],
                            },
                        },
                    },
                    "required": ["tag", "title", "summary", "sources"],
                },
            },
        },
        "required": ["clusters"],
    },
}


def build_prompt(category_label, articles):
    numbered = "\n".join(
        f"{i+1}. [{a['source']}] {a['title']} — {a['snippet']} (url: {a['url']})"
        for i, a in enumerate(articles)
    )
    return f"""Berikut adalah daftar berita bisnis/ekonomi {category_label} dari beberapa jam terakhir:

{numbered}

Definisi "relevan" di sini secara KETAT hanya: pergerakan/kinerja saham & emiten, kebijakan makroekonomi (suku bunga, inflasi, nilai tukar, APBN, pajak), pasar modal & regulasinya (IHSG, OJK, BEI), harga komoditas (minyak, emas), perbankan & institusi keuangan besar, atau berita korporasi besar yang menggerakkan pasar.

Tugas kamu:
1. BUANG/lewati berita yang TIDAK masuk definisi relevan di atas — termasuk politik, kriminal, olahraga, hiburan, gaya hidup, human interest, DAN JUGA profil/kisah inspiratif UMKM atau bisnis kecil bergaya feature (misalnya "Warung X Bertahan di Tengah Krisis", tips usaha, cerita pelaku UMKM) — itu bukan berita pasar/emiten dan tidak dihitung "ekonomi" untuk tujuan ini, walaupun berasal dari sumber/feed berlabel bisnis. Kalau ragu apakah suatu berita cukup relevan, LEBIH BAIK dibuang daripada dipaksakan masuk. Jangan buat cluster untuk berita yang dibuang.
2. Dari sisa berita yang genuinely relevan sesuai definisi di atas, kelompokkan yang membahas topik/saham/perusahaan yang sama menjadi satu cluster (misalnya beberapa berita berbeda tentang saham BBCA jadi satu cluster).
3. Berita yang topiknya berdiri sendiri (tidak ada berita lain yang mirip) tetap jadi satu cluster sendiri, selama masih soal saham/bisnis/ekonomi.
4. Untuk tiap cluster, tulis SATU ringkasan singkat berbahasa Indonesia, MAKSIMAL 2 kalimat pendek (jangan lebih dari ±50 kata total), gaya jurnalistik netral, jangan mengarang fakta yang tidak ada di judul/snippet sumber.
5. Beri "tag" pendek tiap cluster (contoh: "Saham • BBCA", "Makroekonomi", "The Fed", "Teknologi").
6. Sertakan SEMUA url sumber yang termasuk cluster tersebut.
7. Panggil tool submit_digest dengan hasilnya. Jika tidak ada berita saham/bisnis/ekonomi sama sekali, panggil dengan clusters: []."""


def build_personal_prompt(articles):
    numbered = "\n".join(
        f"{i+1}. [{a['ticker']}] [{a['source']}] {a['title']} — {a['snippet']} (url: {a['url']})"
        for i, a in enumerate(articles)
    )
    tickers_list = ", ".join(PERSONAL_TICKERS)
    return f"""Berikut adalah daftar berita seputar saham-saham berikut yang dipantau seorang investor: {tickers_list}.
Setiap baris sudah ditandai kode sahamnya di awal.

{numbered}

Tugas kamu:
1. Untuk SETIAP kode saham yang punya minimal satu berita di atas, gabungkan semua beritanya menjadi SATU ringkasan berbahasa Indonesia, MAKSIMAL 2 kalimat pendek (jangan lebih dari ±50 kata total), jangan mengarang fakta yang tidak ada di judul/snippet sumber.
2. Set "tag" persis sama dengan kode sahamnya (contoh: "ANTM"), jangan tambahkan teks lain di tag.
3. Kalau satu kode saham punya beberapa berita dengan sub-topik yang jelas berbeda, boleh dipecah jadi lebih dari satu cluster untuk kode saham yang sama.
4. Kode saham yang TIDAK punya berita sama sekali di atas, lewati saja (jangan dibuat cluster kosong/mengarang).
5. Sertakan SEMUA url sumber yang termasuk cluster tersebut.
6. Panggil tool submit_digest dengan hasilnya. Jika daftar berita di atas kosong, panggil dengan clusters: []."""


def call_submit_digest(client, label, prompt):
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        tools=[SUBMIT_DIGEST_TOOL],
        tool_choice={"type": "tool", "name": "submit_digest"},
        messages=[{"role": "user", "content": prompt}],
    )

    print(f"  [usage] {label}: stop_reason={response.stop_reason} output_tokens={response.usage.output_tokens}", file=sys.stderr)
    if response.stop_reason == "max_tokens":
        print(f"  [error] response for {label} hit max_tokens before finishing", file=sys.stderr)
        return None

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        print(f"  [error] no tool_use block in response for {label}", file=sys.stderr)
        return None

    return tool_use.input.get("clusters", [])


def summarize(client, category, category_label, articles):
    if not articles:
        return []
    prompt = build_prompt(category_label, articles)
    clusters = call_submit_digest(client, category, prompt)
    if clusters is None:
        return None
    for c in clusters:
        c["category"] = category
    return clusters


def fetch_personal(cutoff):
    articles = []
    for ticker in PERSONAL_TICKERS:
        url = google_news(f'"saham {ticker}"')
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}, timeout=15)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as exc:
            print(f"  [skip] {ticker}: {exc}", file=sys.stderr)
            continue

        kept = 0
        for entry in parsed.entries:
            if kept >= MAX_ARTICLES_PER_TICKER:
                break
            published = entry_published(entry)
            if published and published < cutoff:
                continue
            title = entry.get("title", "").strip()
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            snippet = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "").strip()
            if snippet == title:
                snippet = ""
            source_name = entry.get("source", {}).get("title") if isinstance(entry.get("source"), dict) else None
            articles.append({
                "ticker": ticker,
                "source": source_name or "Google News",
                "title": title,
                "url": entry.get("link", ""),
                "snippet": snippet[:400],
            })
            kept += 1
        print(f"  [ok] {ticker}: {kept} recent items", file=sys.stderr)
    return articles


def summarize_personal(client, articles):
    if not articles:
        return []
    prompt = build_personal_prompt(articles)
    clusters = call_submit_digest(client, "personal", prompt)
    if clusters is None:
        return None
    for c in clusters:
        c["category"] = "personal"
    return clusters


def load_previous_items(category):
    if not DIGEST_PATH.exists():
        return []
    try:
        previous = json.loads(DIGEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [i for i in previous.get("items", []) if i.get("category") == category]


def digest_age_hours():
    if not DIGEST_PATH.exists():
        return None
    try:
        data = json.loads(DIGEST_PATH.read_text(encoding="utf-8"))
        generated_at = datetime.fromisoformat(data["generated_at"])
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None
    return (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600


def main():
    # The workflow now runs on many candidate schedule slots (GitHub's cron
    # for this repo has proven unreliable at hitting exact times — see
    # fetch-news.yml), so most invocations should be no-ops. Skip the
    # (paid) AI summarization work entirely if we already have a recent
    # digest, rather than re-fetching every ~15 minutes.
    age = digest_age_hours()
    if age is not None and age < MIN_HOURS_BETWEEN_RUNS:
        print(f"digest.json is only {age:.1f}h old (< {MIN_HOURS_BETWEEN_RUNS}h) — skipping this run.", file=sys.stderr)
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    print("Fetching domestic feeds...", file=sys.stderr)
    domestic_articles = fetch_category("domestic", cutoff)
    print("Fetching global feeds...", file=sys.stderr)
    global_articles = fetch_category("global", cutoff)
    print("Fetching personal ticker feeds...", file=sys.stderr)
    personal_articles = fetch_personal(cutoff)

    if not domestic_articles and not global_articles and not personal_articles:
        print("No articles fetched from any feed; leaving existing digest.json untouched.", file=sys.stderr)
        return

    client = Anthropic()

    print(f"Summarizing {len(domestic_articles)} domestic articles...", file=sys.stderr)
    domestic_items = summarize(client, "domestic", "dalam negeri (Indonesia)", domestic_articles)
    print(f"Summarizing {len(global_articles)} global articles...", file=sys.stderr)
    global_items = summarize(client, "global", "luar negeri / global", global_articles)
    print(f"Summarizing {len(personal_articles)} personal-ticker articles...", file=sys.stderr)
    personal_items = summarize_personal(client, personal_articles)

    if domestic_items is None:
        print("  [fallback] keeping previous domestic items (summarization failed this run)", file=sys.stderr)
        domestic_items = load_previous_items("domestic")
    if global_items is None:
        print("  [fallback] keeping previous global items (summarization failed this run)", file=sys.stderr)
        global_items = load_previous_items("global")
    if personal_items is None:
        print("  [fallback] keeping previous personal items (summarization failed this run)", file=sys.stderr)
        personal_items = load_previous_items("personal")

    digest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "items": domestic_items + global_items + personal_items,
    }

    DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_PATH.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(digest['items'])} digest items to {DIGEST_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
