"""Server-owned periodic sampling of live memory observations."""

import logging
import threading


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
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — a failed sample leaves a gap; later samples still run
                logging.getLogger(__name__).exception("memory sample failed")

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="basal-ui-memory", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
