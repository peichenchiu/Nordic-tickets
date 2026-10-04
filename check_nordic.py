"""每日查詢台北 ⇄ 芬蘭／挪威（一進一出）全家機票，只看傳統航空，中午寄 Gmail 報告。

用 Google Flights 的「多個城市」查詢：
  A. 台北 → 赫爾辛基 HEL ……（停留）…… 奧斯陸 OSL → 台北
  B. 台北 → 奧斯陸 OSL ……（停留）…… 赫爾辛基 HEL → 台北
每組日期先點選去程最便宜的傳統航空班次，再選回程最便宜的傳統航空班次，讀取全家含稅總價。
設定都可以用環境變數覆寫（見 README）。
"""
import asyncio
import base64
import datetime as dt
import json
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


ORIGIN = os.getenv("ORIGIN", "TPE")
FINLAND = os.getenv("FINLAND_AIRPORT", "HEL")  # 赫爾辛基
NORWAY = os.getenv("NORWAY_AIRPORT", "OSL")  # 奧斯陸
DEPART_START = dt.date.fromisoformat(os.getenv("DEPART_START") or "2027-07-18")
DEPART_END = dt.date.fromisoformat(os.getenv("DEPART_END") or "2027-08-10")
TRIP_DAYS_MIN = int(os.getenv("TRIP_DAYS_MIN") or "16")
TRIP_DAYS_MAX = int(os.getenv("TRIP_DAYS_MAX") or "21")
MAX_STOPS = int(os.getenv("MAX_STOPS") or "1")  # 每段最多轉機幾次
# 要含託運行李：Google Flights 這類行程只能篩手提行李，所以改用航空公司規定判斷。
# 下列航空的長程經濟艙最便宜票種（Light/Basic）常不含託運行李，Google 顯示的最低價多半是這種票，
# 預設排除；設 REQUIRE_BAGS=0 可改為不排除。
REQUIRE_BAGS = (os.getenv("REQUIRE_BAGS") or "1") != "0"
THRESHOLD_TWD = int(os.getenv("THRESHOLD_TWD") or "85000")  # 全家總價低於此就特別推薦
ADULTS = int(os.getenv("ADULTS") or "2")
CHILDREN = int(os.getenv("CHILDREN") or "1")  # 2～11 歲
WORKERS = int(os.getenv("WORKERS") or "6")
LIMIT = int(os.getenv("LIMIT") or "0")  # 只查前幾組（診斷用），0 = 不限

RESULTS = Path("results")
DEBUG = Path("debug")
HISTORY_LABEL = "nordic-history"  # 用一個 issue 記錄每天的最低價，用來判斷「歷史新低」

# 廉價航空（名稱比對，不分大小寫）；行程中只要有一段是這些就排除
LOW_COST = [
    "Norwegian", "Scoot", "Tigerair", "AirAsia", "Air Asia", "Jetstar", "VietJet", "Vietjet",
    "Ryanair", "Wizz", "easyJet", "Peach", "ZIPAIR", "Zipair", "flydubai", "Pegasus",
    "SunExpress", "Eurowings", "Vueling", "Transavia", "PLAY", "Cebu Pacific", "Spring Airlines",
    "IndiGo", "Jet2", "Volotea", "flyadeal", "Air Arabia", "Lion Air", "Batik", "Nok Air",
    "Thai Lion", "Jeju Air", "T'way", "Jin Air", "Air Busan", "Air Seoul", "Eastar",
    "HK Express", "Greater Bay", "Starflyer", "Spring Japan", "Flyr",
]
LOW_COST_RE = re.compile("|".join(rf"\b{re.escape(n)}\b" for n in LOW_COST), re.I)
NO_BAG_FARES = [
    "Finnair", "KLM", "Air France", "Lufthansa", "SWISS", "Swiss", "Austrian", "Brussels Airlines",
    "SAS", "Scandinavian", "British Airways", "Iberia", "Aer Lingus", "LOT", "ITA",
    "Icelandair", "airBaltic", "Delta", "United", "American", "Air Canada", "Virgin Atlantic",
]
NO_BAG_RE = re.compile("|".join(rf"\b{re.escape(n)}\b" for n in NO_BAG_FARES))
# Google Flights 每個班次列的 aria-label，例如：
# "From 152345 New Taiwan dollars. 1 stop flight with EVA Air and Finnair. Leaves ..."
PRICE_RE = re.compile(r"([\d,]+)\s+(?:New\s+)?Taiwan dollars", re.I)
AIRLINE_RE = re.compile(r"flight with (.+?)\.\s", re.I)
STOPS_RE = re.compile(r"\b(Nonstop|(\d+) stops?)\s+flight", re.I)
OPTIONS_SEL = '[role="link"][aria-label*="Taiwan dollars"], li [aria-label*="Taiwan dollars"][aria-label*="flight with"]'


