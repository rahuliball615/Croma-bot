# croma_bot.py — v3 with Telegram commands
import cloudscraper
from bs4 import BeautifulSoup
import json, os, re, requests
from datetime import datetime
from pathlib import Path
from io import BytesIO
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Seed products — sirf pehli baar state empty hone pe use hoga
SEED_PRODUCTS = [
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
URGENT_THRESHOLD = 0.05

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
                return fresh_state()
            return data
        except Exception:
            pass
    return fresh_state()

def fresh_state():
    st = {"products": {}, "last_run": None, "last_update_id": 0}
    for p in SEED_PRODUCTS:
        st["products"][p["url"]] = {
            "name": p["name"],
            "target_price": p["target_price"],
            "notify_on_stock": p["notify_on_stock"],
            "price": None, "in_stock": False, "history": [], "offers": [],
        }
    return st

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

def product_list(state):
    """Returns list of (index, url, info) — 1-indexed"""
    return [(i+1, url, info) for i, (url, info) in enumerate(state["products"].items())]

def find_product(state, arg):
    """arg = index (1-based) or URL substring. Returns (url, info) or (None, None)."""
    plist = product_list(state)
    # try index
    try:
        idx = int(arg)
        for i, url, info in plist:
            if i == idx:
                return url, info
    except ValueError:
        pass
    # try url substring match
    for i, url, info in plist:
        if arg in url:
            return url, info
    return None, None

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

def tg_send_message(msg, chat_id=None):
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        log("[!] Telegram not configured"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        log(f"[+] TG msg -> {r.status_code}")
    except Exception as e:
        log(f"[!] TG err: {e}")

def tg_send_photo(png_bytes, caption, chat_id=None):
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid: return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            data={"chat_id": cid, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("graph.png", png_bytes, "image/png")},
            timeout=30,
        )
        log(f"[+] TG photo -> {r.status_code}")
    except Exception as e:
        log(f"[!] TG photo err: {e}")

def tg_get_updates(offset):
    if not TELEGRAM_BOT_TOKEN: return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 3, "allowed_updates": '["message"]'},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get("ok"): return []
        return data.get("result", [])
    except Exception as e:
        log(f"[!] getUpdates: {e}")
        return []

# ============ COMMAND HANDLERS ============

HELP_TEXT = """<b>Croma Bot — Commands</b>

/list — Sab products aur prices
/add &lt;url&gt; &lt;target&gt; — Naya product
/remove &lt;index&gt; — Product hatao
/target &lt;index&gt; &lt;price&gt; — Target badlo
/check — Abhi check karo
/graph &lt;index&gt; — Price chart bhejo
/help — Yeh list

<i>Index</i> /list se milega.
Products Croma, Amazon.in, Flipkart support karte hain."""

def cmd_list(state):
    if not state["products"]:
        tg_send_message("Abhi koi product track nahi ho raha.\n\n<i>/add &lt;url&gt; &lt;target&gt;</i> se add kar.")
        return
    lines = ["<b>Tracked products</b>"]
    for i, url, info in product_list(state):
        price = info.get("price")
        target = info.get("target_price")
        stock = "✅" if info.get("in_stock") else "❌"
        price_s = f"₹{price:,}" if price else "—"
        target_s = f"₹{target:,}" if target else "—"
        lines.append(f"\n<b>{i}. {info.get('name','?')[:50]}</b>")
        lines.append(f"{stock} Price: {price_s} | Target: {target_s}")
        lines.append(f"<code>{url}</code>")
    tg_send_message("\n".join(lines))

