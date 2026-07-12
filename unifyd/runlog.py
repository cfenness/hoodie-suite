"""runlog.py — make local/off-box scrapes register in the Active Runs board.

The server's Active Runs only sees pulls launched THROUGH the Flask agent (in-memory JOBS). Scrapes run
directly (a nohup python on a laptop, a cron, a batch sweep) are invisible. This writes a run's live status to
a SHARED warehouse table (`scrape_runs`) that `/api/jobs` reads alongside the in-memory jobs — so ANY run,
wherever it runs, shows up with progress/ETA. Progress writes are throttled (the table is on S3).

    with runlog.track("total-wine-inv", total=len(skus)) as r:
        for i, s in enumerate(skus):
            ...; r.progress(i + 1)
    # -> registers running -> done automatically; failures record status='failed' with the traceback

Or manually: r = runlog.start("abc-fws"); r.progress(n, total); r.finish("done", note="landed N").
"""
import os
import socket
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

_HOST = socket.gethostname().split(".")[0]
FIELDS = ["run_id", "connId", "status", "host", "startedAt", "updatedAt", "finishedAt", "n", "total", "note"]


class Run:
    def __init__(self, conn_id, total=None, every=8.0):
        # deterministic-ish id without Math.random/time-in-workflow constraints (this is a normal process)
        self.id = "%s-%s-%d" % (conn_id, _HOST[:6], int(time.time()))
        self.conn_id = conn_id
        self.total = total
        self.n = 0
        self.every = every
        self.started = int(time.time() * 1000)
        self._last = 0.0
        self._write("running")

    def _write(self, status, note="", finished=False):
        now = int(time.time() * 1000)
        rec = dict(run_id=self.id, connId=self.conn_id, status=status, host=_HOST,
                   startedAt=self.started, updatedAt=now, finishedAt=(now if finished else None),
                   n=self.n, total=self.total, note=str(note)[:200])
        try:
            warehouse.write_accumulate("scrape_runs", [rec], key=lambda r: r["run_id"], fields=FIELDS)
        except Exception:
            pass

    def progress(self, n, total=None):
        self.n = n
        if total is not None:
            self.total = total
        t = time.time()
        if t - self._last >= self.every:            # throttle S3 writes
            self._last = t
            self._write("running")

    def finish(self, status="done", note=""):
        self._write(status, note, finished=True)


def start(conn_id, total=None, every=8.0):
    return Run(conn_id, total=total, every=every)


class track:
    """Context manager: registers running on enter, done on clean exit, failed (with traceback) on exception."""
    def __init__(self, conn_id, total=None, every=8.0):
        self.conn_id, self.total, self.every = conn_id, total, every
        self.run = None

    def __enter__(self):
        self.run = start(self.conn_id, total=self.total, every=self.every)
        return self.run

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.run.finish("done")
        else:
            self.run.finish("failed", note="".join(traceback.format_exception_only(exc_type, exc)).strip())
        return False
