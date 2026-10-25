"""Thread-safe pub/sub that fans messages out to WebSocket clients."""
import asyncio
import json


class Bus:
    def __init__(self):
        self.loop = None
        self.queues = set()

    def bind(self, loop):
        self.loop = loop

    def subscribe(self):
        q = asyncio.Queue(maxsize=200)
        self.queues.add(q)
        return q

    def unsubscribe(self, q):
        self.queues.discard(q)

    def publish(self, kind: str, data):
        """Safe to call from any thread."""
        if not self.loop:
            return
        msg = json.dumps({"type": kind, "data": data}, ensure_ascii=False, default=str)

        def push():
            for q in list(self.queues):
                if q.full():
                    try:
                        q.get_nowait()
                    except Exception:
                        pass
                q.put_nowait(msg)
        try:
            self.loop.call_soon_threadsafe(push)
        except RuntimeError:
            pass


bus = Bus()
