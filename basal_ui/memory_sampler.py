"""Server-owned periodic sampling of live memory observations."""

import logging
import threading
import time

STOP_JOIN_S = 5


class MemorySampler:
    def __init__(self, stats, snapshot, interval=60):
        self.stats = stats
        self.snapshot = snapshot
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def tick(self):
        self.stats.record_memory_sample(self.snapshot())

    def _run(self):
        started = time.monotonic()
        started_at = time.time()
        deadline = started + self.interval
        while not self._stop.wait(max(0, deadline - time.monotonic())):
            try:
                sample = self.snapshot()
                if self._stop.is_set():
                    return
                sample["time"] = started_at + (deadline - started)
                self.stats.record_memory_sample(sample)
            except Exception:  # noqa: BLE001 — a failed sample leaves a gap; later samples still run
                logging.getLogger(__name__).exception("memory sample failed")
            deadline += self.interval
            now = time.monotonic()
            if deadline <= now:
                deadline += (int((now - deadline) // self.interval) + 1) * self.interval

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="basal-ui-memory", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_JOIN_S)
            if not self._thread.is_alive():
                self._thread = None
