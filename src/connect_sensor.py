import socket
import time
from collections import deque

class SampleBus:
    def __init__(self, maxlen=1000):
        self.q = deque(maxlen=maxlen)
        self.subscribers = set()

    def publish(self, msg: dict):
        self.q.append(msg)
        for s in list(self.subscribers):
            try:
                s(msg)
            except Exception:
                self.subscribers.discard(s)

    def subscribe(self, callback):
        self.subscribers.add(callback)
        def unsubscribe():
            self.subscribers.discard(callback)
        return unsubscribe

BUS = SampleBus()

def preflight_connect(ip: str, port: int, timeout: float = 2.0) -> tuple[bool, str | None]:
    '''quick connection attempt to socket to ensure host is reachable'''
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

def _emit(event_type: str, payload: dict):
    BUS.publish({"type": event_type, "data": payload})

def stream_to_stdout(ip: str, port: int, stop_event, status: dict, reconnect_delay=1.0):
    status["connected"] = False
    status["last_error"]  = None

    while not stop_event.is_set():
        try:
            with socket.create_connection((ip, port), timeout=5.0) as s:
                status["connected"] = True
                status["last_error"] = None

                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                f = s.makefile("r", encoding="utf-8", newline=None)
                print(f"[stream] Connected to {ip}:{port}")

                header = None
                columns = None

                for raw in f:
                    if stop_event.is_set():
                        status["connected"] = False
                        status["last_error"] = None
                        print("[stream] Stopping by request.")
                        return

                    line = raw.strip()
                    if not line:
                        continue

                    if header is None and line.startswith("time_ms"):
                        header = line
                        columns = [c.strip() for c in line.split(",")]
                        print(f"[stream] Header: {columns}")
                        _emit("header", {"columns": columns})
                        continue

                    if line.startswith("time_ms"):
                        continue

                    if columns is None:
                        continue

                    parts = line.split(",")
                    if len(parts) != len(columns):
                        print("[stream] Malformed line skipped:", line)
                        continue

                    sample = {}
                    for key, val in zip(columns, parts):
                        if key == "time_ms":
                            try:
                                sample[key] = int(val)
                            except ValueError:
                                sample[key] = None
                        else:
                            try:
                                sample[key] = float(val)
                            except ValueError:
                                sample[key] = None

                    print("[sample]", sample)

                    _emit("sample", sample)

                if not stop_event.is_set():
                    print("[stream] Connection closed by device or EOF.")
                    status["connected"] = False
                    status["last_error"] = "Connection closed by device (EOF)."
                    _emit("status", {"connected": False, "reason": "eof"})

        except Exception as e:
            if stop_event.is_set():
                status["connected"] = False
                status["last_error"] = None
                print("[stream] Stopped.")
                return
            
            status["connected"] = False
            status["last_error"] = f"{type(e).__name__}: {e}"
            print(f"[stream] Error: {status['last_error']}")
            _emit("status", {"connected": False, "error": status["last_error"]})

        for _ in range(int(reconnect_delay * 10)):
            if stop_event.is_set():
                break
            time.sleep(0.1)

    print("[stream] Stopped.")
