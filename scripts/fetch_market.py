"""
Fetches today's IHSG (Jakarta Composite Index) and gold prices (world spot +
Antam domestic) and writes data/market.json for the site's "IHSG & Emas" view.

Each of the three data points is fetched independently and wrapped so a
failure in one (e.g. harga-emas.org changing its markup) doesn't block the
other two — the field is just omitted from market.json rather than the
whole script crashing.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKET_PATH = REPO_ROOT / "data" / "market.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def yahoo_quote(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    meta = resp.json()["chart"]["result"][0]["meta"]
    value = meta["regularMarketPrice"]
    previous_close = meta["chartPreviousClose"]
    change_percent = (value - previous_close) / previous_close * 100
    return value, previous_close, change_percent


def fetch_ihsg():
    try:
        value, previous_close, change_percent = yahoo_quote("%5EJKSE")
        return {"value": value, "previous_close": previous_close, "change_percent": round(change_percent, 2)}
    except Exception as exc:
        print(f"  [error] IHSG: {exc}", file=sys.stderr)
        return None


def fetch_gold_world():
    try:
        value, previous_close, change_percent = yahoo_quote("GC=F")
        return {"value": value, "change_percent": round(change_percent, 2)}
    except Exception as exc:
        print(f"  [error] gold_world: {exc}", file=sys.stderr)
        return None


def parse_price_id(text):
    """'2.749.000' -> 2749000 (Indonesian thousands-separator format)."""
    return int(re.sub(r"[^\d]", "", text))


def fetch_gold_antam():
    try:
        resp = requests.get("https://harga-emas.org/", headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        table = soup.find("table", class_=lambda c: c and "GoldPriceTable_table" in c)
        if table is None:
            raise ValueError("gold price table not found")

        buy = None
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) >= 2 and cells[0].get_text(strip=True) == "1":
                buy = parse_price_id(cells[1].get_text(strip=True))
                break
        if buy is None:
            raise ValueError("1-gram row not found in gold price table")

        buyback = None
        wrapper = table.find_parent(class_=lambda c: c and "GoldPriceTable_container" in c)
        notes_text = wrapper.get_text(" ", strip=True) if wrapper else soup.get_text(" ", strip=True)
        match = re.search(r"pembelian kembali[:\s]*Rp\s*([\d.,]+)", notes_text, re.IGNORECASE)
        if match:
            buyback = parse_price_id(match.group(1))

        result = {"buy": buy}
        if buyback is not None:
            result["buyback"] = buyback
        return result
    except Exception as exc:
        print(f"  [error] gold_antam: {exc}", file=sys.stderr)
        return None


def main():
    market = {"generated_at": datetime.now(timezone.utc).astimezone().isoformat()}

    ihsg = fetch_ihsg()
    if ihsg:
        market["ihsg"] = ihsg
        print(f"  [ok] IHSG: {ihsg}", file=sys.stderr)

    gold_world = fetch_gold_world()
    if gold_world:
        market["gold_world"] = gold_world
        print(f"  [ok] gold_world: {gold_world}", file=sys.stderr)

    gold_antam = fetch_gold_antam()
    if gold_antam:
        market["gold_antam"] = gold_antam
        print(f"  [ok] gold_antam: {gold_antam}", file=sys.stderr)

    if len(market) == 1:  # only generated_at, nothing actually fetched
        print("No market data fetched; leaving existing market.json untouched.", file=sys.stderr)
        return

    MARKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKET_PATH.write_text(json.dumps(market, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote market.json to {MARKET_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
