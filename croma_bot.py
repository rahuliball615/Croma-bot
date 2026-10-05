import cloudscraper
from bs4 import BeautifulSoup
import json
import os
import re
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

def debug_page(html, name):
    """Debug — page structure ka summary print karo"""
    log("=" * 50)
    log(f"DEBUG STRUCTURE — {name}")
    log("=" * 50)

    soup = BeautifulSoup(html, "html.parser")

    # 1. JSON-LD
    lds = soup.find_all("script", type="application/ld+json")
    log(f"JSON-LD script tags: {len(lds)}")
    for i, tag in enumerate(lds[:3]):
        content = (tag.string or "")[:200]
        log(f"  [{i}] {content}...")

    # 2. Rupee symbol pehle 5000 chars mein
    log("---")
    rupee_pos = html.find("₹")
    log(f"First ₹ at position: {rupee_pos}")
    if rupee_pos > 0:
        log(f"Context: ...{html[max(0,rupee_pos-100):rupee_pos+100]}...")

    # 3. Common price patterns
    log("---")
    log("Regex search for price patterns:")
    patterns = [
        (r'"price"\s*:\s*"?(\d{3,7})', "JSON price"),
        (r'"priceAmount"\s*:\s*"?(\d{3,7})', "priceAmount"),
        (r'₹\s*([\d,]+)', "Rupee number"),
        (r'class="[^"]*price[^"]*"[^>]*>([^<]{1,30})', "price class"),
        (r'class="[^"]*Price[^"]*"[^>]*>([^<]{1,30})', "Price class"),
    ]
    for pat, label in patterns:
        matches = re.findall(pat, html, re.IGNORECASE)[:3]
        log(f"  {label}: {matches}")

    # 4. Stock indicators
    log("---")
    log("Stock keywords in HTML (lowercase):")
    lower = html.lower()
    for kw in ["add to cart", "addtocart", "out of stock", "outofstock",
               "in stock", "instock", "notify me", "soldout", "sold out",
               "buy now", "check delivery"]:
        cnt = lower.count(kw)
        if cnt > 0:
            log(f"  '{kw}': {cnt} times")

    # 5. Title
    log("---")
    log(f"Page title: {soup.title.string if soup.title else 'None'}")

    # 6. Meta tags mein price?
    log("---")
    for meta in soup.find_all("meta"):
        prop = meta.get("property") or meta.get("name") or ""
        if any(x in prop.lower() for x in ["price", "product", "og:"]):
            content = meta.get("content", "")[:100]
            log(f"  {prop} = {content}")

    log("=" * 50)

def main():
    log("Debug run started")

    for product in PRODUCTS:
        log(f"[*] {product['name']}")
        html = fetch(product["url"])
        if not html:
            log(f"[!] Failed to fetch")
            continue
        log(f"[+] Got {len(html)} bytes")
        debug_page(html, product["name"])

    log("Debug done")

if __name__ == "__main__":
    main()