# ---------- Google Flights 網址（tfs 參數是 protobuf，手動編碼） ----------

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(num: int, value) -> bytes:
    if isinstance(value, int):
        return _varint(num << 3) + _varint(value)
    if isinstance(value, str):
        value = value.encode()
    return _varint(num << 3 | 2) + _varint(len(value)) + value


def _leg(date: dt.date, frm: str, to: str) -> bytes:
    return (_field(2, date.isoformat()) + _field(5, MAX_STOPS)
            + _field(13, _field(2, frm)) + _field(14, _field(2, to)))


def search_url(legs: list[tuple[dt.date, str, str]]) -> str:
    info = b"".join(_field(3, _leg(*leg)) for leg in legs)
    info += b"".join(_field(8, p) for p in [1] * ADULTS + [2] * CHILDREN)  # 1 成人 2 兒童
    info += _field(9, 1) + _field(19, 3)  # 經濟艙、多個城市
    tfs = base64.urlsafe_b64encode(info).decode().rstrip("=")
    return "https://www.google.com/travel/flights/search?" + urlencode(
        {"tfs": tfs, "hl": "en", "gl": "TW", "curr": "TWD"})


# ---------- 查詢 ----------

def parse_option(label: str) -> dict | None:
    price = PRICE_RE.search(label)
    if not price:
        return None
    airlines = AIRLINE_RE.search(label)
    stops = STOPS_RE.search(label)
    return {
        "price": int(price.group(1).replace(",", "")),
        "airlines": airlines.group(1) if airlines else "",
        "stops": 0 if stops and stops.group(1).lower() == "nonstop"
        else int(stops.group(2)) if stops else None,
    }


