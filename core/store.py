# -*- coding: utf-8 -*-
"""Хранилище пользовательских команд: config/custom_commands.json. Горячая перезагрузка по времени изменения файла,
экспорт и импорт, миграция со старого формата (фраза -> одна цель)."""
import datetime
import os
import re
import threading
import uuid

from core.logsetup import get
from core.paths import BASE_DIR, load_json, save_json, user_path

log = get("store")
REL = "custom_commands.json"


def _legacy_step(target):
    t = (target or "").strip()
    if re.match(r"^(https?://|www\.)", t, re.I) or re.match(r"^[\w-]+(\.[\w-]+)*\.(com|ru|org|net|io|tv|me|by|ua|su|рф)(/\S*)?$", t, re.I):
        return {"action": "web.open", "params": {"url": t}}
    return {"action": "app.open", "params": {"target": t}}


class CommandStore:
    def __init__(self):
        self.items = []
        self._mtime = 0.0
        self._lock = threading.RLock()
        self.path = user_path("config", REL)
        self._migrate()
        self.load()

    def _migrate(self):
        old = os.path.join(BASE_DIR, "custom_commands.json")
        if os.path.exists(self.path) or not os.path.exists(old):
            return
        try:
            import json
            with open(old, encoding="utf-8-sig") as f:
                data = json.load(f)
            items = []
            for d in data:
                if isinstance(d, dict) and d.get("phrase") and d.get("target"):
                    items.append({"id": uuid.uuid4().hex[:8], "phrases": [d["phrase"]],
                                  "steps": [_legacy_step(d["target"])], "reply": d.get("reply", ""),
                                  "created": datetime.datetime.now().isoformat(timespec="seconds")})
            save_json(REL, items)
            log.info("перенёс %d команд из старого формата", len(items))
        except Exception:
            log.exception("миграция команд")

    def load(self):
        with self._lock:
            data = load_json(REL, []) or []
            self.items = [d for d in data if isinstance(d, dict) and d.get("phrases") and d.get("steps")]
            try:
                self._mtime = os.path.getmtime(self.path)
            except OSError:
                self._mtime = 0.0

    def changed_on_disk(self):
        try:
            return os.path.getmtime(self.path) != self._mtime
        except OSError:
            return False

    def save(self):
        with self._lock:
            save_json(REL, self.items)
            try:
                self._mtime = os.path.getmtime(self.path)
            except OSError:
                pass

    # ---- операции ----
    def find(self, phrase):
        p = phrase.strip().lower()
        for it in self.items:
            if p in [x.lower() for x in it["phrases"]]:
                return it
        return None

    def add(self, phrases, steps, reply="", params=None):
        with self._lock:
            for ph in phrases:
                old = self.find(ph)
                if old:
                    old["phrases"] = [x for x in old["phrases"] if x.lower() != ph.lower()]
            self.items = [i for i in self.items if i["phrases"]]
            item = {"id": uuid.uuid4().hex[:8], "phrases": list(phrases), "steps": steps, "reply": reply or "",
                    "created": datetime.datetime.now().isoformat(timespec="seconds")}
            if params:
                item["params"] = params
            self.items.append(item)
            self.save()
            return item

    def update(self, item_id, **fields):
        with self._lock:
            for it in self.items:
                if it["id"] == item_id:
                    it.update(fields)
                    self.save()
                    return it
        return None

    def delete(self, item_id):
        with self._lock:
            n = len(self.items)
            self.items = [i for i in self.items if i["id"] != item_id]
            if len(self.items) != n:
                self.save()
                return True
        return False

    def export_to(self, path):
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)
        return len(self.items)

    def import_from(self, path, registry):
        """Импорт с проверкой: команды с неизвестными действиями отбрасываются (битых не создаём)."""
        import json
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        added, skipped = 0, 0
        for d in data if isinstance(data, list) else []:
            if not (isinstance(d, dict) and d.get("phrases") and isinstance(d.get("steps"), list)):
                skipped += 1
                continue
            if any(s.get("action") not in registry.actions for s in d["steps"]):
                skipped += 1
                continue
            self.add(d["phrases"], d["steps"], d.get("reply", ""), d.get("params"))
            added += 1
        return added, skipped
