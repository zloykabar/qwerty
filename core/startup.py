# -*- coding: utf-8 -*-
"""Замеры времени старта: каждая отметка пишется в logs/assistant.log и в logs/jarvis.log строкой «[старт] ...»."""
import threading
import time

T0 = time.perf_counter()
_marks = []
_lock = threading.Lock()


def mark(name):
    dt = time.perf_counter() - T0
    with _lock:
        _marks.append((name, dt))
    line = f"[старт] {name}: {dt:.2f} с"
    print(line, flush=True)
    try:
        from core.logsetup import get
        get("startup").info(line)
    except Exception:
        pass
    return dt


def marks():
    with _lock:
        return list(_marks)
