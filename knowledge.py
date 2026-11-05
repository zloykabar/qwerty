# -*- coding: utf-8 -*-
"""
Актуальные данные для ответов: курсы валют (ЦБ РФ), криптовалюты (CoinGecko),
погода (Open-Meteo) и поиск в интернете (DuckDuckGo). Всё бесплатно и без ключей.
"""
import re

from core.lazy import Lazy
requests = Lazy("requests")

# слово в вопросе -> (код валюты, «доллар», «долларов»)
CURRENCIES = {
    "доллар": ("USD", "доллар", "долларов"), "бакс": ("USD", "доллар", "долларов"),
    "евро": ("EUR", "евро", "евро"), "юан": ("CNY", "юань", "юаней"),
    "фунт": ("GBP", "фунт стерлингов", "фунтов стерлингов"), "иен": ("JPY", "иена", "иен"),
    "йен": ("JPY", "иена", "иен"), "гривн": ("UAH", "гривна", "гривен"),
    "тенге": ("KZT", "тенге", "тенге"), "франк": ("CHF", "франк", "франков"),
    "лир": ("TRY", "лира", "лир"), "злот": ("PLN", "злотый", "злотых"),
}
CRYPTO = {"биткоин": ("bitcoin", "Биткоин"), "биткойн": ("bitcoin", "Биткоин"), "bitcoin": ("bitcoin", "Биткоин"),
          "эфир": ("ethereum", "Эфириум"), "ethereum": ("ethereum", "Эфириум")}
PRICE_WORDS = r"(?<!\w)(курс|стоит|цена|почем|сколько|котировк)"

WEB_HINTS = ("сейчас", "сегодня", "вчера", "завтра", "последн", "новост", "курс", "цена", "стоит", "сколько стоит",
             "кто выиграл", "счет", "когда выйдет", "актуальн", "в этом году", "2025", "2026", "2027",
             "расписание", "рейтинг", "кто такой", "кто такая", "что такое", "в интернете", "кто сейчас",
             "какой сейчас", "какая сейчас", "какое сейчас", "сколько лет", "дата выхода")
NO_WEB_HINTS = ("придумай", "анекдот", "стих", "сочини", "шутк", "как дела", "привет", "расскажи сказку",
                "посоветуй", "объясни", "переведи")

WMO = {0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "туман",
       51: "лёгкая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось",
       61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
       71: "небольшой снег", 73: "снег", 75: "сильный снегопад", 77: "снежная крупа", 80: "кратковременный дождь",
       81: "ливень", 82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад", 95: "гроза",
       96: "гроза с градом", 99: "сильная гроза с градом"}


def plural(n, forms):
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def _rub(x):
    if abs(x - round(x)) < 0.005:
        n = int(round(x))
        return f"{n} {plural(n, ('рубль', 'рубля', 'рублей'))}"
    return f"{x:.2f}".replace(".", ",") + " рубля"


# ---------------- курсы валют ----------------
def currency_answer(text):
    """text: нормализованный вопрос. Возвращает ответ или None, если вопрос не про курсы."""
    if not re.search(PRICE_WORDS, text):
        return None
    for stem, (coin, name) in CRYPTO.items():
        if re.search(r"(?<!\w)" + stem, text):
            try:
                d = requests.get("https://api.coingecko.com/api/v3/simple/price",
                                 params={"ids": coin, "vs_currencies": "usd,rub"}, timeout=8).json()[coin]
                usd = f"{d['usd']:,.0f}".replace(",", " ")
                rub = f"{d['rub']:,.0f}".replace(",", " ")
                return f"{name} сейчас стоит около {usd} долларов, это примерно {rub} рублей."
            except Exception:
                return "Не получилось узнать курс криптовалюты. Проверьте интернет."
    for stem, (code, one, many) in CURRENCIES.items():
        if re.search(r"(?<!\w)" + stem, text):
            try:
                data = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=8).json()
                v = data["Valute"][code]
                nominal = v.get("Nominal", 1) or 1
                rate, prev = v["Value"], v.get("Previous")
                subject = one.capitalize() if nominal == 1 else f"{nominal} {many}"
                verb = "стоит" if nominal == 1 else "стоят"
                s = f"{subject} {verb} {_rub(rate)} по курсу Центробанка."
                if prev and abs(rate - prev) >= 0.005:
                    s += f" Предыдущий курс был {_rub(prev)}."
                return s
            except Exception:
                return "Не получилось получить курс валют. Проверьте интернет."
    return None


# ---------------- погода ----------------
def temp_ru(t):
    n = int(round(t))
    if n == 0:
        return "ноль градусов"
    word = plural(abs(n), ("градус", "градуса", "градусов"))
    return f"{'плюс' if n > 0 else 'минус'} {abs(n)} {word}"


def weather_answer(city, day=0):
    """Возвращает текст прогноза или None, если город не найден. Сетевые ошибки поднимаются наружу."""
    geo = None
    for name in [city] + [city[:-k] for k in (1, 2) if len(city) - k >= 3]:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 1, "language": "ru"}, timeout=8).json()
        if r.get("results"):
            geo = r["results"][0]
            break
    if not geo:
        return None
    f = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": geo["latitude"], "longitude": geo["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "wind_speed_unit": "ms", "timezone": "auto", "forecast_days": 3}, timeout=8).json()
    place = geo.get("name", city)
    d = f["daily"]
    lo, hi = d["temperature_2m_min"][day], d["temperature_2m_max"][day]
    desc = WMO.get(d["weather_code"][day], "")
    if day == 0:
        c = f["current"]
        now_desc = WMO.get(c["weather_code"], desc)
        wind = int(round(c["wind_speed_10m"]))
        return (f"{place}: сейчас {temp_ru(c['temperature_2m'])}, ощущается как {temp_ru(c['apparent_temperature'])}, "
                f"{now_desc}, ветер {wind} {plural(wind, ('метр', 'метра', 'метров'))} в секунду. "
                f"Днём до {temp_ru(hi)}, ночью {temp_ru(lo)}.")
    return f"{place}, завтра: {desc}, от {temp_ru(lo)} до {temp_ru(hi)}."


# ---------------- интернет ----------------
def needs_web(text, mode="auto"):
    if mode == "never":
        return False
    if mode == "always":
        return True
    if any(h in text for h in NO_WEB_HINTS):
        return False
    return any(h in text for h in WEB_HINTS)


def web_context(query, max_results=5):
    """Короткая выжимка из результатов поиска DuckDuckGo. Пустая строка, если поиск недоступен."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        res = list(DDGS(timeout=10).text(query, region="ru-ru", max_results=max_results) or [])
    except Exception:
        return ""
    lines = [f"- {r.get('title', '')}: {r.get('body', '')}" for r in res if r.get("body")]
    return "\n".join(lines)[:3500]
