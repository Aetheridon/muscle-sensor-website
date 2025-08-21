# app.py
import ipaddress
import threading
import json
import time
from pathlib import Path
from flask import Flask, render_template, request, Response, stream_with_context

from connect_sensor import preflight_connect, stream_to_stdout, BUS

app = Flask(__name__, static_folder="static", template_folder="templates")

def is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False

def is_valid_port(port: str) -> bool:
    try:
        p = int(port)
        return 1 <= p <= 65535
    except (ValueError, TypeError):
        return False

_stream_thread: threading.Thread | None = None
_stop_event = threading.Event()
_current_target: tuple[str, int] | None = None
_status = {"connected": False, "last_error": None}

def is_streaming_connected() -> bool:
    return bool(_status.get("connected"))

def start_stream(ip: str, port: int):
    global _stream_thread, _current_target
    if _stream_thread and _stream_thread.is_alive() and _current_target != (ip, port):
        stop_stream()

    if not (_stream_thread and _stream_thread.is_alive()):
        _stop_event.clear()
        _current_target = (ip, port)
        _status["connected"] = False
        _status["last_error"] = None
        _stream_thread = threading.Thread(
            target=stream_to_stdout,
            args=(ip, port, _stop_event, _status),
            daemon=True,
        )
        _stream_thread.start()

def stop_stream(wait_seconds: float = 1.5):
    global _stream_thread
    _stop_event.set()
    if _stream_thread and _stream_thread.is_alive():
        _stream_thread.join(timeout=wait_seconds)
    _stream_thread = None
    _status["connected"] = False

MAPPING_DIR = Path(app.static_folder) / "mappings"
MAPPING_DIR.mkdir(parents=True, exist_ok=True)

def load_mapping(profile: str) -> dict:
    '''Handles for mapping of parts in SVG file'''
    path = MAPPING_DIR / f"{profile}.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

@app.route("/")
def homepage():
    profile = request.args.get("profile", "front")
    mapping = load_mapping(profile)
    return render_template(
        "index.html",
        ip=_current_target[0] if _current_target else "",
        port=_current_target[1] if _current_target else "",
        connected=is_streaming_connected(),
        status_error=_status.get("last_error"),
        profile=profile,
        mapping_json=json.dumps(mapping),
        svg_path=f"/static/svg/body-{profile}.svg",
    )

@app.route("/connect", methods=["POST"])
def connect():
    ip = (request.form.get("ip") or "").strip()
    port_str = (request.form.get("port") or "").strip()

    errors = {}
    if not is_valid_ip(ip):
        errors["ip"] = "Invalid IP"
    if not is_valid_port(port_str):
        errors["port"] = "Invalid Port"

    if errors:
        return render_template("index.html", errors=errors, ip=ip, port=port_str, connected=is_streaming_connected(), status_error=_status.get("last_error"), profile="front", mapping_json=json.dumps({}), svg_path="/static/svg/body-front.svg")

    port = int(port_str)

    ok, err = preflight_connect(ip, port, timeout=2.5)
    if not ok:
        errors["connection"] = f"CONNECT error — {err}"
        return render_template("index.html", errors=errors, ip=ip, port=port_str, connected=False, status_error=err, profile="front", mapping_json=json.dumps({}), svg_path="/static/svg/body-front.svg")
    
    start_stream(ip, port)
    return render_template(
        "index.html",
        success=f"Streaming started for {ip}:{port} (check server console).",
        ip=ip,
        port=port_str,
        connected=True,
        status_error=None,
        profile="front",
        mapping_json=json.dumps({}),
        svg_path="/static/svg/body-front.svg",
    )

@app.route("/disconnect", methods=["POST"])
def disconnect():
    stop_stream()
    return render_template(
        "index.html",
        success="Disconnected.",
        connected=False,
        status_error=_status.get("last_error"),
        ip=_current_target[0] if _current_target else "",
        port=_current_target[1] if _current_target else "",
        profile="front",
        mapping_json=json.dumps({}),
        svg_path="/static/svg/body-front.svg",
    )

@app.route("/sse")
def sse_stream():
    @stream_with_context
    def gen():
        # push a heartbeat so proxies don't close the stream
        last_beat = time.time()
        def send(evt_type, data):
            payload = json.dumps(data, separators=(",", ":"))
            return f"event: {evt_type}\ndata: {payload}\n\n"

        queue = []
        def _sink(msg):
            queue.append(msg)
        unsubscribe = BUS.subscribe(_sink)
        try:
            # initial status
            yield send("status", {"connected": is_streaming_connected()})

            while True:
                now = time.time()
                if now - last_beat > 10:
                    last_beat = now
                    yield ": keep-alive\n\n"  # SSE comment

                if queue:
                    msg = queue.pop(0)
                    yield send(msg["type"], msg["data"])
                else:
                    time.sleep(0.05)
        finally:
            unsubscribe()

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"  # nginx friendly
    })

if __name__ == "__main__":
    app.run(debug=True, threaded=True)
