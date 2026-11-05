# -*- coding: utf-8 -*-
"""Живые ответы: шаблоны из config/responses.json, случайный выбор без повтора последних N=20,
учёт контекста (время суток, число шагов, был ли недавний сбой)."""
import collections
import datetime
import random
import re

from core.paths import load_json
from core.logsetup import get

log = get("responder")


class _Safe(dict):
    def __missing__(self, key):
        return ""


def day_period(hour=None):
    h = datetime.datetime.now().hour if hour is None else hour
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 17:
        return "day"
    if 17 <= h < 23:
        return "evening"
    return "night"


class Responder:
    def __init__(self, history=20, fallback=None, rng=None):
        self.data = load_json("responses.json", {}) or {}
        self.fallback = fallback or {}
        self.N = history
        self.rng = rng or random
        self.recent = collections.deque(maxlen=history)          # последние N фраз (общая история)
        self.by_key = collections.defaultdict(lambda: collections.deque(maxlen=history))
        self.last_failed = False

    def reload(self):
        self.data = load_json("responses.json", {}) or {}

    def note(self, ok):
        """Сообщаем, как закончилось прошлое действие: после сбоя тон чуть меняется."""
        self.last_failed = not ok

    # ------------------------------------------------------------------
    def _variants(self, key):
        v = self.data.get(key)
        if not v:
            v = self.fallback.get(key)
        return v or []

    @staticmethod
    def _norm_variant(v):
        return v if isinstance(v, dict) else {"t": v}

    def _allowed(self, v, steps, period):
        if "period" in v and period not in v["period"]:
            return False
        if "min_steps" in v and steps < v["min_steps"]:
            return False
        if "max_steps" in v and steps > v["max_steps"]:
            return False
        if v.get("after_fail") and not self.last_failed:
            return False
        return True

    def pick(self, key, steps=1, period=None):
        raw = [self._norm_variant(v) for v in self._variants(key)]
        if not raw:
            raise KeyError(key)
        period = period or day_period()
        ok = [v for v in raw if self._allowed(v, steps, period)] or [self._norm_variant(raw[0])]
        hist = self.by_key[key]
        recent = set(self.recent) | set(hist)
        special = [v for v in ok if v.get("after_fail") or v.get("min_steps") or "period" in v]
        fresh = [v for v in ok if v["t"] not in recent]
        sp_fresh = [v for v in special if v["t"] not in recent]
        if sp_fresh and self.rng.random() < 0.6:                  # контекстные варианты (время суток, сбой, длинный сценарий)
            fresh = sp_fresh
        if not fresh:                                             # всё уже было: забываем историю ключа, кроме последней фразы
            last = hist[-1] if hist else None
            hist.clear()
            fresh = [v for v in ok if v["t"] != last] or ok
        choice = self.rng.choice(fresh)
        hist.append(choice["t"])
        self.recent.append(choice["t"])
        return choice["t"]

    def say(self, key, **kw):
        steps = int(kw.pop("steps", kw.get("n", 1) or 1) or 1)
        if "n" in kw:
            n = int(kw["n"] or 0)
            kw["ns"] = f"{n} " + ("шаг", "шага", "шагов")[0 if n % 10 == 1 and n % 100 != 11 else
                                                         1 if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 2]
        if key == "error" and not kw.get("x"):
            key = "error_plain"
        tpl = self.pick(key, steps)
        bridges = self.data.get("_bridges") or []
        if (key in (self.data.get("_bridge_keys") or []) and bridges and not self.last_failed
                and tpl[:1].isalpha() and self.rng.random() < float(self.data.get("_bridge_prob", 0.12))):
            tpl = self.rng.choice(bridges) + " " + tpl[0].lower() + tpl[1:]
        try:
            return tpl.format_map(_Safe(**kw)).strip()
        except Exception:
            log.exception("шаблон %s", key)
            return re.sub(r"\{[^}]*\}", "", tpl).strip()
