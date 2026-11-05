# -*- coding: utf-8 -*-
"""Офлайн-контент: шутки, факты, загадки, истории, мини-игры. Всё из JSON в content/, без выдуманных «новостей».
Без повторов внутри сессии (перемешанные «мешки»), с учётом категории, времени суток и настроения."""
import random
import re
import webbrowser
import urllib.parse

from core import nlu
from core.paths import load_json
from core.responder import day_period

KIND_TITLES = {"joke": "шутка", "fact": "факт", "riddle": "загадка", "story": "история"}


class Bag:
    """Перемешанный набор: каждый элемент выдаётся один раз, пока не кончатся все."""
    def __init__(self, items):
        self.items, self.pool = list(items), []

    def next(self):
        if not self.items:
            return None
        if not self.pool:
            self.pool = random.sample(self.items, len(self.items))
        return self.pool.pop()


class Content:
    def __init__(self, core):
        self.core = core
        self.last_kind = None
        self.last_item = None
        self.last_cat = None
        self.jokes = (load_json("jokes.json", {}, "content") or {}).get("categories", {})
        self.facts = (load_json("facts.json", {}, "content") or {}).get("categories", {})
        self.riddles = (load_json("riddles.json", {}, "content") or {}).get("items", [])
        self.stories = (load_json("stories.json", {}, "content") or {}).get("items", [])
        self.words = (load_json("words.json", {}, "content") or {}).get("words", [])
        self.bags = {}

    # ---------------------------------------------------------------- выбор
    def _bag(self, kind, cat):
        key = (kind, cat)
        if key not in self.bags:
            src = {"joke": self.jokes, "fact": self.facts}[kind]
            pool = src.get(cat) if cat else [x for v in src.values() for x in v]
            self.bags[key] = Bag(pool or [])
        return self.bags[key]

    def match_cat(self, text, table):
        t = nlu.norm(text)
        for cat in table:
            stem = nlu.norm(cat)[:max(4, len(cat) - 2)]
            if stem in t:
                return cat
        keys = {"космос": ("космос", "планет", "звезд", "луна", "солнц"), "наука": ("наук", "физик", "хими", "биолог"),
                "техника": ("техник", "компьютер", "програм", "айти", "it", "интернет", "сеть"),
                "история": ("истори", "древн", "войн"), "язык": ("язык", "слов", "букв", "татарск"),
                "природа": ("природ", "животн", "зверь", "птиц", "растен"), "учёба": ("учеб", "студент", "школ", "экзамен"),
                "быт": ("быт", "дом", "жизн"), "животные": ("животн", "кот", "собак"), "каламбуры": ("каламбур", "игр слов")}
        for cat, ks in keys.items():
            if cat in table and any(k in t for k in ks):
                return cat
        return None

    def joke(self, text=""):
        cat = self.match_cat(text, self.jokes)
        if not cat and self.core.responder.last_failed:
            cat = "техника"                              # после сбоя: самоирония про железо
        item = self._bag("joke", cat).next()
        self.last_kind, self.last_item, self.last_cat = "joke", item, cat
        return item or "Шутки закончились, что само по себе уже смешно."

    def fact(self, text=""):
        cat = self.match_cat(text, self.facts)
        item = self._bag("fact", cat).next()
        self.last_kind, self.last_item, self.last_cat = "fact", item, cat
        return "Знаешь ли ты? " + item if item else "Фактов у меня не осталось."

    def story(self, text=""):
        t = nlu.norm(text)
        cat = "страшилка" if any(k in t for k in ("страш", "ужас", "жутк")) else \
              "мотивация" if any(k in t for k in ("мотив", "вдохнов", "поддерж")) else \
              "история" if any(k in t for k in ("сказк", "истори", "рассказ")) else \
              {"morning": "мотивация", "night": "страшилка"}.get(day_period(), "история")
        pool = [s for s in self.stories if s["cat"] == cat]
        key = ("story", cat)
        if key not in self.bags:
            self.bags[key] = Bag(pool)
        item = self.bags[key].next()
        self.last_kind, self.last_item = "story", item
        if not item:
            return ""
        self.core.emit("msg", who="sys", text="Полный текст:\n" + item["text"])    # целиком в чат
        return item["short"] + " Хочешь целиком вслух, скажи «расскажи подробнее»."

    def more(self):
        """«Расскажи подробнее»: история целиком, факт с поиском в сети, шутка и загадка без продолжения."""
        k, it = self.last_kind, self.last_item
        if k == "story" and it:
            self.core.cmd_q.put(("read", it["text"]))
            return ""
        if k == "fact" and it:
            q = " ".join(it.split()[:8])
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(q))
            return "Подробностей в моей базе нет, поэтому открыл поиск по этой теме."
        return "Подробнее тут нечего рассказывать, но могу ещё одну."

    def again(self):
        k = self.last_kind
        if k == "joke":
            return self.joke(self.last_cat or "")
        if k == "fact":
            return self.fact(self.last_cat or "")
        if k == "story":
            return self.story("")
        if k == "riddle":
            return self.riddle()
        return "Что ещё? Могу пошутить, рассказать факт, загадать загадку или поиграть."

    # ---------------------------------------------------------------- загадки
    def riddle(self):
        key = ("riddle", None)
        if key not in self.bags:
            self.bags[key] = Bag(self.riddles)
        item = self.bags[key].next()
        self.last_kind = "riddle"
        if not item:
            return "Загадки закончились."
        state = {"tries": 0}

        def check(raw):
            t = nlu.norm(raw)
            answers = [nlu.norm(a) for a in item["a"]]
            if t in ("сдаюсь", "не знаю", "ответ", "скажи ответ", "подсказка", "хватит") or t.startswith("сдаюсь"):
                if t.startswith("подсказка"):
                    a = item["a"][0]
                    self.core.ask(f"Подсказка: это слово из {len(a)} букв, начинается на «{a[0]}». Попробуй ещё.", check, 40)
                    return ""
                return f"Ответ: {item['a'][0]}. Хочешь ещё загадку, скажи «ещё»."
            if any(a and (a == t or a in t.split() or nlu.ratio(a, t) >= 85) for a in answers):
                return "Верно! Ты молодец. Скажи «ещё», если хочешь следующую."
            state["tries"] += 1
            if state["tries"] >= 3:
                return f"Не угадал. Ответ: {item['a'][0]}. Скажи «ещё», и будет новая."
            self.core.ask("Не то. Попробуй ещё или скажи «подсказка» или «сдаюсь».", check, 40)
            return ""
        self.core.ask(item["q"], check, 40)
        return ""

    # ---------------------------------------------------------------- игры
    def menu(self):
        self.core.ask("Во что сыграем: угадай число, камень ножницы бумага, орёл или решка, слова?", self._menu_pick, 30)
        return ""

    def _menu_pick(self, raw):
        t = nlu.norm(raw)
        if "числ" in t:
            return self.guess_number()
        if "камен" in t or "ножниц" in t:
            return self.rps()
        if "орел" in t or "решк" in t or "монет" in t:
            return self.coin()
        if "слов" in t:
            return self.word_game()
        return "Не понял, во что играем. Скажи «поиграем» ещё раз."

    def coin(self):
        return random.choice(["Орёл.", "Решка."]) + " Монета не врёт."

    def guess_number(self):
        n = random.randint(1, 100)
        st = {"tries": 0}

        def step(raw):
            t = nlu.norm(raw)
            if t in ("хватит", "сдаюсь", "стоп"):
                return f"Я загадал {n}. Сыграем ещё, скажи «поиграем»."
            m = re.search(r"\d+", raw)
            val = int(m.group()) if m else None
            if val is None:
                words = [nlu.NUM[w] for w in t.split() if w in nlu.NUM]
                val = sum(words) if words else None
            if val is None:
                self.core.ask("Назови число от одного до ста.", step, 40)
                return ""
            st["tries"] += 1
            if val == n:
                return f"Угадал за {st['tries']} {'попытку' if st['tries'] == 1 else 'попытки' if st['tries'] < 5 else 'попыток'}! Отлично."
            if st["tries"] >= 7:
                return f"Попытки кончились. Я загадал {n}."
            self.core.ask("Больше." if n > val else "Меньше.", step, 40)
            return ""
        self.core.ask("Я загадал число от одного до ста. У тебя семь попыток. Называй.", step, 40)
        return ""

    def rps(self):
        opts = {"камень": "ножницы", "ножницы": "бумага", "бумага": "камень"}

        def step(raw):
            t = nlu.norm(raw)
            mine = next((k for k in opts if k[:5] in t), None)
            if t in ("хватит", "стоп", "всё", "все"):
                return "Хорошо, доиграем в другой раз."
            if not mine:
                self.core.ask("Скажи: камень, ножницы или бумага.", step, 30)
                return ""
            me = random.choice(list(opts))
            if me == mine:
                res = "Ничья."
            elif opts[mine] == me:
                res = "Ты выиграл."
            else:
                res = "Я выиграл."
            self.core.ask(f"Я показал: {me}. {res} Ещё раунд? Называй или скажи «хватит».", step, 30)
            return ""
        self.core.ask("Камень, ножницы, бумага. Называй.", step, 30)
        return ""

    def word_game(self):
        if not self.words:
            return "Словарь для игры не нашёлся."
        used = set()

        def last_letter(w):
            w = w.strip().lower().replace("ё", "е")
            for ch in reversed(w):
                if ch not in "ьъы":
                    return ch
            return w[-1]

        def my_word(letter):
            cand = [w for w in self.words if w.lower().replace("ё", "е").startswith(letter) and w not in used]
            return random.choice(cand) if cand else None

        def step(raw):
            t = nlu.norm(raw)
            if t in ("хватит", "стоп", "сдаюсь", "всё", "все"):
                return "Хорошая игра. В следующий раз сыграем дольше."
            w = t.split()[-1] if t else ""
            if not w:
                self.core.ask("Не расслышал слово. Повтори.", step, 40)
                return ""
            if state["need"] and not w.startswith(state["need"]):
                self.core.ask(f"Слово должно начинаться на «{state['need']}». Давай ещё раз.", step, 40)
                return ""
            if w in used:
                self.core.ask("Это слово уже было. Другое.", step, 40)
                return ""
            m = nlu.get_morph()
            if m is not None and not m.word_is_known(w):
                self.core.ask("Такого слова я не знаю. Давай другое.", step, 40)
                return ""
            used.add(w)
            mine = my_word(last_letter(w))
            if not mine:
                return "Мои слова на эту букву закончились. Ты выиграл!"
            used.add(mine)
            state["need"] = last_letter(mine)
            self.core.ask(f"{mine.capitalize()}. Тебе на «{state['need']}».", step, 40)
            return ""
        state = {"need": ""}
        self.core.ask("Играем в слова. Называй существительное, а я отвечу словом на его последнюю букву. "
                      "Скажи первое слово.", step, 40)
        return ""
