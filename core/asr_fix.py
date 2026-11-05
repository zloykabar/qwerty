# -*- coding: utf-8 -*-
"""Логика смешанного распознавания ru-en: выбор языка, выбор лучшего варианта из нескольких
проходов, исправление частых ошибок распознавания.

Чистый Python без тяжёлых зависимостей: этот модуль импортирует и фоновый распознаватель
(whisper_worker.py), и тесты на Linux.
"""
import json
import os
import re

_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)

# Языки Whisper: как мы называем у себя -> как понимает модель.
LANG_NAMES = {"ru": "russian", "en": "english"}
_RU_ALIASES = {"ru", "russian", "русский", "рус"}
_EN_ALIASES = {"en", "english", "английский", "англ"}
_MIXED_ALIASES = {"ru-en", "ruen", "mixed", "смешанный", "ru+en"}


def load_json(base_dir, rel, default=None):
    try:
        with open(os.path.join(base_dir, "config", rel), encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def load_hotwords(base_dir):
    """Английские названия для подсказки распознавателю: из app_aliases.json и из словаря правок."""
    words = []
    d = load_json(base_dir, "app_aliases.json", {}) or {}
    words += list(d.get("hotwords") or [])
    fixes = (load_json(base_dir, "asr_corrections.json", {}) or {}).get("fixes") or {}
    words += [v for v in fixes.values() if _LAT.search(v or "")]
    seen, out = set(), []
    for w in words:
        k = (w or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(w.strip())
    return out


def load_fixes(base_dir):
    return (load_json(base_dir, "asr_corrections.json", {}) or {}).get("fixes") or {}


def prompt_text(hotwords, limit=60):
    """Подсказка-контекст для Whisper: перечисление названий помогает ему узнать их."""
    words = [w for w in hotwords if w][:limit]
    if not words:
        return ""
    return "Джарвис, открой " + ", ".join(words) + "."


def language_plan(mode, second_pass=True):
    """Какие проходы распознавания делать.

    primary — основной язык (None = автоопределение);
    extra   — дополнительные языки на случай, если автоопределение неуверенно.

    Для «ru» и «en» — один проход без лишних затрат.
    Для «auto» и «ru-en» — один проход с автоопределением (он и различает языки по фразе),
    а если модель сомневается — ещё два коротких прохода (ru и en), из которых берём лучший.
    """
    m = (mode or "auto").strip().lower()
    if m in _RU_ALIASES:
        return {"primary": "ru", "extra": []}
    if m in _EN_ALIASES:
        return {"primary": "en", "extra": []}
    extra = ["ru", "en"] if second_pass else []
    return {"primary": None, "extra": extra}


def is_unsure(lang, prob, threshold=0.6):
    """Стоит ли перепроверить фразу вторым проходом: язык не русский/английский или определён неуверенно."""
    if not lang:
        return True
    if lang not in ("ru", "en"):
        return True
    return float(prob or 0.0) < threshold


def latin_ratio(text):
    t = text or ""
    letters = len(_CYR.findall(t)) + len(_LAT.findall(t))
    if not letters:
        return 0.0
    return len(_LAT.findall(t)) / letters


def score(cand, hotwords):
    """Оценка варианта распознавания: уверенность модели + небольшой бонус за узнанные названия."""
    if not cand or not (cand.get("text") or "").strip():
        return -99.0
    base = float(cand.get("score") or 0.0)
    low = cand["text"].lower()
    hits = sum(1 for w in hotwords if w and w.lower() in low)
    return base + 0.15 * min(hits, 2)


def pick_best(candidates, hotwords):
    """Лучший вариант из нескольких проходов. Уверенность важнее, названия — при близких оценках."""
    good = [c for c in candidates if c and (c.get("text") or "").strip()]
    if not good:
        return {"text": "", "score": -99.0, "lang": "", "lang_prob": 0.0}
    return max(good, key=lambda c: score(c, hotwords))


def apply_fixes(text, fixes):
    """Исправляет частые ошибки распознавания. Ключи — как услышала модель, значения — как правильно.
    Ищем по границам слов, регистр не важен; регистр значения сохраняется."""
    t = text or ""
    for bad in sorted(fixes, key=len, reverse=True):
        good = fixes[bad]
        if not bad or not good:
            continue
        t = re.sub(r"(?<!\w)" + re.escape(bad) + r"(?!\w)", good, t, flags=re.I)
    return " ".join(t.split())
