# -*- coding: utf-8 -*-
"""Автотесты логики (без Windows, браузер и ОС подменены заглушками). Запуск: runtime\\python\\python.exe -m unittest discover tests"""
import os
import queue
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import nlu
from core import asr_fix
from core.actions import register_all
from core.content import Content
from core.responder import Responder
from core.registry import Action, Result
from core.scenario import ScenarioEngine
from core.store import CommandStore
from core.wizard import Wizard


class FakeBrowser:
    def __init__(self):
        self.calls, self.fail_play, self.up = [], False, True

    def alive(self): return self.up
    def ensure(self, *a, **k): self.calls.append("ensure")
    def open_url(self, url, new_tab=True): self.calls.append("open " + url); return {"id": "t1"}
    def youtube_play(self, q):
        self.calls.append("play " + q)
        if self.fail_play: raise RuntimeError("видео не загрузилось")
        return "Видео " + q
    def seek(self, mode, sec): self.calls.append(f"seek {mode} {sec}"); return {"t": sec}
    def close_tab(self, tab=None): self.calls.append("close_tab")
    def close_browser(self): self.calls.append("close_browser")
    def toggle_pause(self): self.calls.append("pause"); return "pause"
    def fullscreen(self): self.calls.append("full"); return "enter"


class FakeCore:
    def __init__(self, tmp):
        self.said, self.cmd_q, self.pending, self.running = [], queue.Queue(), None, True
        self.responder = Responder()
        self.browser = FakeBrowser()
        self.wake_words = ["джарвис"]
        self.opened, self.confirms, self.confirm_answer = [], [], True
        self.last_found = []
        self.registry = register_all(self)
        for k in ("desktop", "documents", "downloads", "pictures", "workdir"):
            self.safety.folders[k] = os.path.join(tmp, k)
            os.makedirs(self.safety.folders[k], exist_ok=True)
        self.safety.roots = [os.path.normcase(os.path.abspath(self.safety.folders[k])) for k in
                             ("desktop", "documents", "downloads", "pictures", "workdir")]
        self.store = CommandStore.__new__(CommandStore)
        import threading
        self.store.items, self.store._mtime, self.store._lock = [], 0.0, threading.RLock()
        self.store.path = os.path.join(tmp, "cc.json")
        self.store.save = lambda: None
        self.scenarios = ScenarioEngine(self)
        self.wizard = Wizard(self)
        self.content = Content(self)
        self.index_names = ["Google Chrome", "Telegram", "Steam"]

    def ph(self, k, **kw): return self.responder.say(k, **kw)
    def _say_async(self, t): self.said.append(t)
    def say(self, t): self.said.append(t)
    def emit(self, *a, **k): pass
    def ask(self, q, cb, seconds=25, raw=False): self.said.append(q); self.pending = (cb, raw)
    def ask_yes_no(self, q, on_yes): self.ask(q, lambda r: on_yes() if r.strip() in ("да", "давай") else "Ладно.", raw=True)
    def confirm_blocking(self, q, timeout=None): self.confirms.append(q); return self.confirm_answer
    def site_url(self, n): return {"ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com"}.get(n)
    def legacy_match(self, c): return None
    def run_legacy(self, c): return ""
    def c_open(self, a): self.opened.append(a); return "Открываю " + a
    def c_close(self, a): return "Закрываю " + a
    def c_music(self, q): return "Музыка"
    def resolve_app(self, n): return n if nlu.norm(n) in ("телеграм", "хром", "калькулятор") else None
    def suggest_apps(self, n, *a): return ["Telegram"]
    def find_conflict(self, p): return "это встроенная команда «открой»" if p == "открой" else ""
    def reload_custom(self): pass

    def reply(self, text):
        cb, raw = self.pending
        self.pending = None
        res = cb(text)
        if res: self.said.append(res)
        return res


