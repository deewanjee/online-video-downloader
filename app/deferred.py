"""A single sleeping dispatcher for existing downloads held by a source cooldown."""
import threading


class DeferredDownloads:
    def __init__(self, remaining, submit):
        self.remaining = remaining
        self.submit = submit
        self.condition = threading.Condition()
        self.pending = {}
        self.stopped = False
        self.thread = None

    def defer(self, job_id, request):
        with self.condition:
            if self.stopped:
                return
            self.pending[job_id] = request
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
                delay = self.remaining()
                if delay > 0:
                    self.condition.wait(min(delay, 60))
                    continue
                pending = list(self.pending.items())
                self.pending.clear()
            for job_id, request in pending:
                with self.condition:
                    if self.stopped:
                        return
                self.submit(job_id, request)

    def discard(self, job_id):
        with self.condition:
            self.pending.pop(job_id, None)
            self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.pending.clear()
            self.condition.notify_all()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
