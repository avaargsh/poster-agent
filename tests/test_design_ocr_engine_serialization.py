"""A single cached local OCR engine must not receive overlapping predictions."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from time import sleep

import app.designs.ocr_runs as ocr_runs


def test_process_local_ocr_engine_invocations_are_serialized(monkeypatch):
    state_lock = Lock()
    active = 0
    maximum_active = 0
    calls = []

    def fake_recognize(path):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            sleep(0.05)
            calls.append(path.name)
            return [{"text": path.name, "score": 0.9, "bbox": [0, 0, 10, 10]}]
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(ocr_runs, "recognize", fake_recognize)

    paths = [Path("one.png"), Path("two.png"), Path("three.png")]
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(ocr_runs._recognize_serialized, paths))

    assert maximum_active == 1
    assert sorted(calls) == ["one.png", "three.png", "two.png"]
    assert [item[0]["text"] for item in results] == ["one.png", "two.png", "three.png"]
