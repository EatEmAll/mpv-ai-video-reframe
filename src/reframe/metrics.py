"""Bounded-latency JSONL telemetry with guaranteed close/flush on detach."""

import json
import time
from pathlib import Path


class Telemetry:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None
        self.last_flush = 0.0

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("w", encoding="utf-8")
        return self

    def write(self, record):
        self.stream.write(json.dumps(record, allow_nan=False) + "\n")
        now = time.monotonic()
        if now - self.last_flush >= 0.5:
            self.stream.flush()
            self.last_flush = now

    def __exit__(self, *exception):
        self.stream.close()