async def list_options(page) -> list[dict]:
    """回傳目前頁面上所有班次（含 index，用來點選）。"""
    try:
        await page.wait_for_selector(OPTIONS_SEL, timeout=45_000)
    except Exception:
        return []
    await page.wait_for_timeout(1500)
    # 「View more flights」展開其餘班次，才找得到真正最便宜的
    more = page.get_by_role("button", name=re.compile(r"more flights", re.I))
    if await more.count():
        try:
            await more.first.click(timeout=5000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass
    # 標上編號以便之後點選；同一班次可能有隱藏的重複元素，只留看得到的
    labels = await page.locator(OPTIONS_SEL).evaluate_all("""els => els.map((e, i) => {
        e.setAttribute('data-gf-idx', i);
        return e.offsetParent === null ? null : e.getAttribute('aria-label');
    })""")
    opts = []
    for i, label in enumerate(labels):
        o = parse_option(label or "")
        if o:
            o["idx"] = i
            o["label"] = label
            opts.append(o)
    return opts


async def select(page, opt: dict, before: list[dict]) -> None:
    """點選班次，等頁面換成下一段的列表。"""
    # 用 JS 點，避免浮動的頁首／提示框擋住造成 Playwright 判定無法點擊
    await page.evaluate("i => document.querySelector(`[data-gf-idx=\"${i}\"]`).click()", opt["idx"])
    old = {o["label"] for o in before}
    for _ in range(40):
        await page.wait_for_timeout(500)
        now = await page.locator(OPTIONS_SEL).evaluate_all(
            "els => els.filter(e => e.offsetParent !== null).map(e => e.getAttribute('aria-label'))")
        if now and not set(now) & old:
            return
    raise RuntimeError("點選去程後回程列表沒有出現")


def pick(opts: list[dict]) -> dict | None:
    good = [o for o in opts if o["airlines"] and not LOW_COST_RE.search(o["airlines"])
            and not (REQUIRE_BAGS and NO_BAG_RE.search(o["airlines"]))
            and (o["stops"] is None or o["stops"] <= MAX_STOPS)]
    return min(good, key=lambda o: o["price"]) if good else None


async def accept_consent(page) -> None:
    if "consent." in page.url:
        btn = page.get_by_role("button", name=re.compile(r"Accept all|全部接受", re.I))
        if await btn.count():
            await btn.first.click()
            await page.wait_for_load_state("domcontentloaded")


async def check_one(page, route: str, depart: dt.date, ret: dt.date) -> dict:
    first, last = (FINLAND, NORWAY) if route == "A" else (NORWAY, FINLAND)
    url = search_url([(depart, ORIGIN, first), (ret, last, ORIGIN)])
    result = {"route": route, "into": first, "out_of": last,
              "depart": depart.isoformat(), "return": ret.isoformat(), "url": url}
    tag = f"{route}_{depart.isoformat()}_{ret.isoformat()}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        await accept_consent(page)
        out_opts = await list_options(page)
        out = pick(out_opts)
        if not out:
            return await fail(page, result, tag, out_opts, "去程")
        if LIMIT:
            print("去程範例：", *[o["label"][:200] for o in out_opts[:3]], sep="\n  ", flush=True)
        await select(page, out, out_opts)
        ret_opts = await list_options(page)
        back = pick(ret_opts)
        if LIMIT:
            print("回程範例：", *[o["label"][:200] for o in ret_opts[:3]], sep="\n  ", flush=True)
        if not back:
            return await fail(page, result, tag, ret_opts, "回程")
        result.update(status="ok", min_price=back["price"],
                      out_airlines=out["airlines"], out_stops=out["stops"],
                      ret_airlines=back["airlines"], ret_stops=back["stops"])
    except Exception as e:
        result.update(status="error", error=str(e)[:300])
        await save_debug(page, tag)
    return result


async def fail(page, result: dict, tag: str, opts: list[dict], leg: str) -> dict:
    text = await page.inner_text("body")
    if re.search(r"unusual traffic|not a robot|captcha", text, re.I):
        status = "blocked"
    elif opts or re.search(r"No (results|flights) (returned|found)|no options", text, re.I):
        status = "no_flights"  # 有班次但全是廉價航空或轉機太多，或當天沒有班次
    else:
        status = "no_price"
    result.update(status=status, leg=leg, options=len(opts))
    if status != "no_flights":
        await save_debug(page, tag)
    return result


async def save_debug(page, tag: str) -> None:
    try:
        DEBUG.mkdir(exist_ok=True)
        await page.screenshot(path=str(DEBUG / f"{tag}.png"), full_page=True)
        (DEBUG / f"{tag}.txt").write_text(
            page.url + "\n\n" + await page.inner_text("body"), encoding="utf-8")
    except Exception:
        pass


async def search_all(jobs: list[tuple[str, dt.date, dt.date]]) -> list[dict]:
    queue: asyncio.Queue = asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)
    results = []

    async def worker(browser):
        ctx = await browser.new_context(
            locale="en-US",
            timezone_id="Asia/Taipei",
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        while not queue.empty():
            job = queue.get_nowait()
            r = await check_one(page, *job)
            if r["status"] == "error":  # 偶爾點選後頁面沒反應，重查一次通常就好
                await asyncio.sleep(3)
                r = await check_one(page, *job)
            print(json.dumps(r, ensure_ascii=False), flush=True)
            results.append(r)
            await asyncio.sleep(2)  # 放慢速度，避免被當成機器人
        await ctx.close()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        await asyncio.gather(*(worker(browser) for _ in range(WORKERS)))
        await browser.close()
    return results


def run_search() -> list[dict]:
    jobs = []
    d = DEPART_START
    while d <= DEPART_END:
        for days in range(TRIP_DAYS_MIN, TRIP_DAYS_MAX + 1):
            for route in ("A", "B"):
                jobs.append((route, d, d + dt.timedelta(days=days)))
        d += dt.timedelta(days=1)
    if LIMIT:
        jobs = jobs[:LIMIT]
    return asyncio.run(search_all(jobs))


# ---------- 歷史最低價（存在 GitHub issue 內文） ----------

def _gh():
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if not (token and repo):
        return None, None
    return (f"https://api.github.com/repos/{repo}",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})


