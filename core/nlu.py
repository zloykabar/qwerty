# -*- coding: utf-8 -*-
"""Понимание русской речи ПЕРЕД матчингом команд: падежи (pymorphy3), топонимы, синонимы,
нечёткое сопоставление (rapidfuzz), разбор времени и деления фраз на шаги."""
import difflib
import re
import threading

from core.paths import load_json
from core.logsetup import get

log = get("nlu")

# ---------------------------------------------------------------- морфология
_morph = None
_morph_tried = False
_morph_lock = threading.Lock()


def get_morph():
    """pymorphy3 грузится лениво (при первом обращении или фоновым прогревом)."""
    global _morph, _morph_tried
    if _morph is not None or _morph_tried:
        return _morph
    with _morph_lock:
        if _morph is None and not _morph_tried:
            _morph_tried = True
            try:
                import pymorphy3
                _morph = pymorphy3.MorphAnalyzer()
            except Exception:
                log.warning("pymorphy3 недоступен, работаю на упрощённых правилах", exc_info=True)
    return _morph


def warm_up():
    threading.Thread(target=get_morph, daemon=True).start()


_lemma_cache = {}
_ENDINGS = ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях", "ов", "ев", "ам", "ям", "ом", "ем",
            "ой", "ей", "ую", "юю", "ая", "яя", "ое", "ее", "ые", "ие", "ых", "их", "ий", "ый", "ут", "ют", "ат",
            "ят", "ет", "ит", "ешь", "ишь", "ть", "ти", "а", "я", "у", "ю", "е", "ы", "и", "о", "ь", "й")


def lemma(word):
    w = word.lower().replace("ё", "е")
    if w in _lemma_cache:
        return _lemma_cache[w]
    m = get_morph() if _morph_tried or _morph is not None else None
    res = w
    if m is not None and re.fullmatch(r"[а-я\-]+", w):
        try:
            res = m.parse(w)[0].normal_form.replace("ё", "е")
        except Exception:
            res = w
    elif len(w) > 4:                                    # запасной вариант без pymorphy3: грубое усечение окончания
        for e in _ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= 3:
                res = w[:-len(e)]
                break
    _lemma_cache[w] = res
    return res


def lemmas(text):
    return [lemma(w) for w in text.split()]


# ---------------------------------------------------------------- нормализация
def norm(s):
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower().replace("ё", "е")).split())


def _syn_cfg():
    return load_json("synonyms.json", {}) or {}


def strip_fillers(text, wake_words=()):
    """Убирает обращение «Джарвис» и ведущие слова-паразиты. Ни одного слова из середины не трогаем."""
    t = text
    for w in wake_words:
        t = re.sub(r"(?<!\w)" + re.escape(w) + r"(?!\w)", " ", t)
    fillers = sorted(_syn_cfg().get("fillers") or [], key=len, reverse=True)
    t = " ".join(t.split())
    changed = True
    while changed and t:
        changed = False
        for f in fillers:
            if t == f or t.startswith(f + " "):
                t = t[len(f):].strip()
                changed = True
    return t


def apply_synonyms(text):
    t = text
    for canon, variants in (_syn_cfg().get("synonyms") or {}).items():
        for v in sorted(variants, key=len, reverse=True):
            t = re.sub(r"(?<!\w)" + re.escape(norm(v)) + r"(?!\w)", norm(canon), t)
    return t


# ---------------------------------------------------------------- нечёткое сравнение
try:
    from rapidfuzz import fuzz as _fuzz
except Exception:
    _fuzz = None


