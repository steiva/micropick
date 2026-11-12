"""The robot client's connections end a wait on a robot gone silent: TCP
keep-alive on every one, a connect timeout and no read timeout (GUI-05)."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from opentrons_api.ot2_api import OpentronsAPI, keepalive_options


def test_every_connection_has_keep_alive():
    api = OpentronsAPI(host="127.0.0.1", port=1)
    pool = api.http.get_adapter("http://127.0.0.1").poolmanager
    options = pool.connection_pool_kw["socket_options"]
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in options
    assert set(keepalive_options()) <= set(options)
    # An idle time at least, on every platform this runs on.
    assert len(keepalive_options()) >= 2


def test_the_client_still_talks_to_a_robot():
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"name": "ot2"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api = OpentronsAPI(host="127.0.0.1", port=server.server_port)
        assert api.health()["name"] == "ot2"
    finally:
        server.shutdown()


def test_the_session_waits_for_a_slow_command_not_for_a_connect(
        tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    from micropick.gui.app import Options
    from micropick.gui.session import CONNECT_TIMEOUT_S, Session
    session = Session(Options(mock=False))
    try:
        api = session._client()
        assert api.timeout == (CONNECT_TIMEOUT_S, None)
    finally:
        session._api = None
        session.shutdown()
