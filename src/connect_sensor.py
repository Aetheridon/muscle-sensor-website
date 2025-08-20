import socket
import time

def preflight_connect(ip: str, port: int, timeout: float = 2.0) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


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
                print(f"[stream] Connected to {ip}:{port}. Printing lines...")

                for raw in f:
                    if stop_event.is_set():
                        status["connected"] = False
                        status["last_error"] = None
                        print("[stream] Stopping by request.")
                        return
                    line = raw.rstrip("\r\n")
                    if not line or line.startswith("time_ms"):
                        continue
                    print(line)

                if not stop_event.is_set():
                    print("[stream] Connection closed by device or EOF.")
                    status["connected"] = False
                    status["last_error"] = "Connection closed by device (EOF)."

        except Exception as e:
            if stop_event.is_set():
                status["connected"] = False
                status["last_error"] = None
                print("[stream] Stopped.")
                return
            status["connected"] = False
            status["last_error"] = f"{type(e).__name__}: {e}"
            print(f"[stream] Error: {status['last_error']}")


        for _ in range(int(reconnect_delay * 10)):
            if stop_event.is_set():
                break
            time.sleep(0.1)

    print("[stream] Stopped.")