def ratio(a, b):
    """Похожесть 0..100. rapidfuzz, если установлен, иначе difflib."""
    if not a or not b:
        return 0.0
    if _fuzz is not None:
        return float(_fuzz.ratio(a, b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100


# ---------------------------------------------------------------- топонимы
def _forms(word):
    w = word
    out = {w}
    if w.endswith("ия"):
        s = w[:-2]
        out |= {s + e for e in ("ия", "ии", "ию", "ией")}
    elif w.endswith("а"):
        s = w[:-1]
        soft = s[-1:] in "гкхжшчщ"
        out |= {s + e for e in (("и" if soft else "ы"), "е", "у", "ой", "ою")}
    elif w.endswith("я"):
        s = w[:-1]
        out |= {s + e for e in ("и", "е", "ю", "ей", "ею")}
    elif w.endswith("ь"):
        s = w[:-1]
        out |= {s + e for e in ("и", "ью", "я", "ю", "е")}
    elif w.endswith("й"):
        s = w[:-1]
        out |= {s + e for e in ("я", "ю", "ем", "е")}
    elif w[-1:] in "аеиоуыэюя":
        pass
    else:
        out |= {w + e for e in ("а", "у", "ом", "е", "ем")}
    return out


class Places:
    def __init__(self):
        d = load_json("places.json", {}) or {}
        self.canon = {}
        for city in d.get("cities", []):
            for form in self._variants(city):
                self.canon.setdefault(form, city)
        for alias, city in (d.get("aliases") or {}).items():
            for form in self._variants(alias):
                self.canon[form] = city
        self.canon_names = {norm(c): c for c in d.get("cities", [])}

    @staticmethod
    def _variants(name):
        n = norm(name).replace("-", " ")
        parts = n.split()
        forms = set()
        if len(parts) == 1:
            forms |= _forms(parts[0])
        elif len(parts) >= 2 and "на" in parts:                  # ростов на дону
            for f in _forms(parts[0]):
                forms.add(" ".join([f] + parts[1:]))
            forms.add(n)
        else:
            for f in _forms(parts[-1]):
                forms.add(" ".join(parts[:-1] + [f]))
            forms.add(n)
        forms.add(n)
        return forms

    def find(self, text):
        """-> (город или None, уверенность 0..1, кандидаты). Уверенность < 0.9: переспросить."""
        toks = norm(text).replace("-", " ").split()
        for n in (4, 3, 2, 1):
            for i in range(len(toks) - n + 1):
                g = " ".join(toks[i:i + n])
                if g in self.canon:
                    return self.canon[g], 1.0, []
        cands = []
        m = get_morph()
        if m is not None:
            for tok in toks:
                if len(tok) < 3 or tok in _NOT_PLACES:
                    continue
                try:
                    for p in m.parse(tok)[:3]:
                        if "Geox" in p.tag and p.score >= 0.2:
                            cands.append((_title(p.normal_form), 0.9))
                            break
                except Exception:
                    pass
            if cands:
                return cands[0][0], cands[0][1], [c[0] for c in cands]
        preps = {"в", "во", "из", "до", "по", "для", "от", "к", "около", "возле", "под", "над"}
        scored = []
        for i, tok in enumerate(toks):
            if len(tok) < 4 or tok in _NOT_PLACES:
                continue
            if i > 0 and toks[i - 1] not in preps and len(toks) > 3:
                continue
            for form, city in self.canon.items():
                sc = ratio(tok, form)
                if sc >= 80:
                    scored.append((sc, city))
        scored.sort(reverse=True)
        if scored:
            names = []
            for _, c in scored:
                if c not in names:
                    names.append(c)
            return names[0], scored[0][0] / 100.0, names[:3]
        return None, 0.0, []


_NOT_PLACES = set("""погода погоду погоды погоде сегодня завтра сейчас какая будет градусов градусы температура
температуру улице скажи пожалуйста сколько послезавтра днем вечером утром ночью ветер дождь снег""".split())


def _title(name):
    return "-".join("-".join(p.capitalize() if p != "на" else p for p in seg.split("-")) for seg in [name]) \
        if "-" in name else " ".join(w.capitalize() for w in name.split())


_places = None


def places():
    global _places
    if _places is None:
        _places = Places()
    return _places


# ---------------------------------------------------------------- время для перемотки
NUM = {"ноль": 0, "один": 1, "одна": 1, "одну": 1, "одной": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3,
       "четыре": 4, "четырех": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
       "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
       "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
       "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80,
       "девяносто": 90, "сто": 100, "двести": 200, "триста": 300}
HALF = {"полторы": 1.5, "полтора": 1.5}


def parse_timecode(raw):
    """«на три минуты», «на 1:30», «на полторы минуты», «в самый конец», «на 10 секунд вперёд».
    -> ("abs", сек) | ("rel", ±сек) | ("end", 0) | None."""
    t = raw.lower().replace("ё", "е")
    if re.search(r"(?:в|до)\s+(?:самый\s+|самого\s+)?(?:конец|конца|концу)", t) or "до конца" in t:
        return ("end", 0)
    if re.search(r"(?:в|на|с)\s+(?:самое\s+)?начал", t):
        return ("abs", 0)
    rel = re.search(r"вперед|назад", t)
    sign = -1 if "назад" in t else 1
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
    total = None
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        total = a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b
    else:
        toks = re.findall(r"\d+|[а-я]+", t)
        cur, got, total, prev_num = None, False, 0.0, False
        for w in toks:
            if w.isdigit():
                cur = (cur or 0) + int(w) if prev_num else int(w)
                prev_num = True
            elif w in NUM:
                cur = (cur or 0) + NUM[w] if prev_num else NUM[w]
                prev_num = True
            elif w in HALF:
                cur, prev_num = HALF[w], True
            elif w == "половиной" or w == "половина":
                cur = (cur or 0) + 0.5
            elif w == "полчаса":
                total += 1800
                got = True
            elif w in ("полминуты", "полминутки"):
                total += 30
                got = True
            elif w.startswith("час"):
                total += (1 if cur is None else cur) * 3600
                cur, prev_num, got = None, False, True
            elif w.startswith("минут"):
                total += (1 if cur is None else cur) * 60
                cur, prev_num, got = None, False, True
            elif w.startswith("секунд"):
                total += (1 if cur is None else cur) * 1
                cur, prev_num, got = None, False, True
            else:
                prev_num = False
        if not got:
            mm = re.search(r"(?<!\d)(\d{1,2})\s(\d{2})(?!\d)", t)         # голос: «1:30» приходит как «1 30»
            if mm and int(mm.group(2)) < 60:
                total = int(mm.group(1)) * 60 + int(mm.group(2))
            else:
                return None
        total = int(total)
    if rel:
        return ("rel", sign * int(total))
    return ("abs", int(total))


def fmt_time(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------- деление на шаги
_SEP = re.compile(r"\s*(?:,|;|\bа\s+потом\b|\bи\s+потом\b|\bпотом\b|\bзатем\b|\bпосле\s+этого\b|\bдалее\b)\s*", re.I)


_NO_SPLIT_AFTER = {"как", "чтобы", "можно", "хочу", "надо", "нужно", "умеешь", "могу", "можешь", "когда", "почему",
                   "зачем", "что", "где", "не", "если", "ли", "бы"}
LEGACY_VERBS = {"скажи", "поищи", "загугли", "сыграй", "сверни", "заблокируй", "засеки", "пошути", "загадай"}


def split_clauses(raw, verbs):
    """«открой ютуб, включи видео и закрой ютуб» -> три фразы.
    Режет по запятым и словам «потом/затем», а также ПЕРЕД каждым глаголом-командой: распознанная речь приходит без
    знаков препинания. Союз «и» перед глаголом выбрасывается. Вопросы («как открыть файл») не режем."""
    verbs = set(verbs) | LEGACY_VERBS
    out = []
    for part in _SEP.split(raw.strip()):
        part = part.strip()
        if not part:
            continue
        words = part.split()
        cur = []
        for i, w in enumerate(words):
            nw = norm(w)
            prev = norm(words[i - 1]) if i else ""
            if cur and nw in verbs and prev not in _NO_SPLIT_AFTER:
                if cur and norm(cur[-1]) == "и":
                    cur.pop()
                if cur:
                    out.append(" ".join(cur))
                cur = []
            cur.append(w)
        if cur:
            out.append(" ".join(cur))
    return out


# ---------------------------------------------------------------- сопоставление команд
class Match:
    def __init__(self, cmd, args, how, score=100.0, trig=""):
        self.cmd, self.args, self.how, self.score, self.trig = cmd, args, how, score, trig


def _antonym(a, b):
    pairs = (_syn_cfg().get("antonyms") or [])
    for x, y in pairs:
        if (a.startswith(x) and b.startswith(y)) or (a.startswith(y) and b.startswith(x)):
            return True
    return False


def _trig_lemmas(cmd):
    if not hasattr(cmd, "_lem"):
        cmd._lem = [(t, lemmas(t)) for t in cmd.triggers]
    return cmd._lem


def match_command(text, commands, fuzzy_threshold=80):
    """Этапы: точное совпадение -> синонимы -> леммы -> нечёткое.
    Возвращает Match или None. how: exact | synonym | lemma | fuzzy (fuzzy всегда уточняется голосом)."""
    # 1. точное совпадение по началу слова (как раньше)
    m = _exact(text, commands)
    if m:
        m.how = "exact"
        return m
    # 2. синонимы
    t2 = apply_synonyms(text)
    if t2 != text:
        m = _exact(t2, commands)
        if m:
            m.how = "synonym"
            return m
    # 3. леммы: «открываешь ютуб», «включи музыке» -> совпадение по нормальным формам слов
    toks = t2.split()
    if toks:
        lt = lemmas(t2)
        for cmd in commands:
            for trig, tl in _trig_lemmas(cmd):
                k = len(tl)
                if not k:
                    continue
                for i in range(0, len(lt) - k + 1):
                    if lt[i:i + k] == tl:
                        return Match(cmd, " ".join(toks[i + k:]), "lemma", 95.0, trig)
    # 4. нечёткое: сравниваем начало фразы с каждой фразой-триггером
    best = None
    if toks:
        for cmd in commands:
            for trig in cmd.triggers:
                k = len(trig.split())
                for kk in {k, max(1, k - 1), k + 1}:
                    head = " ".join(toks[:kk])
                    if len(head) < 5 or len(trig) < 5:
                        continue
                    sc = ratio(head, trig)
                    if sc >= fuzzy_threshold and (best is None or sc > best.score) \
                            and not _antonym(head, trig):
                        best = Match(cmd, " ".join(toks[kk:]), "fuzzy", sc, trig)
    return best


def _exact(text, commands):
    for cmd in commands:
        for trig in cmd.triggers:
            m = re.search(r"(?<!\w)" + re.escape(trig), text)
            if m:
                return Match(cmd, text[m.end():].strip(), "exact", 100.0, trig)
    return None


def exact_match(text, commands):
    m = _exact(text, commands)
    if m:
        m.how = "exact"
    return m