def cmd_add(state, args):
    if len(args) < 2:
        tg_send_message("Format: <code>/add &lt;url&gt; &lt;target_price&gt;</code>\n\nExample:\n<code>/add https://www.croma.com/.../p/123456 99999</code>")
        return
    url = args[0]
    try:
        target = int(args[1].replace(",", ""))
    except ValueError:
        tg_send_message("Target price number hona chahiye, jaise 99999.")
        return
    if not any(d in url for d in PARSERS):
        tg_send_message("Sirf Croma, Amazon.in ya Flipkart URL supported hain.")
        return
    if url in state["products"]:
        tg_send_message("Yeh URL pehle se tracked hai. <i>/list</i> dekh.")
        return

    # Quick verify — fetch and check
    tg_send_message("⏳ Checking URL...")
    html = fetch(url)
    if not html:
        tg_send_message("❌ URL se response nahi mila. Croma pe confirm kar ki link sahi hai.")
        return
    parser = get_parser(url)
    info = parser(html)
    if not info.get("valid"):
        tg_send_message("❌ Yeh valid product page nahi lagta. Croma pe search karke product page ka URL copy kar.")
        return

    name = info.get("name") or url.split("/p/")[-1][:30] or "New Product"
    state["products"][url] = {
        "name": name[:80],
        "target_price": target,
        "notify_on_stock": True,
        "price": info.get("price"),
        "in_stock": info.get("in_stock", False),
        "history": [],
        "offers": info.get("offers", []),
    }
    add_history(state, url, info.get("price"))
    price_s = f"₹{info['price']:,}" if info.get("price") else "—"
    tg_send_message(f"✅ Added:\n<b>{name[:60]}</b>\nCurrent: {price_s} | Target: ₹{target:,}")

def cmd_remove(state, args):
    if not args:
        tg_send_message("Format: <code>/remove &lt;index&gt;</code>\n\n<i>/list</i> se index dekh.")
        return
    url, info = find_product(state, args[0])
    if not url:
        tg_send_message("Product nahi mila. <i>/list</i> dekh.")
        return
    name = info.get("name", "?")
    del state["products"][url]
    tg_send_message(f"🗑️ Removed: <b>{name[:60]}</b>")

def cmd_target(state, args):
    if len(args) < 2:
        tg_send_message("Format: <code>/target &lt;index&gt; &lt;price&gt;</code>")
        return
    url, info = find_product(state, args[0])
    if not url:
        tg_send_message("Product nahi mila. <i>/list</i> dekh.")
        return
    try:
        target = int(args[1].replace(",", ""))
    except ValueError:
        tg_send_message("Price number hona chahiye.")
        return
    state["products"][url]["target_price"] = target
    tg_send_message(f"🎯 Target set: <b>{info.get('name','?')[:60]}</b> → ₹{target:,}")

def cmd_check(state):
    state["last_run"] = None  # force next run to check
    tg_send_message("⏱️ Next run pe check hoga. 15 min ke andar result.")

def cmd_graph(state, args):
    if not args:
        tg_send_message("Format: <code>/graph &lt;index&gt;</code>")
        return
    url, info = find_product(state, args[0])
    if not url:
        tg_send_message("Product nahi mila.")
        return
    hist = info.get("history", [])
    if len(hist) < 2:
        tg_send_message(f"📊 History abhi {len(hist)} din ki hai. Kam se kam 2 din chahiye graph ke liye.")
        return
    png = make_graph(hist, info.get("name", "?"))
    if png:
        tg_send_photo(png, f"📊 <b>{info.get('name','?')[:60]}</b>")

def handle_command(text, state, chat_id):
    parts = text.strip().split()
    if not parts: return
    cmd = parts[0].lower()
    # strip @botname for group chats
    if "@" in cmd: cmd = cmd.split("@")[0]
    args = parts[1:]
    log(f"[cmd] {cmd} from chat {chat_id}")

    if cmd in ("/start", "/help"):
        tg_send_message(HELP_TEXT, chat_id)
    elif cmd == "/list":
        # list sends to configured chat, not arbitrary
        cmd_list(state)
    elif cmd == "/add":
        cmd_add(state, args)
    elif cmd == "/remove":
        cmd_remove(state, args)
    elif cmd == "/target":
        cmd_target(state, args)
    elif cmd == "/check":
        cmd_check(state)
    elif cmd == "/graph":
        cmd_graph(state, args)
    else:
        tg_send_message(f"Unknown command: <code>{cmd}</code>\n<i>/help</i> dekh.", chat_id)

