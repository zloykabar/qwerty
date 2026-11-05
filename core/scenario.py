# -*- coding: utf-8 -*-
"""Движок сценариев на конечном автомате.
Состояния сценария: idle -> running <-> paused -> done | failed | cancelled (и обратно в running при перезапуске).
Состояния шага: pending -> running -> ok | failed | skipped.
Сценарий = список шагов {action, params, on_error, timeout, undo}. Ошибка шага -> политика on_error
(abort | skip | retry:N), затем безопасный откат: уже сделанные шаги с undo выполняются в обратном порядке."""
import re
import threading
import time
import traceback

from core import nlu
from core.logsetup import get
from core.registry import Ctx

log = get("scenario")

TRANSITIONS = {
    "idle": {"running"},
    "running": {"paused", "done", "failed", "cancelled"},
    "paused": {"running", "cancelled"},
    "done": set(),
    "failed": {"running"},
    "cancelled": {"running"},
}
PAUSE_WORDS = {"стоп", "стой", "пауза", "притормози", "подожди секунду"}
RESUME_WORDS = {"дальше", "продолжай", "продолжи", "поехали", "давай дальше"}
SKIP_WORDS = {"пропусти", "пропустить", "пропусти шаг", "пропусти это"}
CANCEL_WORDS = {"отмена", "отмени", "отбой", "прекрати", "хватит", "отмени сценарий"}
RESTART_WORDS = {"повтори сценарий", "продолжи сценарий", "запусти сценарий заново", "попробуй ещё раз", "попробуй еще раз"}


class Run:
    def __init__(self, title, steps, params):
        self.title, self.steps, self.params = title, steps, dict(params)
        self.state = "idle"
        self.idx = 0
        self.step_state = ["pending"] * len(steps)
        self.completed = set()            # номера шагов, выполненных успешно: при перезапуске не дублируются
        self.cancel = threading.Event()
        self.pause_ev = threading.Event()
        self.pause_ev.set()
        self.skip_next = False
        self.thread = None
        self.reply = ""
        self.lock = threading.Lock()

    def goto(self, new):
        with self.lock:
            if new not in TRANSITIONS[self.state]:
                log.warning("недопустимый переход %s -> %s", self.state, new)
                return False
            log.info("сценарий «%s»: %s -> %s", self.title, self.state, new)
            self.state = new
            return True


def _subst(value, params):
    """«{query}» -> значение параметра. Если параметра нет, бросаем KeyError (шаг пропускается)."""
    if isinstance(value, dict):
        return {k: _subst(v, params) for k, v in value.items()}
    if isinstance(value, str):
        m = re.fullmatch(r"\{(\w+)\}", value)
        if m:
            if params.get(m.group(1)) in (None, ""):
                raise KeyError(m.group(1))
            return params[m.group(1)]
        def rep(mm):
            if params.get(mm.group(1)) in (None, ""):
                raise KeyError(mm.group(1))
            return str(params[mm.group(1)])
        return re.sub(r"\{(\w+)\}", rep, value)
    return value


def strip_time_phrase(text):
    t = re.sub(r"\s+(?:на|в)\s+(?:\d+[:.]\d{2}|[а-яё\d]+(?:\s+[а-яё\d]+){0,3}?\s+(?:минут\w*|секунд\w*|час\w*))"
               r"(?:\s+(?:вперед|вперёд|назад))?\s*$", "", text.strip(), flags=re.I)
    return t.strip()


