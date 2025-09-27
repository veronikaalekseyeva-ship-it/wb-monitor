import os, re, json, time, math, csv
import requests

# Настройки WB
DEST = -283781
SPP = 30
QUERY = "самахан"

# Твои карточки и цены
MY_ITEMS = [
    {"nm": "174390483", "my_price": 288.0, "label": "10 пакетов"},
    {"nm": "187389586", "my_price": 526.0, "label": "20 пакетов"},
    {"nm": "187389938", "my_price": 757.0, "label": "30 пакетов"},
    {"nm": "187390128", "my_price": 982.0, "label": "40 пакетов"},
    {"nm": "187390608", "my_price": 1212.0, "label": "50 пакетов"},
    {"nm": "187390972", "my_price": 1437.0, "label": "60 пакетов"},
]

# Настройки Телеграм
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ALERT_THRESHOLD_RUB = float(os.getenv("ALERT_THRESHOLD_RUB", "1"))  # оповещение если конкурент ниже хотя бы на 1 руб

# Служебные данные
WB_CARD_URLS = [
    "https://card.wb.ru/cards/detail",
    "https://card.wb.ru/cards/v2/detail",
    "https://card.wb.ru/cards/v1/detail",
]
WB_SEARCH_URLS = [
    "https://search.wb.ru/exactmatch/ru/common/v4/search",
    "https://search.wb.ru/exactmatch/ru/mts/v4/search",
    "https://search.wb.ru/exactmatch/ru/common/v5/search",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru",
}

def round2(x: float) -> float:
    return math.floor((x + 1e-9) * 100) / 100

def fetch_cards(nm_ids):
    params = {"appType": 1, "curr": "rub", "dest": DEST, "spp": SPP, "nm": ",".join(nm_ids)}
    for u in WB_CARD_URLS:
        r = requests.get(u, params=params, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            continue
        js = r.json()
        prods = js.get("data", {}).get("products", [])
        if prods:
            return js
    return {"data": {"products": []}}

def parse_cards(js):
    out = {}
    for p in js.get("data", {}).get("products", []):
        nm = str(p.get("id"))
        priceU = p.get("priceU")
        salePriceU = p.get("salePriceU")
        base = round2(priceU / 100.0) if isinstance(priceU, int) else None
        finalP = round2(salePriceU / 100.0) if isinstance(salePriceU, int) else base
        out[nm] = {
            "nm": nm,
            "name": p.get("name"),
            "brand": p.get("brand"),
            "price_base": base,
            "price_display": finalP,
            "rating": p.get("rating"),
            "feedbacks": p.get("feedbacks"),
        }
    return out

def pick_size_from_text(t: str):
    if not t:
        return None
    m1 = re.search(r"(\d+)\s*(пакет|пакета|пакетиков)", t, re.I)
    if m1:
        return m1.group(1)
    m2 = re.search(r"\b(10|20|30|40|50|60|70|80|90|100)\b", t)
    if m2:
        return m2.group(1)
    return None

def search_ids(query, pages=5):
    ids = []
    base = {"appType": 1, "curr": "rub", "resultset": "catalog", "sort": "popular", "query": query}
    for u in WB_SEARCH_URLS:
        for page in range(1, pages + 1):
            prm = dict(base, dest=DEST, spp=SPP, page=page)
            r = requests.get(u, params=prm, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                break
            js = r.json()
            prods = js.get("data", {}).get("products", [])
            if not prods:
                break
            ids.extend(str(p["id"]) for p in prods if p.get("id"))
        if ids:
            break
    return list(dict.fromkeys(ids))

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data, timeout=15)
    except Exception:
        pass

def main():
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S")
    ids = search_ids(QUERY, 5)
    cards = parse_cards(fetch_cards(ids))

    items = []
    for v in cards.values():
        size = pick_size_from_text(v.get("name") or "")
        if v.get("price_display") is None or not size:
            continue
        items.append({**v, "size": size})

    by_size = {}
    for it in items:
        by_size.setdefault(it["size"], []).append(it)
    for k in by_size:
        by_size[k].sort(key=lambda x: x["price_display"])

    my_by_size = {re.search(r"(\d+)", it["label"]).group(1): it for it in MY_ITEMS}

    rows = []
    alerts = []
    for size, arr in sorted(by_size.items(), key=lambda x: int(x[0])):
        top = arr[:5]
        min_item = top[0]
        my = my_by_size.get(size)
        my_price = my["my_price"] if my else None
        row = {
            "timestamp": now_ts,
            "pack_size": size,
            "my_price": my_price,
            "min_price": min_item.get("price_display"),
            "min_nm": min_item.get("nm"),
            "min_brand": min_item.get("brand"),
            "min_name": min_item.get("name"),
            "top5_prices": ",".join(str(x["price_display"]) for x in top),
            "top5_nm": ",".join(x["nm"] for x in top),
        }
        rows.append(row)

        if my and my_price and min_item.get("price_display") is not None:
            diff = my_price - min_item["price_display"]
            if diff > ALERT_THRESHOLD_RUB:
                alerts.append((size, my_price, min_item))

    with open("wb_market_report.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if f.tell() == 0:
            w.writeheader()
        w.writerows(rows)

    if alerts:
        lines = [f"Мониторинг WB {now_ts}"]
        for size, my_price, min_item in alerts:
            url = f"https://www.wildberries.ru/catalog/{min_item['nm']}/detail.aspx"
            lines.append(f"{size} пакетов. Конкурент {min_item['price_display']} руб ({min_item.get('brand')}). Твоя {my_price}. {url}")
        send_telegram("\n".join(lines))

    print(json.dumps(rows, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
