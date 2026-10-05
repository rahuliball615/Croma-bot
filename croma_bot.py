# croma_bot.py — cloudscraper version
import cloudscraper
from bs4 import BeautifulSoup
import json
import os
import random
from datetime import datetime
from pathlib import Path

PRODUCTS = [
    {
        "name": "iPhone 16 Pro 128GB",
        "url": "https://www.croma.com/apple-iphone-16-pro-128gb-desert-titanium-/p/309265",
        "target_price": 110000,
        "notify_on_stock": True,
    },
    {
        "name": "PS5 Slim",
        "url": "https://www.croma.com/sony-playstation-5-slim-1tb-/p/302312",
        "target_price": 45000,
        "notify_on_stock": True,
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = Path("croma_state.json")

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# cloudscraper session ek hi baar banate hain, reuse karte hain
SCRAPER = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=5,
)

def fetch(url):
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        r = SCRAPER.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            log(f"[+] Got page ({len(r.text)} bytes)")
            return r.text
        log(f"[!] HTTP {r.status_code}")
    except Exception as e:
        log(f"[!] Fetch error: {e}")
    return None

def parse_product(html):
    soup = BeautifulSoup(html, "html.parser")
    result = {"price": None, "in_stock": False, "name": ""}

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") == "Product":
                result["name"] = data.get("name", "")
                offers = data.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                price = offers.get("price")
                if price:
                    result["price"] = int(float(price))
                avail = offers.get("availability", "")
                result["in_stock"] = "InStock" in avail
                return result
        except (json.JSONDecodeError, AttributeError, IndexError):
            continue

    price_el = soup.select_one('[class*="pdpPrice"], .amount, [data-testid="price"]')
    if price_el:
        digits = "".join(c for c in price_el.get_text() if c.isdigit())
        if digits:
            result["price"] = int(digits)

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
        log("[!] Telegram not configured")
        return
    try:
        r = requests_post_telegram(msg)
        log(f"[+] Telegram sent: {r}")
    except Exception as e:
        log(f"[!] Telegram error: {e}")

def requests_post_telegram(msg):
    import requests
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=10,
    )
    return r.status_code

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
    log(f"    price=₹{price} stock={in_stock}")

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