def process_telegram_updates(state):
    offset = state.get("last_update_id", 0) + 1
    updates = tg_get_updates(offset)
    if not updates:
        return
    for upd in updates:
        state["last_update_id"] = max(state.get("last_update_id", 0), upd.get("update_id", 0))
        msg = upd.get("message") or {}
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")
        if text.startswith("/"):
            try:
                handle_command(text, state, chat_id)
            except Exception as e:
                log(f"[!] cmd error: {e}")
                tg_send_message(f"⚠️ Error: {e}", chat_id)

# ============ PRODUCT CHECK ============

def is_urgent(info):
    price = info.get("price")
    target = info.get("target_price")
    if not price or not target: return False
    return price <= target * (1 + URGENT_THRESHOLD)

def check_product(url, info, state):
    name = info.get("name", "?")
    parser = get_parser(url)
    if not parser:
        log(f"[!] no parser for {url}"); return

    log(f"[*] {name[:50]}")
    html = fetch(url)
    if not html:
        log(f"[!] fetch failed"); return

    parsed = parser(html)
    if not parsed["valid"]:
        log(f"[!] invalid page"); return

    price = parsed["price"]; in_stock = parsed["in_stock"]
    prev_price = info.get("price"); prev_stock = info.get("in_stock", False)
    offers = parsed.get("offers", [])

    log(f"    Rs{price} stock={in_stock} offers={len(offers)}")
    add_history(state, url, price)

    if info.get("notify_on_stock") and in_stock and not prev_stock:
        msg = f"🔔 <b>{name}</b>\n✅ Back in stock!\nPrice: ₹{price or 0:,}\n{url}"
        if offers:
            msg += "\n\n💳 Offers:\n" + "\n".join(f"• {o}" for o in offers)
        tg_send_message(msg)

    if price and prev_price and price < prev_price:
        save = prev_price - price
        tg_send_message(f"🔔 <b>{name}</b>\n📉 Down ₹{save:,}\nWas: ₹{prev_price:,}\nNow: ₹{price:,}\n{url}")

    if price and info.get("target_price") and price <= info["target_price"]:
        msg = f"🎯 <b>{name}</b>\nTarget hit! (≤ ₹{info['target_price']:,})\nNow: ₹{price:,}\n{url}"
        if offers:
            msg += "\n\n💳 Offers:\n" + "\n".join(f"• {o}" for o in offers)
        tg_send_message(msg)

    info["price"] = price
    info["in_stock"] = in_stock
    info["offers"] = offers
    info["last_checked"] = datetime.now().isoformat()

# ============ MAIN ============

def main():
    log("=" * 40)
    state = load_state()
    now = datetime.now()

    # 1. Process incoming Telegram commands
    try:
        process_telegram_updates(state)
    except Exception as e:
        log(f"[!] updates err: {e}")

    # 2. Decide if we need to check now
    urgent_now = any(is_urgent(info) for info in state["products"].values())
    last = state.get("last_run")
    if last and not urgent_now:
        try:
            delta = (now - datetime.fromisoformat(last)).total_seconds()
            if delta < 9 * 60:
                log(f"[~] Throttled ({int(delta)}s). Skip checks.")
                save_state(state)
                return
        except Exception:
            pass

    log(f"[*] Urgent: {urgent_now} | Products: {len(state['products'])}")

    # 3. Check products
    for url, info in list(state["products"].items()):
        try:
            check_product(url, info, state)
        except Exception as e:
            log(f"[!] {url}: {e}")

    state["last_run"] = now.isoformat()
    save_state(state)

    # 4. Sunday graph
    if now.weekday() == 6:
        for url, info in state["products"].items():
            hist = info.get("history", [])
            png = make_graph(hist, info.get("name", "?"))
            if png:
                tg_send_photo(png, f"📊 <b>{info.get('name','?')[:60]}</b> — 30-day")

    log("Done.")

if __name__ == "__main__":
    main()
