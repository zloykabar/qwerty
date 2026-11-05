# -*- coding: utf-8 -*-
"""Память Джарвиса о вас: факты хранятся в profile.json и подмешиваются в каждый разговор."""
import json
import re

MAX_FACTS = 60


def _n(s):
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower().replace("ё", "е")).split())


class Profile:
    def __init__(self, path):
        self.path = path
        self.facts = []
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            self.facts = [str(x) for x in data.get("facts", []) if str(x).strip()]
        except FileNotFoundError:
            pass
        except Exception:
            self.facts = []

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"facts": self.facts}, f, ensure_ascii=False, indent=2)

    def add(self, fact):
        fact = fact.strip().rstrip(".!")[:250]
        if not fact:
            return False
        if any(_n(fact) == _n(x) for x in self.facts):
            return True
        self.facts.append(fact[:1].upper() + fact[1:])
        self.facts = self.facts[-MAX_FACTS:]
        self._save()
        return True

    def forget(self, query):
        """Удаляет факты, где встречаются слова запроса. Возвращает сколько удалено."""
        words = [w for w in _n(query).split() if len(w) >= 3]
        if not words:
            return 0
        keep = [x for x in self.facts if not any(w[:max(4, len(w) - 2)] in _n(x) for w in words)]
        removed = len(self.facts) - len(keep)
        if removed:
            self.facts = keep
            self._save()
        return removed

    def clear(self):
        n = len(self.facts)
        self.facts = []
        self._save()
        return n

    def prompt(self):
        if not self.facts:
            return ""
        return ("Что пользователь сам рассказал о себе: " + "; ".join(self.facts) + ". "
                "Учитывай это в ответах, когда уместно, но не пересказывай без повода.")
