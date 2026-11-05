# -*- coding: utf-8 -*-
"""Мастер команд: диалог «по одному простому вопросу», голосом или текстом. Человек не пишет ни кода, ни путей.
Все действия берутся из реестра; если нужного действия нет, мастер говорит об этом и предлагает ближайшие."""
import re

from core import nlu
from core.logsetup import get

log = get("wizard")
CANCEL = {"отмена", "отмени", "стоп", "хватит", "не надо", "выйти", "выход", "отбой"}
SKIP = {"пропусти", "ничего", "не надо", "никак", "без ответа", "нет", "пропустить", "пусто"}
INTRO = re.compile(r"^(?:когда я говорю|когда я скажу|если я скажу|на фразу|по фразе|команда|фраза)\s+", re.I)
TAIL = re.compile(r"\s+(?:то|тогда|ты|джарвис|пожалуйста)$", re.I)


class Wizard:
    def __init__(self, core):
        self.core = core
        self.d = None
        self.cur = None            # действие, параметры которого сейчас собираем
        self.vals = {}
        self.queue = []
        self.tries = 0

    # ------------------------------------------------------------ утилиты
    @property
    def reg(self):
        return self.core.registry

    def _ask(self, question, cb, seconds=60, yn=False):
        def wrapped(raw):
            t = nlu.norm(raw)
            if t in CANCEL:
                self.d = None
                return "Хорошо, мастер закрыт, ничего не сохранял."
            return cb(raw)
        self.core.ask(question, wrapped, seconds=seconds, raw=True)

    def _yn(self, question, on_yes, on_no=None):
        yes = self.core.safety.yes | {"да", "ага", "угу", "давай"}

        def cb(raw):
            t = nlu.norm(raw)
            if t and (t in yes or t.split()[0] in yes):
                return on_yes() or ""
            return (on_no() if on_no else "Ладно.") or ""
        self._ask(question, cb, 40)

    def _clean_phrase(self, raw):
        t = nlu.strip_fillers(nlu.norm(raw), self.core.wake_words)
        t = INTRO.sub("", t)
        return TAIL.sub("", t).strip()

    # ------------------------------------------------------------ один шаг за другим
    def start(self, prefill_phrase=""):
        self.d = {"phrases": [], "steps": [], "reply": ""}
        self.tries = 0
        if prefill_phrase:
            self.d["phrases"] = [prefill_phrase]
            self._ask_action()
            return ""
        self._ask("Как назовём команду? Скажи фразу, по которой я её запущу.", self._on_name)
        return ""

    def _on_name(self, raw):
        phrase = self._clean_phrase(raw)
        if not phrase:
            return "Не расслышал фразу. Скажи «добавь команду» ещё раз."
        conflict = self.core.find_conflict(phrase)
        if conflict:
            self._yn(f"Фраза «{phrase}» уже занята: {conflict}. Переопределить её?",
                     lambda: self._take_name(phrase), lambda: "Хорошо, оставляю как было. Скажи «добавь команду» с другой фразой.")
            return ""
        return self._take_name(phrase)

    def _take_name(self, phrase):
        self.d["phrases"] = [phrase]
        self.tries = 0
        self._ask_action()
        return ""

    def _ask_action(self):
        q = "Что сделать?" if not self.d["steps"] else "Что сделать следующим шагом?"
        self._ask(q + " Например: открыть программу, открыть сайт, найти файл, создать папку, включить музыку.", self._on_action)

    def _on_action(self, raw):
        cands = [(s, a) for s, a in self.reg.find_for_wizard(raw, 4) if a.names]
        if not cands or cands[0][0] < 60:
            self.tries += 1
            sugg = ", ".join((a.names[0]) for _, a in cands[:3]) or self.reg.catalog_text()
            if self.tries >= 3:
                self.d = None
                return "Не получается подобрать действие, останавливаюсь. Ничего не сохранил."
            self._ask(f"Такого действия у меня нет. Ближайшие: {sugg}. Что выбираем?", self._on_action)
            return ""
        score, act = cands[0]
        if score < 80:
            self._yn(f"Ты имел в виду «{act.names[0]}»?", lambda: self._begin_params(act),
                     lambda: self._ask_action() or "Тогда скажи иначе.")
            return ""
        return self._begin_params(act)

    def _begin_params(self, act):
        self.cur, self.vals, self.queue, self.tries = act, {}, list(act.params), 0
        self._next_param()
        return ""

    def _next_param(self):
        if not self.queue:
            return self._finish_step()
        p = self.queue[0]
        q = ("С чем? " if not self.vals else "") + p.prompt
        if not p.required:
            q += " Или скажи «пропусти»."
        self._ask(q, lambda raw, p=p: self._on_param(p, raw))
        return ""

    def _on_param(self, p, raw):
        t = nlu.norm(raw)
        if not p.required and t in SKIP:
            self.queue.pop(0)
            return self._next_param() or ""
        val, err = self._validate(p, raw)
        if err:
            self.tries += 1
            if self.tries >= 3:
                self.d = None
                return "Не получается, останавливаюсь. Ничего не сохранил."
            self._ask(err, lambda r, p=p: self._on_param(p, r))
            return ""
        self.vals[p.name] = val
        self.queue.pop(0)
        self.tries = 0
        return self._next_param() or ""

    def _validate(self, p, raw):
        """-> (значение, текст ошибки). Битые команды не создаём."""
        text = raw.strip()
        if p.kind == "app":
            name = self.core.resolve_app(text)
            if name:
                return text, None
            sugg = self.core.suggest_apps(text)
            return None, "Не нашёл программу «%s».%s Назови её иначе." % (text, (" Похожие: " + ", ".join(sugg) + ".") if sugg else "")
        if p.kind == "site":
            if self.core.site_url(nlu.norm(text)) or re.search(r"\.\w{2,}", text):
                return text, None
            return None, "Не знаю такой сайт. Назови адрес, например youtube.com."
        if p.kind == "time":
            tc = nlu.parse_timecode(text if text.lower().startswith("на ") else "на " + text)
            return ({"mode": tc[0], "sec": tc[1]}, None) if tc else (None, "Не понял время. Скажи, например, «на две минуты».")
        if p.kind == "number":
            tc = nlu.parse_timecode("на " + text)
            return (tc[1], None) if tc and tc[1] else (None, "Не понял, сколько секунд. Скажи число и слово «секунд».")
        if not text:
            return None, "Не расслышал. Повтори, пожалуйста."
        if p.name == "name":
            text = text[:1].upper() + text[1:]
        return text, None

    def _finish_step(self):
        self.d["steps"].append({"action": self.cur.name, "params": dict(self.vals), "on_error": "abort",
                                **({"undo": {"action": self.cur.undo, "params": {"target": self.vals.get("target")}
                                             if self.cur.undo == "app.close" else {}}} if self.cur.undo else {})})
        self._yn("Ещё шаг?", self._ask_action, self._ask_reply)
        return ""

    def _ask_reply(self):
        self._ask("Как мне ответить, когда всё будет готово? Можно сказать «пропусти».", self._on_reply)
        return ""

    def _on_reply(self, raw):
        t = nlu.norm(raw)
        self.d["reply"] = "" if t in SKIP else raw.strip()
        return self._summary()

    def _summary(self):
        lines = []
        for i, st in enumerate(self.d["steps"], 1):
            act = self.reg.get(st["action"])
            lines.append(f"{i}. " + (act.describe(st["params"]) if act else st["action"]))
        text = f"Итак, команда «{self.d['phrases'][0]}»: " + "; ".join(lines) + ". Сохранить?"
        self._yn(text, self._save, lambda: "Не сохраняю. Скажи «добавь команду», чтобы начать заново.")
        self.core.emit("msg", who="sys", text="\n".join(lines))
        return ""

    def _save(self):
        d, self.d = self.d, None
        if d.get("edit_id"):
            self.core.store.update(d["edit_id"], steps=d["steps"], reply=d["reply"])
        else:
            self.core.store.add(d["phrases"], d["steps"], d["reply"])
        self.core.reload_custom()
        return f"Готово. Скажи: Джарвис, {d['phrases'][0]}. Работает уже сейчас."

    # ------------------------------------------------------------ пакетное добавление
    def start_batch(self):
        self.batch = []
        self.bad = []
        self._ask("Диктуй список. Каждую команду так: «когда я говорю чат, открой телеграм». "
                  "Между командами говори «следующая». Закончишь, скажи «всё».", self._on_batch, 90)
        return ""

    def _on_batch(self, raw):
        t = nlu.norm(raw)
        finished = bool(re.search(r"(?:^|\s)(?:это\s+)?(?:всё|все|готово|конец|хватит)$", t))
        t = re.sub(r"\s*(?:это\s+)?(?:всё|все|готово|конец|хватит)$", "", t)
        for chunk in re.split(r"\s*(?:следующая|следующий|дальше|потом|;|ещё одна|еще одна)\s*", t):
            chunk = chunk.strip()
            if chunk:
                item = self._parse_chunk(chunk)
                (self.batch if item else self.bad).append(item or chunk)
        if not finished:
            self._ask("Записал. Продолжай или скажи «всё».", self._on_batch, 90)
            return ""
        return self._batch_summary()

    def _parse_chunk(self, chunk):
        words = chunk.split()
        verbs = self.reg.verbs() | {"скажи"}
        for i, w in enumerate(words):
            if i == 0 or w not in verbs:
                continue
            phrase = TAIL.sub("", INTRO.sub("", " ".join(words[:i]))).strip()
            action_text = " ".join(words[i:])
            if not phrase:
                continue
            if w == "скажи":
                return {"phrases": [phrase], "steps": [{"action": "say", "params": {"text": " ".join(words[i + 1:])}}]} \
                    if len(words) > i + 1 else None
            m = self.reg.match_clause(action_text)
            if not m:
                return None
            act, params = m
            if act.name in ("open.any", "app.open") and params.get("target") and not self.core.resolve_app(params["target"]):
                return None
            if any(p.required and params.get(p.name) in (None, "") for p in act.params):
                return None
            step = {"action": act.name, "params": params, "on_error": "abort"}
            if act.name == "open.any":
                step["action"] = "app.open"
            return {"phrases": [phrase], "steps": [step]}
        return None

    def _batch_summary(self):
        if not self.batch:
            return "Ни одной команды не разобрал. Попробуй: «когда я говорю чат, открой телеграм»."
        parts = [f"«{b['phrases'][0]}»" for b in self.batch]
        text = f"Разобрал {len(self.batch)}: " + ", ".join(parts) + "."
        if self.bad:
            text += f" Не понял: {'; '.join(self.bad)}."
        text += " Сохранить разобранные?"
        self.core.emit("msg", who="sys", text=text)

        def save():
            for b in self.batch:
                self.core.store.add(b["phrases"], b["steps"], "")
            self.core.reload_custom()
            return f"Сохранил {len(self.batch)}. Всё уже работает."
        self._yn(text, save, lambda: "Ничего не сохранил.")
        return ""

    # ------------------------------------------------------------ список, удаление, правка
    def list_text(self):
        items = self.core.store.items
        if not items:
            return "Своих команд пока нет. Скажи «добавь команду»."
        return "Твои команды: " + ", ".join(i["phrases"][0] for i in items) + "."

    def _pick(self, name):
        n = nlu.norm(name)
        for it in self.core.store.items:
            if any(nlu.norm(p) == n for p in it["phrases"]):
                return it
        best, sc = None, 0
        for it in self.core.store.items:
            s = nlu.ratio(n, nlu.norm(it["phrases"][0]))
            if s > sc:
                best, sc = it, s
        return best if sc >= 70 else None

    def delete(self, name):
        it = self._pick(name) if name else None
        if not it:
            self._ask("Какую команду удалить? Назови её фразу.", lambda r: self.delete(r) or "")
            return ""
        self._yn(f"Удалить команду «{it['phrases'][0]}»?",
                 lambda: (self.core.store.delete(it["id"]), self.core.reload_custom(), "Удалил.")[2])
        return ""

    def show(self, name):
        it = self._pick(name) if name else None
        if not it:
            return "Не нашёл такую команду. " + self.list_text()
        lines = []
        for i, st in enumerate(it["steps"], 1):
            act = self.reg.get(st["action"])
            lines.append(f"{i}. " + (act.describe(st["params"]) if act else st["action"]))
        self.core.emit("msg", who="sys", text=f"«{it['phrases'][0]}»:\n" + "\n".join(lines))
        return f"В команде «{it['phrases'][0]}» шагов: {len(lines)}. " + "; ".join(lines) + "."

    def edit(self, name):
        it = self._pick(name) if name else None
        if not it:
            self._ask("Какую команду изменить? Назови её фразу.", lambda r: self.edit(r) or "")
            return ""
        self.core.say(self.show(it["phrases"][0]))
        self._ask("Что поменять: фразу, ответ, добавить шаг, убрать шаг или переделать заново?",
                  lambda r: self._edit_choice(it, nlu.norm(r)))
        return ""

    def _edit_choice(self, it, t):
        store = self.core.store
        if "фраз" in t:
            def ph(raw):
                p = self._clean_phrase(raw)
                if p:
                    store.update(it["id"], phrases=[p])
                    self.core.reload_custom()
                    return f"Теперь команда запускается по фразе «{p}»."
                return "Не расслышал."
            self._ask("Какая будет новая фраза?", ph)
        elif "ответ" in t:
            def rp(raw):
                store.update(it["id"], reply="" if nlu.norm(raw) in SKIP else raw.strip())
                self.core.reload_custom()
                return "Ответ обновил."
            self._ask("Как отвечать, когда команда выполнена?", rp)
        elif "добав" in t:
            self.d = {"phrases": it["phrases"], "steps": list(it["steps"]), "reply": it.get("reply", ""), "edit_id": it["id"]}
            self.tries = 0
            self._ask_action()
        elif "убрать" in t or "удал" in t:
            def rm(raw):
                m = re.search(r"\d+", raw)
                num = int(m.group()) if m else nlu.NUM.get(nlu.norm(raw).split()[0] if nlu.norm(raw) else "", 0)
                if not (1 <= num <= len(it["steps"])) or len(it["steps"]) == 1:
                    return "Такой шаг убрать нельзя: в команде должен остаться хотя бы один."
                steps = [s for k, s in enumerate(it["steps"], 1) if k != num]
                store.update(it["id"], steps=steps)
                self.core.reload_custom()
                return f"Шаг {num} убрал."
            self._ask("Какой по номеру шаг убрать?", rm)
        elif "заново" in t or "перед" in t:
            store.delete(it["id"])
            self.core.reload_custom()
            self.start(it["phrases"][0])
        else:
            return "Не понял, что поменять. Скажи «измени команду» ещё раз."
        return ""
