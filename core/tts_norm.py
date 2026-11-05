# -*- coding: utf-8 -*-
"""Текст для озвучки: числа, даты, время, единицы, аббревиатуры словами; английские слова по-русски.
В чат уходит оригинал, в TTS уходит результат normalize_for_tts()."""
import re

from core.paths import load_json

ONES_M = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
ONES_F = ["ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
ONES_N = ["ноль", "одно", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
         "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
HUNDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
SCALES = [("тысяча", "тысячи", "тысяч", True), ("миллион", "миллиона", "миллионов", False),
          ("миллиард", "миллиарда", "миллиардов", False), ("триллион", "триллиона", "триллионов", False)]

ORD_DAY = {1: "первое", 2: "второе", 3: "третье", 4: "четвёртое", 5: "пятое", 6: "шестое", 7: "седьмое",
           8: "восьмое", 9: "девятое", 10: "десятое", 11: "одиннадцатое", 12: "двенадцатое", 13: "тринадцатое",
           14: "четырнадцатое", 15: "пятнадцатое", 16: "шестнадцатое", 17: "семнадцатое", 18: "восемнадцатое",
           19: "девятнадцатое", 20: "двадцатое", 30: "тридцатое"}
ORD_TENS = {20: "двадцать", 30: "тридцать"}
MONTHS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
            "ноября", "декабря"]
THOUSAND_ORD = {2000: "две тысячи"}

# (одна, две-четыре, пять) формы и род для единиц измерения
UNITS = {
    "гб": ("гигабайт", "гигабайта", "гигабайт", "m"), "мб": ("мегабайт", "мегабайта", "мегабайт", "m"),
    "кб": ("килобайт", "килобайта", "килобайт", "m"), "тб": ("терабайт", "терабайта", "терабайт", "m"),
    "ггц": ("гигагерц", "гигагерца", "гигагерц", "m"), "мгц": ("мегагерц", "мегагерца", "мегагерц", "m"),
    "кг": ("килограмм", "килограмма", "килограммов", "m"), "г": ("грамм", "грамма", "граммов", "m"),
    "км": ("километр", "километра", "километров", "m"), "м": ("метр", "метра", "метров", "m"),
    "см": ("сантиметр", "сантиметра", "сантиметров", "m"), "мм": ("миллиметр", "миллиметра", "миллиметров", "m"),
    "мин": ("минута", "минуты", "минут", "f"), "сек": ("секунда", "секунды", "секунд", "f"),
    "ч": ("час", "часа", "часов", "m"), "руб": ("рубль", "рубля", "рублей", "m"),
    "₽": ("рубль", "рубля", "рублей", "m"), "р": ("рубль", "рубля", "рублей", "m"),
    "$": ("доллар", "доллара", "долларов", "m"), "€": ("евро", "евро", "евро", "n"),
    "%": ("процент", "процента", "процентов", "m"), "°c": ("градус", "градуса", "градусов", "m"),
    "°": ("градус", "градуса", "градусов", "m"), "мс": ("миллисекунда", "миллисекунды", "миллисекунд", "f"),
}
FEM_NEXT = ("минут", "секунд", "штук", "недел", "тысяч", "копе", "миллисекунд", "сотк", "минуту", "секунду")
NEUT_NEXT = ("очк", "ведр", "яблок")


def plural_idx(n):
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return 2
    if n % 10 == 1:
        return 0
    if 2 <= n % 10 <= 4:
        return 1
    return 2


def _below_1000(n, gender):
    ones = {"m": ONES_M, "f": ONES_F, "n": ONES_N}[gender]
    out = []
    if n >= 100:
        out.append(HUNDS[n // 100])
        n %= 100
    if 10 <= n < 20:
        out.append(TEENS[n - 10])
    else:
        if n >= 20:
            out.append(TENS[n // 10])
            n %= 10
        if n:
            out.append(ones[n])
    return out


def int_words(n, gender="m"):
    n = int(n)
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + int_words(-n, gender)
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    if len(groups) > len(SCALES) + 1:
        return " ".join(ONES_M[int(c)] for c in str(abs(n)))
    parts = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            continue
        if i == 0:
            parts += _below_1000(g, gender)
        else:
            one, few, many, fem = SCALES[i - 1]
            parts += _below_1000(g, "f" if fem else "m")
            parts.append((one, few, many)[plural_idx(g)])
    return " ".join(parts)


def unit_phrase(n, unit_key):
    one, few, many, gender = UNITS[unit_key]
    return f"{int_words(n, gender)} {(one, few, many)[plural_idx(n)]}"


_FRAC = {1: ("десятая", "десятых"), 2: ("сотая", "сотых"), 3: ("тысячная", "тысячных")}


def decimal_words(whole, frac):
    f = int(frac)
    k = len(frac)
    if k not in _FRAC:
        return f"{int_words(whole)} запятая {' '.join(ONES_M[int(c)] for c in frac)}"
    w = int_words(whole, "f")
    cel = "целая" if plural_idx(int(whole)) == 0 else "целых"
    one, many = _FRAC[k]
    return f"{w} {cel} {int_words(f, 'f')} {one if plural_idx(f) == 0 else many}"


def year_words(y):
    """2026 -> «две тысячи двадцать шестой» (в родительном падеже: «... шестого года»)."""
    return int_words(y)


def _ord_gen_year(y):
    """Год в родительном падеже: «две тысячи двадцать шестого»."""
    last2 = y % 100
    base = y - last2
    head = int_words(base) if base else ""
    ords = {1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого", 5: "пятого", 6: "шестого", 7: "седьмого",
            8: "восьмого", 9: "девятого", 10: "десятого", 11: "одиннадцатого", 12: "двенадцатого",
            13: "тринадцатого", 14: "четырнадцатого", 15: "пятнадцатого", 16: "шестнадцатого",
            17: "семнадцатого", 18: "восемнадцатого", 19: "девятнадцатого"}
    tens_o = {20: "двадцатого", 30: "тридцатого", 40: "сорокового", 50: "пятидесятого", 60: "шестидесятого",
              70: "семидесятого", 80: "восьмидесятого", 90: "девяностого"}
    if last2 == 0:
        return (head + "ого") if head else "нулевого"
    if last2 in ords:
        tail = ords[last2]
    elif last2 % 10 == 0:
        tail = tens_o[last2]
    else:
        tail = TENS[last2 // 10] + " " + ords[last2 % 10]
    return (head + " " + tail).strip()


def day_ord(d):
    if d in ORD_DAY:
        return ORD_DAY[d]
    return ORD_TENS[(d // 10) * 10] + " " + ORD_DAY[d % 10]


# ---------------------------------------------------------------------------
_LAT = [("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("ck", "к"), ("kh", "х"), ("zh", "ж"),
        ("ee", "и"), ("oo", "у"), ("qu", "кв"), ("ya", "я"), ("yu", "ю"), ("yo", "ё"), ("ts", "ц")]
_LAT1 = dict(zip("abcdefghijklmnopqrstuvwxyz",
                 ["а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м", "н", "о", "п", "к", "р", "с",
                  "т", "у", "в", "в", "кс", "й", "з"]))


def latin_to_ru(word):
    """Запасная транслитерация для английских слов, которых нет в словаре."""
    w = word.lower()
    for a, b in _LAT:
        w = w.replace(a, b)
    return "".join(_LAT1.get(ch, ch) for ch in w)


class Normalizer:
    def __init__(self):
        d = load_json("translit.json", {}) or {}
        self.words = {k.lower(): v for k, v in (d.get("words") or {}).items()}
        self.abbr = d.get("abbr") or {}

    # --- отдельные правила ---
    def _dates(self, t):
        def rep(m):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                return f"{day_ord(d)} {MONTHS_G[mo - 1]} {_ord_gen_year(y)} года"
            return m.group(0)
        return re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", rep, t)

    def _times(self, t):
        def rep(m):
            h, mi = int(m.group(1)), int(m.group(2))
            if h > 24 or mi > 59:
                return m.group(0)
            if mi == 0:
                return f"{int_words(h)} {('час', 'часа', 'часов')[plural_idx(h)]} ровно"
            return f"{int_words(h)} {'ноль ' + int_words(mi) if mi < 10 else int_words(mi, 'f')}"
        return re.sub(r"\b(\d{1,2}):(\d{2})\b", rep, t)

    def _units(self, t):
        keys = sorted(UNITS, key=len, reverse=True)
        pat = r"(?<![\w.,])(-?\d+)(?:[.,](\d+))?\s?(" + "|".join(re.escape(k) for k in keys) + r")(?![\wА-Яа-я])"

        def rep(m):
            unit = m.group(3).lower()
            if unit == "°":
                unit = "°"
            if unit not in UNITS:
                return m.group(0)
            whole, frac = m.group(1), m.group(2)
            if frac:
                one, few, many, g = UNITS[unit]
                return decimal_words(whole, frac) + " " + many.replace("градусов", "градуса") \
                    if unit in ("°", "°c") else decimal_words(whole, frac) + " " + few
            return unit_phrase(int(whole), unit)
        t = re.sub(pat, rep, t, flags=re.I)
        return re.sub(r"°\s?[CcСс]\b", "", t)

    def _decimals(self, t):
        return re.sub(r"(?<![\w.,])(-?\d+)[.,](\d{1,3})(?![\d])",
                      lambda m: ("минус " if m.group(1).startswith("-") else "")
                      + decimal_words(m.group(1).lstrip("-"), m.group(2)), t)

    def _ints(self, t):
        def rep(m):
            s = m.group(0)
            neg = s.startswith("-") or s.startswith("−")
            digits = s.lstrip("-−+")
            if len(digits) > 15:
                return " ".join(ONES_M[int(c)] for c in digits)
            after = t[m.end():m.end() + 12].lower().lstrip()
            g = "f" if after.startswith(FEM_NEXT) else ("n" if after.startswith(NEUT_NEXT) else "m")
            w = int_words(int(digits), g)
            if g == "f" and after.startswith(("минуту", "секунду")) and w.endswith("одна"):
                w = w[:-4] + "одну"
            return ("минус " if neg else "") + w
        return re.sub(r"(?<![\w])[-−+]?\d+(?![\w])", rep, t)

    def _abbr(self, t):
        for k in sorted(self.abbr, key=len, reverse=True):
            t = re.sub(r"(?<![\w])" + re.escape(k) + r"(?![\w])", self.abbr[k], t)
        return t

    def _latin(self, t):
        def rep(m):
            w = m.group(0)
            lw = w.lower()
            if lw in self.words:
                return self.words[lw]
            if len(lw) == 1:
                return lw
            if w.isupper() and len(w) <= 5:                      # аббревиатура: читаем по буквам
                return " ".join(_LAT1.get(c.lower(), c) for c in w)
            return latin_to_ru(w)
        return re.sub(r"[A-Za-z][A-Za-z\-']*", rep, t)

    def __call__(self, text):
        t = text or ""
        t = self._abbr(t)
        t = self._dates(t)
        t = self._times(t)
        t = self._units(t)
        t = self._decimals(t)
        t = self._ints(t)
        t = self._latin(t)
        t = t.replace("%", " процентов").replace("&", " и ").replace("+", " плюс ")
        return re.sub(r"\s+", " ", t).strip()


_N = None


def normalize_for_tts(text):
    global _N
    if _N is None:
        _N = Normalizer()
    try:
        return _N(text)
    except Exception:
        return text