class ScenarioEngine:
    def __init__(self, core):
        self.core = core
        self.run = None              # текущий или последний запуск
        self.last_adhoc = None

    # -------------------------------------------------------------- состояние
    @property
    def active(self):
        return self.run is not None and self.run.state in ("running", "paused")

    # -------------------------------------------------------------- разбор речи
    def build_adhoc(self, raw):
        """«открой ютуб, включи видео и закрой ютуб» -> (steps, неразобранные фразы). Иначе None."""
        reg = self.core.registry
        clauses = nlu.split_clauses(raw, reg.verbs())
        if len(clauses) < 2:
            return None
        steps, unknown = [], []
        for c in clauses:
            m = reg.match_clause(c)
            if m:
                act, params = m
                steps.append(self._make_step(act.name, params, act.undo, c))
            elif self.core.legacy_match(c):                       # обычная команда Джарвиса («скажи время», «пошути»)
                steps.append(self._make_step("legacy", {"text": c}, None, c))
            else:
                unknown.append(c)
        if len(steps) < 2:
            return None
        return steps, unknown

    @staticmethod
    def _make_step(action, params, undo=None, clause=""):
        st = {"action": action, "params": dict(params), "on_error": "abort"}
        if undo:
            st["undo"] = {"action": undo, "params": {"target": params.get("target")} if undo == "app.close" else {}}
        if clause:
            st["clause"] = clause
        return st

    def try_handle_text(self, raw):
        """Вызывается из Core.handle до одиночных команд. True, если фраза ушла в сценарий."""
        built = self.build_adhoc(raw)
        if not built:
            return False
        steps, unknown = built
        self.last_adhoc = steps
        if unknown:
            self.core.ask_yes_no(f"Часть «{unknown[0]}» я не понял. Выполнить только остальное?",
                                 lambda: self.start("Цепочка команд", steps, {}) or "")
            return True
        self.start("Цепочка команд", steps, {})
        return True

    # -------------------------------------------------------------- запуск
    def missing_params(self, steps, params):
        """Какие обязательные параметры не названы. Спрашиваем про ОДИН, остальным шагам не мешаем."""
        reg = self.core.registry
        out = []
        for i, st in enumerate(steps):
            act = reg.get(st["action"])
            if not act or st.get("optional"):
                continue
            for p in act.params:
                if not p.required:
                    continue
                try:
                    val = _subst(st["params"].get(p.name), params) if p.name in st["params"] else None
                except KeyError:
                    val = None
                if val in (None, ""):
                    out.append((i, p))
        return out

    def start(self, title, steps, params, reply=""):
        miss = self.missing_params(steps, params)
        if miss:
            i, p = miss[0]
            question = p.prompt

            def got(raw):
                val = self._parse_answer(p, raw)
                if val in (None, ""):
                    self._finish_start(title, steps, params, [i], reply)
                    return self.core.ph("not_understood") + " Этот шаг пропущу."
                st = steps[i]
                st["params"][p.name] = val
                self._finish_start(title, steps, params, [j for j, _ in miss[1:] if j != i], reply)
                return ""
            self.core.ask(self.core.ph("clarify", x=question), got, seconds=30)
            return None
        return self._begin(title, steps, params, reply)

    def _finish_start(self, title, steps, params, dropped, reply=""):
        keep = [s for k, s in enumerate(steps) if k not in set(dropped)]
        if dropped:
            self.core._say_async("Шаги, для которых не хватало данных, пропускаю.")
        if keep:
            self._begin(title, keep, params, reply)

    @staticmethod
    def _parse_answer(param, raw):
        if param.kind == "time":
            tc = nlu.parse_timecode(raw if raw.lower().startswith("на ") else "на " + raw)
            return {"mode": tc[0], "sec": tc[1]} if tc else None
        if param.kind == "number":
            tc = nlu.parse_timecode("на " + raw)
            return tc[1] if tc else None
        return raw.strip()

    def _begin(self, title, steps, params, reply=""):
        run = Run(title, steps, params)
        run.reply = reply
        self.run = run
        n = len(steps)
        if n > 1:
            self.core._say_async(self.core.ph("scenario_start", n=n))
        else:
            self.core._say_async(self.core.ph("confirm"))
        self._spawn(run)
        return run

    def _spawn(self, run):
        run.goto("running")
        run.cancel.clear()
        run.pause_ev.set()
        run.thread = threading.Thread(target=self._loop, args=(run,), daemon=True, name="scenario")
        run.thread.start()

    # -------------------------------------------------------------- выполнение
    def _loop(self, run):
        core = self.core
        ctx = Ctx(core, run.cancel)
        done_steps = []
        try:
            for i, step in enumerate(run.steps):
                run.idx = i
                if run.cancel.is_set():
                    break
                while not run.pause_ev.is_set():                  # пауза: ждём «дальше» или «отмена»
                    if run.cancel.wait(0.2):
                        break
                if run.cancel.is_set():
                    break
                if i in run.completed:                            # шаг уже выполнен раньше: не дублируем
                    done_steps.append(i)
                    continue
                if run.skip_next:
                    run.skip_next = False
                    run.step_state[i] = "skipped"
                    core._say_async(core.ph("scenario_skip"))
                    continue
                try:
                    params = _subst(step.get("params", {}), run.params)
                except KeyError:
                    run.step_state[i] = "skipped"                 # параметр не назван: шаг тихо пропускаем
                    continue
                run.step_state[i] = "running"
                res = self._execute(step, params, ctx)
                if res.ok:
                    run.step_state[i] = "ok"
                    run.completed.add(i)
                    done_steps.append(i)
                    core.responder.note(True)
                    if step["action"] != "wait":
                        core._say_async(res.msg or core.ph("step_ok", i=i + 1))
                    continue
                run.step_state[i] = "failed"
                core.responder.note(False)
                policy = step.get("on_error", "abort")
                if policy == "skip":
                    core._say_async(f"Шаг {i + 1} не вышел ({res.msg}), иду дальше.")
                    continue
                core._say_async(core.ph("scenario_fail", i=i + 1, x=res.msg or "причина неизвестна"))
                self._rollback(run, done_steps, ctx)
                run.goto("failed")
                return
            if run.cancel.is_set():
                self._rollback(run, done_steps, ctx)
                run.goto("cancelled")
                core._say_async(core.ph("scenario_cancel"))
                return
            run.goto("done")
            core._say_async(run.reply or core.ph("done", n=len(run.steps)))
        except Exception:
            log.exception("сценарий упал")
            traceback.print_exc()
            self._rollback(run, done_steps, ctx)
            run.goto("failed") if "failed" in TRANSITIONS[run.state] else None
            core._say_async(core.ph("error"))

    def _execute(self, step, params, ctx):
        reg = self.core.registry
        retries = 0
        pol = str(step.get("on_error", ""))
        if pol.startswith("retry:"):
            try:
                retries = int(pol.split(":")[1])
            except ValueError:
                retries = 1
        res = reg.run_with_timeout(step["action"], params, ctx, step.get("timeout"))
        while not res.ok and retries > 0 and not ctx.cancel.is_set():
            retries -= 1
            time.sleep(1.0)
            res = reg.run_with_timeout(step["action"], params, ctx, step.get("timeout"))
        return res

    def _rollback(self, run, done_steps, ctx):
        """Закрываем то, что сценарий успел открыть (в обратном порядке)."""
        for i in reversed(done_steps):
            und = run.steps[i].get("undo")
            if not und:
                continue
            try:
                p = _subst(und.get("params", {}), run.params) if und.get("params") else {}
            except KeyError:
                continue
            try:
                self.core.registry.run_with_timeout(und["action"], p, Ctx(self.core), 10)
            except Exception:
                log.exception("откат шага %s", i)

    # -------------------------------------------------------------- управление голосом
    def control(self, text):
        """«стоп», «дальше», «пропусти», «отмена», «повтори сценарий». Возвращает ответ или None."""
        t = nlu.norm(text)
        run = self.run
        if run is None:
            return None
        if run.state in ("running", "paused"):
            if t in PAUSE_WORDS and run.state == "running":
                run.pause_ev.clear()
                run.goto("paused")
                return self.core.ph("scenario_pause")
            if t in RESUME_WORDS and run.state == "paused":
                run.goto("running")
                run.pause_ev.set()
                return self.core.ph("scenario_resume")
            if t in SKIP_WORDS:
                run.skip_next = True
                return self.core.ph("scenario_skip")
            if t in CANCEL_WORDS:
                run.cancel.set()
                if run.state == "paused":
                    run.pause_ev.set()
                return ""
        elif t in RESTART_WORDS and run.state in ("failed", "cancelled"):
            self.core._say_async("Продолжаю с места остановки, готовое не повторяю.")
            self._spawn(run)
            return ""
        return None

    # -------------------------------------------------------------- пользовательские команды
    def run_item(self, item, args=""):
        """Запуск сохранённой команды. Параметры (например, {query}, {time}) берутся из хвоста фразы."""
        params = {}
        if item.get("params"):
            rest = args or ""
            tc = nlu.parse_timecode(rest) if any(p["name"] == "time" for p in item["params"]) else None
            if tc:
                params["time"] = {"mode": tc[0], "sec": tc[1]}
            if any(p["name"] == "query" for p in item["params"]):
                q = strip_time_phrase(rest)
                q = re.sub(r"^(?:про|о|об|на тему)\s+", "", q)
                if q and nlu.norm(q) not in ("нужное", "нужное видео"):
                    params["query"] = q
        steps = [dict(s) for s in item["steps"]]
        for s in steps:
            s["params"] = dict(s.get("params", {}))
            if s["action"] in ("app.open", "browser.open_site", "browser.youtube_play", "browser.search", "browser.new_tab") \
                    and "undo" not in s:
                act = self.core.registry.get(s["action"])
                if act and act.undo:
                    s["undo"] = {"action": act.undo, "params": {"target": s["params"].get("target")} if act.undo == "app.close" else {}}
        self.start(item["phrases"][0], steps, params, item.get("reply", ""))
        return ""