def load_history() -> tuple[dict, int | None]:
    """回傳 ({日期: 當天最低價}, issue 編號)。"""
    api, h = _gh()
    if not api:
        return {}, None
    try:
        issues = requests.get(f"{api}/issues", headers=h, timeout=30,
                              params={"labels": HISTORY_LABEL, "state": "open"}).json()
        if not issues:
            return {}, None
        m = re.search(r"<!-- history:(.*?) -->", issues[0].get("body") or "", re.S)
        return (json.loads(m.group(1)) if m else {}), issues[0]["number"]
    except Exception as e:
        print(f"load history failed: {e}", file=sys.stderr)
        return {}, None


def save_history(history: dict, number: int | None) -> None:
    api, h = _gh()
    if not api:
        return
    lines = "\n".join(f"- {d}：NT${p:,}" for d, p in sorted(history.items(), reverse=True))
    body = (f"芬蘭／挪威機票每日最低價紀錄（自動更新，請勿關閉）\n\n{lines}\n\n"
            f"<!-- history:{json.dumps(history)} -->")
    try:
        if number:
            requests.patch(f"{api}/issues/{number}", headers=h, json={"body": body}, timeout=30)
        else:
            requests.post(f"{api}/issues", headers=h, timeout=30, json={
                "title": "📈 芬蘭／挪威機票最低價紀錄", "body": body, "labels": [HISTORY_LABEL]})
    except Exception as e:
        print(f"save history failed: {e}", file=sys.stderr)


# ---------- 報告 ----------

def notify_email(subject: str, body: str) -> None:
    user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    to = os.getenv("NOTIFY_EMAIL") or user  # 沒設定收件人就寄給自己
    if not (user and pw and to):
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg)


def send_email_safely(subject: str, body: str) -> None:
    try:
        notify_email(subject, body)
    except Exception as e:
        print(f"email failed: {e}", file=sys.stderr)


def route_name(r: dict) -> str:
    names = {FINLAND: "芬蘭", NORWAY: "挪威"}
    return f"進{names[r['into']]}{r['into']}／出{names[r['out_of']]}{r['out_of']}"


def format_line(r: dict) -> str:
    days = (dt.date.fromisoformat(r["return"]) - dt.date.fromisoformat(r["depart"])).days
    stops = lambda s: "直飛" if s == 0 else f"轉{s}次" if s else ""
    return (f"- {r['depart']} 去 / {r['return']} 回（{days} 天，{route_name(r)}）："
            f"全家含稅 NT${r['min_price']:,}\n"
            f"  去程 {r['out_airlines']} {stops(r['out_stops'])}；"
            f"回程 {r['ret_airlines']} {stops(r['ret_stops'])}\n  {r['url']}")


