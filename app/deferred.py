"""A single sleeping dispatcher for existing downloads held by a source cooldown."""
import threading
import time


class DeferredDownloads:
    def __init__(self, remaining, submit, delay_for=None):
        self.remaining = remaining
        self.submit = submit
        self.delay_for = delay_for
        self.due = {}
        self.condition = threading.Condition()
        self.pending = {}
        self.stopped = False
        self.thread = None

    def defer(self, job_id, request, delay=0):
        with self.condition:
            if self.stopped:
                return
            self.pending[job_id] = request
            self.due[job_id] = time.monotonic() + max(0, delay)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='source-cooldown', daemon=True)
                self.thread.start()
            self.condition.notify_all()

    def _run(self):
        while True:
            with self.condition:
                if self.stopped:
                    return
                if not self.pending:
                    self.condition.wait()
                    continue
                delays = {key: max(self.remaining() if self.delay_for is None else self.delay_for(key, req),
                                   self.due.get(key, 0) - time.monotonic()) for key, req in self.pending.items()}
                pending = [(key, req) for key, req in self.pending.items() if delays[key] <= 0]
                if not pending:
                    self.condition.wait(min(max(.05, min(delays.values())), 60))
                    continue
                for key, _ in pending:
                    self.pending.pop(key, None)
                    self.due.pop(key, None)
            for job_id, request in pending:
                with self.condition:
                    if self.stopped:
                        return
                self.submit(job_id, request)

    def discard(self, job_id):
        with self.condition:
            self.pending.pop(job_id, None)
            self.due.pop(job_id, None)
            self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.pending.clear()
            self.due.clear()
            self.condition.notify_all()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
