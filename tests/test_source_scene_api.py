from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from scripts.serve_delivery import mount_workspace_static


@pytest.fixture
def plain_service():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/api/v1/echo?scene=plain"
            assert self.headers["Cookie"] == "radio_local_session=plain"
            assert self.headers["X-Forwarded-Proto"] == "http"
            body = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Set-Cookie", "radio_local_session=new; Path=/; HttpOnly")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            assert self.path == "/api/v1/redirect"
            self.send_response(307)
            self.send_header("Location", "/api/v1/echo")
            self.end_headers()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as upstream:
        worker = threading.Thread(target=upstream.serve_forever, daemon=True)
        worker.start()
        try:
            yield upstream.server_port
        finally:
            upstream.shutdown()
            worker.join()


def test_source_workspace_routes_and_session_isolation(tmp_path, plain_service):
    (tmp_path / "index.html").write_text("<title>双场景</title>", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/v1/marker")
    def marker():
        return {"scene": "hills"}

    mount_workspace_static(app, tmp_path, plain_service)
    with TestClient(app) as client:
        assert client.get("/api/v1/marker").json() == {"scene": "hills"}
        assert client.get("/").text == client.get("/scenes/plain/").text
        assert "no-store" in client.get("/scenes/plain/").headers["Cache-Control"]
        response = client.post("/scenes/plain/api/v1/echo?scene=plain", content=b"plan-data", headers={
            "Cookie": "radio_local_session=hills; radio_plain_session=plain",
            "X-Forwarded-Proto": "https",
        })
        assert response.status_code == 200
        assert response.content == b"plan-data"
        assert response.headers["Set-Cookie"] == "radio_plain_session=new; HttpOnly; Path=/scenes/plain/"
        redirect = client.get("/scenes/plain/api/v1/redirect", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["Location"] == "/scenes/plain/api/v1/echo"