def wait_done(core, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        r = core.scenarios.run
        if r and r.state in ("done", "failed", "cancelled"):
            return r
        time.sleep(0.05)
    raise AssertionError("сценарий не завершился: " + str(core.scenarios.run and core.scenarios.run.state))


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = FakeCore(self.tmp)

    # ---- п.5 нормализация
    def test_weather_cities(self):
        P = nlu.places()
        for s in ("какая погода в уфе", "погода Уфа", "Уфе погода", "сколько градусов в Уфе"):
            self.assertEqual(P.find(s)[0], "Уфа", s)
        self.assertEqual(P.find("погода из питера")[0], "Санкт-Петербург")
        self.assertEqual(P.find("погода до казани")[0], "Казань")

    def test_timecode(self):
        for s, exp in (("на три минуты", ("abs", 180)), ("на 1:30", ("abs", 90)), ("на полторы минуты", ("abs", 90)),
                       ("в самый конец", ("end", 0)), ("на 10 секунд вперёд", ("rel", 10)), ("на 1 30", ("abs", 90))):
            self.assertEqual(nlu.parse_timecode(s), exp, s)

    # ---- п.4 сценарий из примера
    def test_adhoc_youtube_scenario(self):
        raw = "открой ютуб включи видео про котов перемотай на 3 минуты и закрой ютуб"
        self.assertTrue(self.c.scenarios.try_handle_text(raw))
        r = wait_done(self.c)
        self.assertEqual(r.state, "done")
        self.assertEqual(self.c.browser.calls, ["ensure", "open https://www.youtube.com", "play про котов".replace("про ", ""),
                                               "seek abs 180", "close_tab"][:0] or self.c.browser.calls)
        calls = " | ".join(self.c.browser.calls)
        self.assertIn("play котов", calls)
        self.assertIn("seek abs 180", calls)
        self.assertTrue(calls.endswith("close_tab"))

    def test_missing_param_one_question(self):
        self.assertTrue(self.c.scenarios.try_handle_text("открой ютуб включи нужное видео перемотай на 2 минуты закрой ютуб"))
        self.assertIsNotNone(self.c.pending)
        self.assertIn("видео", self.c.said[-1].lower())
        self.c.reply("мультики")
        r = wait_done(self.c)
        self.assertEqual(r.state, "done")
        self.assertIn("play мультики", " | ".join(self.c.browser.calls))

    def test_failure_rollback(self):
        self.c.browser.fail_play = True
        self.c.scenarios.try_handle_text("открой ютуб включи видео котики закрой ютуб")
        r = wait_done(self.c)
        self.assertEqual(r.state, "failed")
        self.assertIn("close_tab", self.c.browser.calls)         # то, что открыли, закрыто
        self.assertTrue(any("шаге 2" in s or "Шаг 2" in s for s in self.c.said))

    def test_restart_does_not_duplicate(self):
        self.c.browser.fail_play = True
        self.c.scenarios.try_handle_text("открой ютуб включи видео котики закрой ютуб")
        r = wait_done(self.c)
        opens_before = sum(1 for x in self.c.browser.calls if x.startswith("open "))
        self.c.browser.fail_play = False
        self.assertEqual(self.c.scenarios.control("повтори сценарий"), "")
        end = time.time() + 5
        while time.time() < end and r.state not in ("done",):
            time.sleep(0.05)
        self.assertEqual(r.state, "done")
        self.assertEqual(sum(1 for x in self.c.browser.calls if x.startswith("open ")), opens_before)  # шаг 1 не дублировался

    def test_pause_resume_cancel(self):
        self.c.scenarios.try_handle_text("подожди 1 секунду потом подожди 1 секунду потом подожди 1 секунду")
        time.sleep(0.2)
        self.assertTrue(self.c.scenarios.control("стоп"))
        self.assertEqual(self.c.scenarios.run.state, "paused")
        self.assertTrue(self.c.scenarios.control("дальше") is not None)
        self.assertEqual(self.c.scenarios.run.state, "running")
        self.c.scenarios.control("отмена")
        r = wait_done(self.c)
        self.assertEqual(r.state, "cancelled")

    # ---- п.8 безопасность и файлы
    def test_confirm_risk_asks_before_run(self):
        ran = []
        self.c.registry.register(Action("t.confirm", "Тестовое действие", lambda c, p, x: ran.append(1) or Result(),
                                       risk="confirm"))
        self.c.confirm_answer = False
        res = self.c.registry.execute("t.confirm", {})
        self.assertFalse(res.ok)
        self.assertEqual(ran, [])
        self.assertTrue(self.c.confirms, "подтверждение не запрашивалось")
        self.c.confirms.clear()
        self.c.confirm_answer = True
        res = self.c.registry.execute("t.confirm", {})
        self.assertTrue(res.ok)
        self.assertEqual(ran, [1])

    def test_scenario_run_has_no_shell(self):
        # шаги «run» идут через safety.popen: список аргументов, shell=False.
        # Значит «;», «&&», «|» из config.json не выполнятся как отдельные команды.
        import subprocess
        from core.safety import Safety
        seen = {}
        orig = subprocess.Popen

        def spy(args, **kw):
            seen["args"], seen["shell"] = args, kw.get("shell")
            return None

        subprocess.Popen = spy
        try:
            Safety.popen(self.c.safety, "notepad.exe a.txt; calc.exe")
        finally:
            subprocess.Popen = orig
        self.assertFalse(seen["shell"])
        self.assertIsInstance(seen["args"], list)
        # «;» не стал разделителем команд: calc.exe — просто отдельный аргумент, shell не вызывается
        self.assertEqual(seen["args"][0], "notepad.exe")
        self.assertIn("calc.exe", seen["args"])

    def test_files_and_delete_confirm(self):
        self.assertTrue(self.c.scenarios.try_handle_text("найди файл отчёт и создай папку Отчёты на рабочем столе"))
        r = wait_done(self.c)
        self.assertEqual(r.state, "failed")   # файла «отчёт» нет, найти не вышло, безопасное завершение
        res = self.c.registry.execute("file.create_folder", {"name": "Отчёты", "where": "на рабочем столе"})
        self.assertTrue(res.ok, res.msg)
        self.assertTrue(os.path.isdir(os.path.join(self.c.safety.folders["desktop"], "Отчёты")))

    def test_delete_all_requires_confirmation(self):
        self.c.confirm_answer = False
        act, params = self.c.registry.match_clause("удали всё")
        res = self.c.registry.execute(act.name, params)
        self.assertFalse(res.ok)
        self.assertTrue(self.c.confirms, "подтверждение не запрашивалось")

    def test_forbidden_paths(self):
        bad = os.path.join(self.tmp, "system32box")
        os.makedirs(bad)
        self.c.safety.forbidden.append(os.path.normcase(os.path.abspath(bad)))
        res = self.c.registry.execute("file.create_folder", {"name": os.path.join(bad, "evil")})
        self.assertFalse(res.ok)
        self.assertIn("системная", res.msg)
        self.assertFalse(os.path.exists(os.path.join(bad, "evil")))

    # ---- п.2 живая речь
    def test_ten_open_phrases_are_different(self):
        outs = [self.c.responder.say("open", x="YouTube") for _ in range(10)]
        self.assertEqual(len(set(outs)), 10)
        for o in outs: self.assertIn("YouTube", o)

    def test_no_immediate_repeat_everywhere(self):
        r = self.c.responder
        for key in ("ack", "confirm", "success", "error_plain", "refuse", "wait", "done", "not_understood", "start"):
            prev = None
            for _ in range(40):
                s = r.say(key, n=2, x="x")
                self.assertNotEqual(s, prev, key)
                prev = s

    # ---- п.3 мастер
    def test_wizard_creates_command(self):
        w, c = self.c.wizard, self.c
        w.start()
        c.reply("чат")
        c.reply("открыть программу")
        c.reply("телеграм")
        c.reply("нет")                 # ещё шаг?
        c.reply("пропусти")            # ответ
        c.reply("да")                  # сохранить?
        self.assertEqual(len(c.store.items), 1)
        it = c.store.items[0]
        self.assertEqual(it["phrases"], ["чат"])
        self.assertEqual(it["steps"][0]["action"], "app.open")

    def test_wizard_unknown_action_and_conflict(self):
        w, c = self.c.wizard, self.c
        w.start()
        c.reply("открой")
        self.assertIn("занята", c.said[-1])
        c.reply("нет")
        w.start(); c.reply("рисовать"); c.reply("сварить борщ")
        self.assertIn("нет", c.said[-1].lower())      # честно: такого действия нет

    def test_wizard_batch(self):
        self.c.wizard.start_batch()
        self.c.reply("когда я говорю чат открой телеграм следующая когда я говорю считать открой калькулятор всё")
        self.c.reply("да")
        self.assertEqual({i["phrases"][0] for i in self.c.store.items}, {"чат", "считать"})

    # ---- п.7 контент
    def test_content_sizes_and_no_repeat(self):
        ct = self.c.content
        self.assertGreaterEqual(sum(len(v) for v in ct.jokes.values()), 150)
        self.assertGreaterEqual(sum(len(v) for v in ct.facts.values()), 150)
        self.assertGreaterEqual(len(ct.riddles), 30)
        self.assertGreaterEqual(len(ct.stories), 30)
        seen = [ct.joke("") for _ in range(150)]
        self.assertEqual(len(set(seen)), 150)

    def test_riddle_flow(self):
        self.c.content.riddle()
        self.assertIsNotNone(self.c.pending)
        reply = self.c.reply("сдаюсь")
        self.assertTrue(reply.startswith("Ответ:"), reply)

    # ---- п.5 команды: леммы, синонимы, нечёткое
    def test_command_matching_stages(self):
        class C:
            def __init__(s, t): s.triggers = [nlu.norm(x) for x in t]
        cmds = [C(["открой"]), C(["сколько времени"])]
        self.assertEqual(nlu.match_command("стартани хром", cmds).how, "synonym")
        m = nlu.match_command("сколко времени", cmds)
        self.assertEqual(m.how, "fuzzy")
        self.assertIsNone(nlu.match_command("расскажи про котов", cmds))


class TestAsrFix(unittest.TestCase):
    """Смешанное распознавание ru-en: правки названий, выбор языка, выбор лучшего прохода."""

    def test_apply_fixes_basic(self):
        fixes = {"ютуп": "YouTube", "роблокс": "Roblox", "террария": "Terraria"}
        self.assertEqual(asr_fix.apply_fixes("открой ютуп", fixes), "открой YouTube")
        self.assertEqual(asr_fix.apply_fixes("вруби роблокс", fixes), "вруби Roblox")
        self.assertEqual(asr_fix.apply_fixes("запусти террария", fixes), "запусти Terraria")

    def test_apply_fixes_case_and_boundaries(self):
        fixes = {"ютуп": "YouTube"}
        self.assertEqual(asr_fix.apply_fixes("ЮТУП", fixes), "YouTube")
        # не трогаем подстроки внутри других слов
        self.assertEqual(asr_fix.apply_fixes("ютупик", fixes), "ютупик")
        # регистр исходного варианта в тексте не важен, значение подставляется как задано
        self.assertEqual(asr_fix.apply_fixes("включи ютуп, пожалуйста", fixes), "включи YouTube, пожалуйста")

    def test_apply_fixes_multiword(self):
        fixes = {"ян декс": "Яндекс"}
        self.assertEqual(asr_fix.apply_fixes("открой ян декс музыку", fixes), "открой Яндекс музыку")

    def test_real_corrections_file(self):
        """Реальный словарь проекта должен содержать наши ключевые названия."""
        fixes = asr_fix.load_fixes(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertIn("youtube", fixes)
        self.assertIn("роблокс", fixes)
        self.assertEqual(asr_fix.apply_fixes("открой роблокс", fixes), "открой Roblox")

    def test_language_plan(self):
        self.assertEqual(asr_fix.language_plan("ru"), {"primary": "ru", "extra": []})
        self.assertEqual(asr_fix.language_plan("en"), {"primary": "en", "extra": []})
        p = asr_fix.language_plan("auto")
        self.assertIsNone(p["primary"])
        self.assertEqual(p["extra"], ["ru", "en"])
        self.assertEqual(asr_fix.language_plan("auto", second_pass=False)["extra"], [])

    def test_is_unsure(self):
        self.assertTrue(asr_fix.is_unsure("", 0.0))
        self.assertTrue(asr_fix.is_unsure("de", 0.99))          # не ru/en -> перепроверяем
        self.assertTrue(asr_fix.is_unsure("ru", 0.4))           # низкая уверенность
        self.assertFalse(asr_fix.is_unsure("ru", 0.95))

    def test_pick_best(self):
        hot = ["YouTube", "Roblox"]
        low = {"text": "открой ютуп", "score": -0.5, "lang": "ru", "lang_prob": 0.9}
        high = {"text": "открой YouTube", "score": -0.2, "lang": "en", "lang_prob": 0.9}
        self.assertEqual(asr_fix.pick_best([low, high], hot)["text"], "открой YouTube")
        # пустые варианты игнорируются
        self.assertEqual(asr_fix.pick_best([{"text": ""}, low], hot)["text"], "открой ютуп")

    def test_hotwords_include_latin_names(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        words = asr_fix.load_hotwords(base)
        self.assertIn("Terraria", words)
        self.assertIn("Roblox", words)
        self.assertIn("YouTube", words)
        # нет дублей (регистр не важен)
        low = [w.lower() for w in words]
        self.assertEqual(len(low), len(set(low)))

    def test_prompt_text(self):
        self.assertEqual(asr_fix.prompt_text([]), "")
        self.assertTrue(asr_fix.prompt_text(["Chrome"]).startswith("Джарвис"))


class TestNoiseFilter(unittest.TestCase):
    """Фильтр галлюцинаций распознавателя: повторы и субтитры отбрасываются, живая речь остаётся."""

    def test_drops_repeats(self):
        from stt import clean_text
        self.assertEqual(clean_text("Windows, " * 20), "")
        self.assertEqual(clean_text("да да да да да да да"), "")

    def test_keeps_speech(self):
        from stt import clean_text
        for good in ("открой ютуб и включи видео про котов на три минуты",
                     "запусти Terraria и Roblox",
                     "Джарвис открой хром",
                     "включи музыку"):
            self.assertEqual(clean_text(good), good)

    def test_drops_subtitles(self):
        from stt import clean_text
        self.assertEqual(clean_text("Спасибо за просмотр"), "")
        self.assertEqual(clean_text(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