def main() -> int:
    if os.getenv("TEST_EMAIL") == "true":
        if not (os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD")):
            print("沒有設定 SMTP_USER / SMTP_PASSWORD", file=sys.stderr)
            return 1
        notify_email("✈️ 芬蘭挪威機票監控：測試信",
                     "這是測試信。收到代表 Gmail 設定成功，之後每天中午 12:00 左右會寄最低價報告給你。")
        print("測試信已寄出")
        return 0

    results = run_search()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "nordic.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    ok = sorted((r for r in results if r["status"] == "ok"), key=lambda r: r["min_price"])
    counts = {s: sum(r["status"] == s for r in results)
              for s in ("no_flights", "blocked", "no_price", "error")}
    summary = (f"查詢 {len(results)} 組日期，成功 {len(ok)} 組；"
               f"沒有符合條件的傳統航空班次 {counts['no_flights']} 組"
               + (f"，查詢失敗 {counts['blocked'] + counts['no_price'] + counts['error']} 組"
                  if counts['blocked'] + counts['no_price'] + counts['error'] else "") + "。")
    print(summary)

    now = dt.datetime.now(ZoneInfo("Asia/Taipei"))
    today = now.strftime("%m/%d")
    header = (f"台北 {ORIGIN} ⇄ 芬蘭 {FINLAND}／挪威 {NORWAY}（一進一出，多個城市行程）\n"
              f"出發 {DEPART_START}～{DEPART_END}，旅程 {TRIP_DAYS_MIN}～{TRIP_DAYS_MAX} 天，"
              f"經濟艙，{ADULTS} 成人 + {CHILDREN} 兒童，只看傳統航空，每段最多轉機 {MAX_STOPS} 次"
              + ("，只看基本票種就含託運行李的航空" if REQUIRE_BAGS else ""))

    if not ok:
        print("沒有抓到任何價格，請查看 artifact 中的 debug 截圖。", file=sys.stderr)
        send_email_safely(f"⚠️ 芬蘭挪威機票 {today}：查詢失敗",
                          f"{header}\n\n{summary}\nGoogle Flights 可能擋了自動查詢或改版，"
                          "請查看 GitHub Actions 紀錄。")
        return 1

    best = ok[0]["min_price"]
    history, issue_no = load_history()
    prev_low = min(history.values()) if history else None
    yesterday = history.get((now.date() - dt.timedelta(days=1)).isoformat())
    history[now.date().isoformat()] = min(best, history.get(now.date().isoformat(), best))
    if not LIMIT:  # 診斷用的部分查詢不算進歷史紀錄
        save_history(history, issue_no)

    reasons = []
    if best < THRESHOLD_TWD:
        reasons.append(f"低於門檻 NT${THRESHOLD_TWD:,}")
    if prev_low is not None and best < prev_low:
        reasons.append(f"比之前歷史最低 NT${prev_low:,} 還便宜 NT${prev_low - best:,}")
    recommend = ""
    if reasons:
        recommend = ("⭐⭐⭐ 特別推薦 ⭐⭐⭐\n" + "、".join(reasons) + "，建議盡快查看下面這組：\n"
                     + format_line(ok[0]) + "\n\n")
    trend = []
    if yesterday:
        diff = best - yesterday
        trend.append(f"昨天最低 NT${yesterday:,}（今天{'漲' if diff > 0 else '跌'} NT${abs(diff):,}）"
                     if diff else f"昨天最低 NT${yesterday:,}（持平）")
    if prev_low is not None:
        trend.append(f"之前歷史最低 NT${prev_low:,}")

    by_route = {}
    for r in ok:
        by_route.setdefault(r["route"], r)
    by_depart: dict[str, dict] = {}
    for r in ok:
        by_depart.setdefault(r["depart"], r)
    table = [f"{d}  NT${r['min_price']:,}（{(dt.date.fromisoformat(r['return']) - dt.date.fromisoformat(d)).days} 天，"
             f"{route_name(r)}）" for d, r in sorted(by_depart.items())]

    body = (f"{recommend}{header}\n門檻：NT${THRESHOLD_TWD:,}\n{summary}\n"
            + ("\n".join(trend) + "\n" if trend else "")
            + "\n【今日最便宜 10 組】\n" + "\n".join(format_line(r) for r in ok[:10])
            + "\n\n【兩種走法各自最低】\n" + "\n".join(format_line(r) for r in by_route.values())
            + "\n\n【每個出發日的最低價】\n" + "\n".join(table)
            + "\n\n價格為 Google Flights 顯示的全家（含兒童）含稅總價，去程、回程各選最便宜的傳統航空班次；"
              + ("已排除最便宜票種可能不含託運行李的航空（芬航、KLM、法航、漢莎集團、SAS 等），"
                 "列出的航空長程經濟艙一般含託運行李，訂票時仍請確認票種的行李額度；" if REQUIRE_BAGS else "")
              + "實際價格以航空公司訂票頁為準。")
    flag = "🔥特別推薦！" if reasons else ""
    send_email_safely(f"✈️ 芬蘭挪威 {today}：全家最低 NT${best:,} {flag}".strip(), body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
