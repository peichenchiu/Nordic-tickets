"""暫時的診斷：比較幾種 Google Flights 網址能不能載出結果（判斷是被擋還是網址格式改了）。"""
import asyncio, base64, re
from urllib.parse import urlencode, quote
from playwright.async_api import async_playwright
import os
os.environ["LIMIT"] = ""
import check_nordic as c
import datetime as dt

d1, d2 = dt.date(2027, 7, 18), dt.date(2027, 8, 3)


def tfs(legs, trip):
    info = b"".join(c._field(3, c._leg(*leg)) for leg in legs)
    info += b"".join(c._field(8, p) for p in [1, 1, 2]) + c._field(9, 1) + c._field(19, trip)
    t = base64.urlsafe_b64encode(info).decode().rstrip("=")
    return "https://www.google.com/travel/flights/search?" + urlencode({"tfs": t, "hl": "en", "gl": "TW", "curr": "TWD"})


URLS = {
    "multi-city (目前用的)": tfs([(d1, "TPE", "HEL"), (d2, "OSL", "TPE")], 3),
    "one-way tfs": tfs([(d1, "TPE", "HEL")], 2),
    "round-trip tfs": tfs([(d1, "TPE", "HEL"), (d2, "HEL", "TPE")], 1),
    "q= one-way": "https://www.google.com/travel/flights?hl=en&gl=TW&curr=TWD&q=" + quote("Flights from TPE to HEL on 2027-07-18 one way"),
    "q= 2026 near date": "https://www.google.com/travel/flights?hl=en&gl=TW&curr=TWD&q=" + quote("Flights from TPE to HEL on 2026-11-20 one way"),
}


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(locale="en-US", timezone_id="Asia/Taipei", viewport={"width": 1366, "height": 900},
                                  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
        page = await ctx.new_page()
        for name, url in URLS.items():
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            await page.wait_for_timeout(20_000)
            text = re.sub(r"\s+", " ", await page.inner_text("body"))
            n = await page.locator('[aria-label*="Taiwan dollars"]').count()
            oops = "something went wrong" in text
            print(f"[{name}] 價格列 {n} 個, Oops={oops}, url={page.url[:150]}", flush=True)
            if n == 0:
                i = text.find("Search results")
                print("   ", text[max(0, i):i + 300], flush=True)
        await b.close()

asyncio.run(main())
