# croma_bot.py — v2 with multi-site, history, offers, urgent mode
import cloudscraper
from bs4 import BeautifulSoup
import json, os, re, requests
from datetime import datetime
from pathlib import Path
from io import BytesIO
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PRODUCTS = [
    {
        "name": "iPhone 17 Pro 256GB Deep Blue",
        "url": "https://www.croma.com/apple-iphone-17-pro-256gb-deep-blue-/p/317418",
        "target_price": 120000,
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
URGENT_THRESHOLD = 0.05  # 5% of target = urgent

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

SCRAPER = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=5,
)

# ============ PARSERS ============

def parse_croma(html):
    soup = BeautifulSoup(html, "html.parser")
    r = {"price": None, "in_stock": False, "name": "", "valid": False, "offers": []}
    if soup.title and (soup.title.string or "").startswith("Buy "):
        r["valid"] = True
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "{}")
            if isinstance(d, list): d = d[0]
            if d.get("@type") == "Product":
                r["name"] = d.get("name", "")
                off = d.get("offers") or {}
                if isinstance(off, list): off = off[0] if off else {}
                p = off.get("price")
                if p and str(p) != "undefined":
                    r["price"] = int(float(str(p).replace(",", "")))
                a = str(off.get("availability", ""))
                r["in_stock"] = "InStock" in a
                if r["price"]: break
        except Exception: continue
    if not r["price"]:
        m = re.search(r'"price"\s*:\s*"?(\d{3,7})', html)
        if m: r["price"] = int(m.group(1))
    if not r["in_stock"]:
        if soup.select_one('button[class*="addToCart"], button[class*="pdpAddToCart"]'):
            r["in_stock"] = True
    for el in soup.find_all(string=re.compile(r"Bank Offer|Flat ₹|Get ₹|Cashback", re.I)):
        t = " ".join(el.strip().split())[:160]
        if t and t not in r["offers"]:
            r["offers"].append(t)
        if len(r["offers"]) >= 5: break
    return r

def parse_amazon(html):
    soup = BeautifulSoup(html, "html.parser")
    r = {"price": None, "in_stock": False, "name": "", "valid": False, "offers": []}
    t = soup.title.string if soup.title else ""
    if "Amazon" in t and "Page Not Found" not in t:
        r["valid"] = True
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "{}")
            if isinstance(d, list): d = d[0]
            if d.get("@type") == "Product":
                r["name"] = d.get("name", "")
                off = d.get("offers") or {}
                if isinstance(off, list): off = off[0] if off else {}
                p = off.get("price")
                if p: r["price"] = int(float(str(p).replace(",", "")))
                a = str(off.get("availability", ""))
                r["in_stock"] = "InStock" in a
        except Exception: continue
    if not r["price"]:
        el = soup.select_one(".a-price-whole, #priceblock_ourprice, .a-offscreen")
        if el:
            digits = "".join(c for c in el.get_text() if c.isdigit())
            if 3 < len(digits) < 8: r["price"] = int(digits)
    if not r["name"]:
        el = soup.select_one("#productTitle")
        if el: r["name"] = el.get_text().strip()
    if not r["in_stock"]:
        if soup.select_one("#add-to-cart-button"):
            r["in_stock"] = True
    return r

def parse_flipkart(html):
    soup = BeautifulSoup(html, "html.parser")
    r = {"price": None, "in_stock": False, "name": "", "valid": False, "offers": []}
    if soup.title and "Flipkart" in (soup.title.string or ""):
        r["valid"] = True
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "{}")
            if isinstance(d, list): d = d[0]
            if d.get("@type") == "Product":
                r["name"] = d.get("name", "")
                off = d.get("offers") or {}
                p = off.get("price")
                if p: r["price"] = int(float(str(p).replace(",", "")))
                a = str(off.get("availability", ""))
                r["in_stock"] = "InStock" in a
        except Exception: continue
    if not r["price"]:
        el = soup.select_one("._30jeq3, ._16Jk6d")
        if el:
            digits = "".join(c for c in el.get_text() if c.isdigit())
            if 3 < len(digits) < 8: r["price"] = int(digits)
    if not r["in_stock"]:
        if soup.select_one("button._2KpZ6l._2U9uOA"):
            r["in_stock"] = True
    return r

PARSERS = {
    "croma.com": parse_croma,
    "amazon.in": parse_amazon,
    "flipkart.com": parse_flipkart,
}

def get_parser(url):
    for domain, p in PARSERS.items():
        if domain in url: return p
    return None

def fetch(url):
    try:
        r = SCRAPER.get(url, headers={"Accept-Language": "en-IN,en;q=0.9"}, timeout=30)
        if r.status_code == 200: return r.text
        log(f"[!] HTTP {r.status_code}")
    except Exception as e:
        log(f"[!] fetch: {e}")
    return None

