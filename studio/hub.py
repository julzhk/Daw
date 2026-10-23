"""In-process broadcast hub for collaboration.

Each open browser tab holds one long-lived SSE stream (views.events) with a
queue here. broadcast() puts Datastar events on every queue except the
sender's. This only works within a single server process; several processes
or servers would need a shared pub/sub (e.g. Redis) instead.
"""

import queue
import threading

_clients: dict[str, queue.Queue] = {}
_lock = threading.Lock()


def connect(client_id):
    q = queue.Queue()
    with _lock:
        _clients[client_id] = q
    return q


def disconnect(client_id, q):
    with _lock:
        if _clients.get(client_id) is q:
            del _clients[client_id]


def broadcast(events, exclude=None):
    with _lock:
        targets = [q for cid, q in _clients.items() if cid != exclude]
    for q in targets:
        q.put(events)


def count():
    with _lock:
        return len(_clients)
