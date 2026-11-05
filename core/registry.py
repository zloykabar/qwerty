# -*- coding: utf-8 -*-
"""Единый реестр действий (tool registry). Из него собираются сценарии и пользовательские команды.
Каждое действие: имя, параметры, уровень риска (safe | confirm | danger), обработчик."""
import threading

from core.logsetup import get
from core.paths import load_json
from core import nlu

log = get("action")


class Result:
    def __init__(self, ok=True, msg="", data=None):
        self.ok, self.msg, self.data = ok, msg, data

    def __repr__(self):
        return f"Result(ok={self.ok}, msg={self.msg!r})"


class Param:
    def __init__(self, name, prompt, kind="text", required=True):
        self.name, self.prompt, self.kind, self.required = name, prompt, kind, required


class Action:
    def __init__(self, name, title, handler, params=(), risk="safe", names=(), extract=None, describe=None,
                 undo=None, timeout=30):
        self.name, self.title, self.handler = name, title, handler
        self.params = list(params)
        self.risk = risk
        self.names = list(names)             # как это называет человек в мастере: «открыть программу»
        self.extract = extract               # извлечение параметров из речи
        self._describe = describe
        self.undo = undo                     # имя действия для отката или None
        self.timeout = timeout

    def describe(self, params):
        if self._describe:
            try:
                return self._describe(params)
            except Exception:
                pass
        vals = ", ".join(str(v) for v in params.values() if v not in (None, ""))
        return f"{self.title}: {vals}" if vals else self.title


class Ctx:
    """Контекст выполнения: подтверждение голосом, признак отмены."""
    def __init__(self, core, cancel=None):
        self.core = core
        self.cancel = cancel or threading.Event()

    def confirm(self, text):
        return self.core.confirm_blocking(text)


class Registry:
    def __init__(self, core):
        self.core = core
        self.actions = {}
        self.phrases = []                    # [(триггер(токены), имя действия)], длинные первыми
        self._verbs = set()

    # ---------------------------------------------------------------- регистрация
    def register(self, action):
        self.actions[action.name] = action

    def load_phrases(self):
        data = load_json("action_phrases.json", []) or []
        items = []
        for d in data:
            if d.get("action") not in self.actions:
                continue
            for t in d.get("triggers", []):
                tt = nlu.norm(t).split()
                if tt:
                    items.append((tt, d["action"]))
        items.sort(key=lambda x: -len(x[0]))
        self.phrases = items
        self._verbs = {tt[0] for tt, _ in items} - {"на", "без", "новая", "полноэкранный", "поставь"} | {"поставь"}

    def verbs(self):
        return self._verbs

    def get(self, name):
        return self.actions.get(name)

    # ---------------------------------------------------------------- выполнение
    def execute(self, name, params=None, ctx=None):
        params = dict(params or {})
        act = self.actions.get(name)
        if not act:
            return Result(False, "такого действия нет: " + name)
        for p in act.params:
            if p.required and params.get(p.name) in (None, ""):
                return Result(False, f"не хватает параметра «{p.name}»")
        ctx = ctx or Ctx(self.core)
        desc = act.describe(params)
        log.info("действие %s %s", name, params)
        if act.risk in ("confirm", "danger"):
            if not ctx.confirm(desc + "?"):
                log.info("действие %s отклонено пользователем", name)
                return Result(False, "ты не подтвердил, так что ничего не трогаю")
        try:
            res = act.handler(self.core, params, ctx)
        except Exception as e:
            log.exception("сбой действия %s", name)
            return Result(False, str(e) or e.__class__.__name__)
        if res is None:
            res = Result(True, "")
        elif isinstance(res, str):
            res = Result(True, res)
        log.info("результат %s: ok=%s %s", name, res.ok, res.msg)
        return res

    def run_with_timeout(self, name, params, ctx, timeout=None):
        act = self.actions.get(name)
        timeout = timeout or (act.timeout if act else 30)
        if act and act.risk in ("confirm", "danger"):
            timeout += self.core.safety.confirm_timeout + 5
        box = []
        th = threading.Thread(target=lambda: box.append(self.execute(name, params, ctx)), daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            ctx.cancel.set()
            log.warning("действие %s не уложилось в %s с", name, timeout)
            return Result(False, "не уложился во время")
        return box[0]

    # ---------------------------------------------------------------- разбор речи
    def match_clause(self, clause):
        """Фраза -> (Action, params) по самому длинному совпадающему началу. None, если действие не найдено."""
        toks = clause.split()
        ntoks = [nlu.norm(t) for t in toks]
        for tt, name in self.phrases:
            k = len(tt)
            if len(ntoks) < k:
                continue
            head = ntoks[:k]
            if head == tt or [nlu.lemma(w) for w in head] == [nlu.lemma(w) for w in tt]:
                act = self.actions[name]
                rest = " ".join(toks[k:])
                params = act.extract(rest, clause) if act.extract else {}
                override = params.pop("_action", None) if params else None
                if override and override in self.actions:
                    act = self.actions[override]
                return act, params or {}
        return None

    def find_for_wizard(self, text, limit=3):
        """Что человек имел в виду под «что сделать»: ближайшие действия по именам и названию."""
        t = nlu.norm(text)
        scored = []
        for act in self.actions.values():
            best = 0.0
            for n in act.names + [act.title]:
                nn = nlu.norm(n)
                s = nlu.ratio(t, nn)
                if nn and (nn in t or t in nn):
                    s = max(s, 92.0)
                tl, nl = set(nlu.lemmas(t)), set(nlu.lemmas(nn))
                if tl and nl:
                    s = max(s, 100.0 * len(tl & nl) / max(len(tl), len(nl)))
                best = max(best, s)
            if best >= 45:
                scored.append((best, act))
        scored.sort(key=lambda x: -x[0])
        return scored[:limit]

    def catalog_text(self):
        return ", ".join(a.names[0] if a.names else a.title for a in self.actions.values() if a.names)