# ============ STATE ============

def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            if not isinstance(data, dict) or "products" not in data:
                # purana format ya corrupt — fresh start
                return {"products": {}, "last_run": None}
            return data
        except Exception:
            pass
    return {"products": {}, "last_run": None}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def add_history(state, url, price):
    if not price: return
    rec = state["products"].setdefault(url, {})
    hist = rec.get("history", [])
    today = datetime.now().strftime("%Y-%m-%d")
    hist = [h for h in hist if h.get("date") != today]
    hist.append({"date": today, "price": price})
    rec["history"] = hist[-30:]

# ============ GRAPH ============

def make_graph(history, name):
    if len(history) < 2: return None
    dates = [h["date"][5:] for h in history]
    prices = [h["price"] for h in history]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(dates, prices, marker="o", color="#d32f2f", linewidth=2)
    ax.set_title(f"{name[:40]} — Price Trend")
    ax.set_ylabel("Price (Rs)")
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=90)
    plt.close(fig)
    buf.seek(0)
    return buf.read()

# ============ TELEGRAM ============

def send_message(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("[!] Telegram secrets missing"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=15,
        )
        log(f"[+] TG msg: {r.status_code}")
    except Exception as e:
        log(f"[!] TG err: {e}")

def send_photo(png_bytes, caption):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("graph.png", png_bytes, "image/png")},
            timeout=30,
        )
        log(f"[+] TG photo: {r.status_code}")
    except Exception as e:
        log(f"[!] TG photo err: {e}")

# ============ LOGIC ============

def is_urgent(product, price):
    if not price or not product.get("target_price"): return False
    return price <= product["target_price"] * (1 + URGENT_THRESHOLD)

def check_product(product, state):
    name = product["name"]; url = product["url"]
    prev = state["products"].get(url, {})
    prev_price = prev.get("price"); prev_stock = prev.get("in_stock", False)

    parser = get_parser(url)
    if not parser:
        log(f"[!] No parser for {url}"); return

    log(f"[*] {name}")
    html = fetch(url)
    if not html:
        log(f"[!] fetch failed"); return

    info = parser(html)
    if not info["valid"]:
        log(f"[!] not a product page"); return

    price = info["price"]; in_stock = info["in_stock"]
    log(f"    Rs{price} stock={in_stock} offers={len(info['offers'])}")

    add_history(state, url, price)

    if product["notify_on_stock"] and in_stock and not prev_stock:
        msg = f"🔔 <b>{name}</b>\n✅ Back in stock!\nPrice: ₹{price or 0:,}\n{url}"
        if info["offers"]:
            msg += "\n\n💳 Offers:\n" + "\n".join(f"• {o}" for o in info["offers"])
        send_message(msg)

    if price and prev_price and price < prev_price:
        save = prev_price - price
        msg = (f"🔔 <b>{name}</b>\n📉 Down ₹{save:,}\n"
               f"Was: ₹{prev_price:,}\nNow: ₹{price:,}\n{url}")
        send_message(msg)

    if price and product.get("target_price") and price <= product["target_price"]:
        msg = (f"🎯 <b>{name}</b>\nTarget hit! (≤ ₹{product['target_price']:,})\n"
               f"Now: ₹{price:,}\n{url}")
        if info["offers"]:
            msg += "\n\n💳 Offers:\n" + "\n".join(f"• {o}" for o in info["offers"])
        send_message(msg)

    state["products"][url] = {
        "name": name, "price": price, "in_stock": in_stock,
        "last_checked": datetime.now().isoformat(),
        "history": prev.get("history", []),
        "offers": info["offers"],
    }

def main():
    log("=" * 40)
    state = load_state()
    now = datetime.now()

    urgent_now = any(
        is_urgent(p, state["products"].get(p["url"], {}).get("price"))
        for p in PRODUCTS
    )

    last = state.get("last_run")
    if last and not urgent_now:
        try:
            delta = (now - datetime.fromisoformat(last)).total_seconds()
            if delta < 9 * 60:
                log(f"[~] Throttled ({int(delta)}s). Skip.")
                return
        except Exception:
            pass

    log(f"[*] Urgent mode: {urgent_now}")

    for p in PRODUCTS:
        try: check_product(p, state)
        except Exception as e: log(f"[!] {p['name']}: {e}")

    state["last_run"] = now.isoformat()
    save_state(state)

    if now.weekday() == 6:
        for p in PRODUCTS:
            hist = state["products"].get(p["url"], {}).get("history", [])
            png = make_graph(hist, p["name"])
            if png:
                send_photo(png, f"📊 <b>{p['name']}</b> — 30-day trend")

    log("Done.")

if __name__ == "__main__":
    main()
