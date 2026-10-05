# croma_bot.py — Final version with correct URLs
import cloudscraper
from bs4 import BeautifulSoup
import json
import os
import re
import requests
from datetime import datetime
from pathlib import Path

PRODUCTS = [
    {
        "name": "iPhone 17 Pro 256GB Deep Blue",
        "url": "https://www.croma.com/apple-iphone-17-pro-256gb-deep-blue-/p/317418",
        "target_price": 999999,
        "notify_on_stock": True,
    },
    {
        "name": "PS5 Slim 1TB Disc Edition",
        "url": "https://www.croma.com/sony-playstation-5-slim-1tb-ssd-gaming-console-white-/p/305985",
        "target_price": 50000,
        "notify_on_stock": True,
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = Path("croma_state.json")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

SCRAPER = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=5,
)

def fetch(url):
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        r = SCRAPER.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.text
        log(f"[!] HTTP {r.status_code}")
    except Exception as e:
        log(f"[!] Fetch error: {e}")
    return None

def parse_product(html):
    """
    Returns: {price, in_stock, name, valid_product}
    valid_product = True if this is a real product page
    """
    result = {"price": None, "in_stock": False, "name": "", "valid_product": False}
    soup = BeautifulSoup(html, "html.parser")

    # Validation: title must start with "Buy" for a real product page
    title = (soup.title.string or "") if soup.title else ""
    if title.startswith("Buy "):
        result["valid_product"] = True

    # Method 1: JSON-LD (primary)
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") == "Product":
                name = data.get("name", "")
                if name and name != "undefined":
                    result["name"] = name

                offers = data.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                price = offers.get("price")
                if price and str(price) != "undefined":
                    try:
                        result["price"] = int(float(str(price).replace(",", "")))
                    except ValueError:
                        pass

                avail = str(offers.get("availability", ""))
                if "InStock" in avail:
                    result["in_stock"] = True
                elif "OutOfStock" in avail:
                    result["in_stock"] = False

                if result["price"]:
                    return result
        except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
            continue

    # Method 2: Raw HTML regex — "price":"NNNNN"
    m = re.search(r'"price"\s*:\s*"?(\d{3,7})', html)
    if m:
        result["price"] = int(m.group(1))

    # Method 3: HTML selectors
    if not result["price"]:
        price_el = soup.select_one('[class*="pdpPrice"], [class*="priceValue"], [data-testid="price"]')
        if price_el:
            digits = "".join(c for c in price_el.get_text() if c.isdigit())
            if 3 < len(digits) < 8:
                result["price"] = int(digits)

    # Stock indicators
    if soup.select_one('button[class*="addToCart"], button[class*="pdpAddToCart"]'):
        result["in_stock"] = True
    if soup.find(string=lambda t: t and "out of stock" in t.lower()):
        result["in_stock"] = False

    return result

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def notify(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("[!] Telegram secrets missing")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        log(f"[+] Telegram sent: {r.status_code}")
    except Exception as e:
        log(f"[!] Telegram error: {e}")

def check_product(product, state):
    name = product["name"]
    url = product["url"]
    prev = state.get(url, {"price": None, "in_stock": False})

    log(f"[*] Checking: {name}")
    html = fetch(url)
    if not html:
        log(f"[!] Failed: {name}")
        return

    info = parse_product(html)
    price = info["price"]
    in_stock = info["in_stock"]
    found_name = info["name"]
    valid = info["valid_product"]

    if not valid:
        log(f"[!] NOT a product page — URL galat hai")
        return

    log(f"    found: {found_name[:60]}")
    log(f"    price=₹{price} stock={in_stock}")

    # Alerts
    if product["notify_on_stock"] and in_stock and not prev["in_stock"]:
        notify(f"🔔 <b>{name}</b>\n✅ Back in stock!\nPrice: ₹{price or 0:,}\n{url}")

    if price and prev["price"] and price < prev["price"]:
        notify(f"🔔 <b>{name}</b>\n📉 Price dropped (was ₹{prev['price']:,})\nNow: ₹{price:,}\n{url}")

    if price and product.get("target_price") and price <= product["target_price"]:
        notify(f"🔔 <b>{name}</b>\n🎯 Target hit (≤ ₹{product['target_price']:,})\nNow: ₹{price:,}\n{url}")

    state[url] = {
        "name": name,
        "price": price,
        "in_stock": in_stock,
        "last_checked": datetime.now().isoformat(),
    }

def main():
    log("=" * 50)
    log("Croma bot — GitHub Actions run")
    log("=" * 50)
    state = load_state()
    for p in PRODUCTS:
        check_product(p, state)
    save_state(state)
    log("Done.")

if __name__ == "__main__":
    main()
