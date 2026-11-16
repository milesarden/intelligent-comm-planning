"""Serve the independent plain scene through the portable loopback origin."""
from __future__ import annotations

import asyncio
import hmac
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote


PREFIX = "/scenes/plain"
INTERNAL = b"x-portable-scene-token"
HOP_BY_HOP = frozenset({b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
                        b"proxy-connection", b"te", b"trailer", b"transfer-encoding", b"upgrade"})
COOKIES = {name: name.replace(b"radio_local_", b"radio_plain_")
           for name in (b"radio_local_session", b"radio_local_challenge")}
PLAIN_COOKIE_NAMES = {value: key for key, value in COOKIES.items()}
# 32 MiB file imports are represented as base64 in JSON.
MAX_REQUEST_BYTES = 64 * 1024 * 1024
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_write(scope):
    return scope["method"] not in SAFE_METHODS and scope["path"].startswith("/api/v1/")


def forwarded_headers(headers):
    excluded = HOP_BY_HOP | {INTERNAL}
    for name, value in headers:
        if name == b"connection":
            excluded |= {token.strip().lower() for token in value.split(b",")}
    return [(name, value) for name, value in headers if name not in excluded]


def plain_request_headers(headers):
    for name, value in forwarded_headers(headers):
        if name == b"cookie":
            mapped = []
            for item in value.split(b";"):
                cookie, separator, contents = item.strip().partition(b"=")
                if separator and cookie in PLAIN_COOKIE_NAMES:
                    mapped.append(PLAIN_COOKIE_NAMES[cookie] + separator + contents)
            if mapped:
                yield name, b"; ".join(mapped)
        elif name not in (b"host", b"content-length"):
            yield name, value


def plain_response_headers(headers, origin):
    for name, value in forwarded_headers(headers):
        if name == b"set-cookie":
            cookie, separator, contents = value.partition(b"=")
            if cookie not in COOKIES:
                continue
            value = (COOKIES[cookie] + separator + contents).replace(b"Path=/", b"Path=/scenes/plain/")
        elif name == b"location":
            if value.startswith(origin + b"/"):
                value = origin + PREFIX.encode("ascii") + value[len(origin):]
            elif value.startswith(b"/") and not value.startswith(b"//"):
                value = PREFIX.encode("ascii") + value
        yield name, value


def response_error(status, message):
    from starlette.responses import JSONResponse
    return JSONResponse({"code": 5002, "message": message, "data": None}, status_code=status)


class PlainScene:
    def __init__(self, root, data, public_port, shutdown):
        self.root = root
        self.data = data
        self.authority = f"127.0.0.1:{public_port}"
        self.origin = f"http://{self.authority}".encode("ascii")
        self.shutdown = shutdown
        self.port = None
        launch_id = secrets.token_hex(12)
        self.setup_file = data / f"plain-setup-{launch_id}.json"
        self.ready_file = data / f"plain-ready-{launch_id}.json"
        self.token = secrets.token_urlsafe(32)
        self.process = None
        self.stop_requested = False
        manifest = json.loads((root / "package-manifest.json").read_text(encoding="utf-8"))
        self.shared_static = frozenset(manifest.get("plain_shared_static", ()))
        for name in self.shared_static:
            relative = PurePosixPath(name)
            if (relative.as_posix() != name or len(relative.parts) < 2
                    or relative.parts[0] not in {"maps", "fonts"} or ".." in relative.parts
                    or "\\" in name):
                raise RuntimeError("平原共享静态资源清单存在无效路径")

    def start(self, timeout=120):
        logs = self.data / "logs"
        logs.mkdir(exist_ok=True)
        process_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            setup = {"public_port": int(self.authority.split(":")[1]), "token": self.token,
                     "parent_pid": os.getpid()}
            with self.setup_file.open("x", encoding="utf-8") as file:
                json.dump(setup, file)
            # Windows 算法 spawn 需要可关闭的标准输入；保留与单场景启动相同的 DEVNULL。
            with (logs / "plain-launcher.log").open("ab") as log:
                self.process = subprocess.Popen([sys.executable, "-B", str(self.root / "launcher.py"),
                    "plain-worker", "--scene-setup", self.setup_file.name], cwd=self.root,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, **process_options)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and self.process.poll() is None:
                if self.ready_file.is_file():
                    ready = json.loads(self.ready_file.read_text(encoding="utf-8"))
                    if ready.get("pid") != self.process.pid or not 1024 <= ready.get("port", 0) <= 65535:
                        break
                    self.port = ready["port"]
                    try:
                        if self.control("status") == 200:
                            return
                    except (OSError, HTTPException):
                        pass
                time.sleep(.2)
            raise RuntimeError("平原场景启动失败，请查看 app/data/runtime/logs/plain-launcher.log")
        finally:
            self.setup_file.unlink(missing_ok=True)
            self.ready_file.unlink(missing_ok=True)

    def control(self, action):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=1)
        try:
            connection.request("POST" if action == "stop" else "GET", "/__scene/" + action,
                               headers={"Host": self.authority, INTERNAL.decode(): self.token})
            return connection.getresponse().status
        finally:
            connection.close()

    async def request_stop(self):
        if self.process.poll() is not None:
            return True
        try:
            self.stop_requested = await asyncio.to_thread(self.control, "stop") == 200
            return self.stop_requested
        except (OSError, HTTPException):
            return self.process.poll() is not None

    def cleanup(self):
        self.setup_file.unlink(missing_ok=True)
        self.ready_file.unlink(missing_ok=True)
        if self.process is not None and self.process.poll() is None and self.port and not self.stop_requested:
            try:
                self.stop_requested = self.control("stop") == 200
            except (OSError, HTTPException):
                pass  # 父进程退出后，子进程的父进程监视仍会收尾。

    def finish(self):
        self.cleanup()
        try:
            return self.process.wait(timeout=30) == 0
        except subprocess.TimeoutExpired:
            return False

    def dispatch(self, hills):
        async def handler(scope, receive, send):
            if scope["type"] != "http":
                await hills(scope, receive, send)
                return
            path = scope["path"]
            if path != PREFIX and not path.startswith(PREFIX + "/"):
                await hills(scope, receive, send)
                return
            headers = dict(scope["headers"])
            client = scope.get("client")
            if headers.get(b"host") != self.authority.encode() or not client or client[0] != "127.0.0.1":
                await response_error(403, "平原场景仅允许受控本机同源入口")(scope, receive, send)
                return
            if path == PREFIX:
                from starlette.responses import RedirectResponse
                query = scope["query_string"].decode("ascii")
                await RedirectResponse(PREFIX + "/" + ("?" + query if query else ""))(scope, receive, send)
                return
            part = path[len(PREFIX):]
            if any(segment in {"..", "."} for segment in part.split("/")) or "\\" in part or "//" in part:
                await response_error(404, "场景资源路径无效")(scope, receive, send)
                return
            local = {**scope, "path": part, "raw_path": quote(part, safe="/").encode("ascii")}
            if scope["method"] in {"GET", "HEAD"} and part[1:] in self.shared_static:
                await hills(local, receive, send)
                return
            if part.startswith("/__"):
                await response_error(404, "场景接口不存在")(scope, receive, send)
                return
            writing = is_write(local)
            if writing and self.shutdown["requested"]:
                await response_error(503, "系统正在停止，请稍后重试")(scope, receive, send)
                return
            if writing:
                self.shutdown["writes"] += 1
            try:
                await self.proxy(local, receive, send)
            finally:
                if writing:
                    self.shutdown["writes"] -= 1
        return handler

    async def proxy(self, scope, receive, send):
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                await response_error(413, "场景请求超过支持的文件大小")(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        connection = HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            try:
                response = await asyncio.to_thread(self.request, connection, scope, body)
            except (OSError, HTTPException):
                await response_error(503, "平原场景服务暂不可用，请检查本机启动日志")(scope, receive, send)
                return
            headers = [(name.lower().encode("ascii"), value.encode("latin-1")) for name, value in response.getheaders()]
            await send({"type": "http.response.start", "status": response.status,
                        "headers": list(plain_response_headers(headers, self.origin))})
            while chunk := await asyncio.to_thread(response.read, 128 * 1024):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            connection.close()

    def request(self, connection, scope, body):
        query = scope["query_string"].decode("ascii")
        path = scope["raw_path"].decode("ascii") + ("?" + query if query else "")
        connection.putrequest(scope["method"], path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", self.authority)
        connection.putheader(INTERNAL.decode(), self.token)
        connection.putheader("Connection", "close")
        if body or scope["method"] not in SAFE_METHODS:
            connection.putheader("Content-Length", str(len(body)))
        for name, value in plain_request_headers(scope["headers"]):
            connection.putheader(name, value)
        connection.endheaders(body)
        return connection.getresponse()


def parent_waiter(pid):
    if pid != os.getppid():
        raise RuntimeError("平原场景的父进程与启动参数不一致")
    if os.name != "nt":
        def wait():
            while os.getppid() == pid:
                time.sleep(.25)
        return wait
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    def wait():
        try:
            kernel.WaitForSingleObject(handle, 0xffffffff)
        finally:
            kernel.CloseHandle(handle)
    return wait


def run_plain_worker(root, parent_data, setup_name, configure_plain, active_algorithms, write_json):
    if (not re.fullmatch(r"plain-setup-[a-f0-9]{24}\.json", setup_name)
            or parent_data != root / "app/data/runtime"):
        raise RuntimeError("平原场景子进程的启动参数无效")
    setup_file = parent_data / setup_name
    with setup_file.open(encoding="utf-8") as file:
        setup = json.loads(file.read(1024))
    setup_file.unlink()
    public_port, token = setup["public_port"], setup["token"]
    ready = setup_name.replace("plain-setup-", "plain-ready-")
    if (not 1024 <= public_port <= 65535 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
            or not isinstance(setup["parent_pid"], int)):
        raise RuntimeError("平原场景子进程的启动参数无效")
    wait_for_parent = parent_waiter(setup["parent_pid"])
    configure_plain(public_port)
    from scripts.serve_delivery import create_delivery_app
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    import uvicorn

    app = create_delivery_app()
    shutdown = {"requested": False, "writes": 0}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]

        async def protected(scope, receive, send):
            if scope["type"] == "http":
                header = dict(scope["headers"]).get(INTERNAL, b"")
                if not hmac.compare_digest(header, token.encode("ascii")):
                    await response_error(403, "平原子进程仅允许便携入口访问")(scope, receive, send)
                    return
            await app(scope, receive, send)

        server = uvicorn.Server(uvicorn.Config(protected, host="127.0.0.1", port=port, access_log=False,
                                  proxy_headers=False, loop="asyncio", http="h11", timeout_graceful_shutdown=30))

        @app.middleware("http")
        async def write_guard(request, call_next):
            writing = is_write(request.scope)
            if writing and shutdown["requested"]:
                return response_error(503, "系统正在停止，请稍后重试")
            if writing:
                shutdown["writes"] += 1
            try:
                return await call_next(request)
            finally:
                if writing:
                    shutdown["writes"] -= 1

        async def endpoint(request):
            if request.url.path.endswith("/stop"):
                if shutdown["writes"] or active_algorithms():
                    return JSONResponse({"state": "BUSY"}, status_code=409)
                shutdown["requested"] = True
                server.should_exit = True
            return JSONResponse({"state": "STOPPING" if server.should_exit else "RUNNING"})

        def watch_parent():
            try:
                wait_for_parent()
            finally:
                server.should_exit = True

        app.router.routes[0:0] = [Route("/__scene/status", endpoint, methods=["GET"]),
                                  Route("/__scene/stop", endpoint, methods=["POST"])]
        threading.Thread(target=watch_parent, name="portable-parent-exit", daemon=True).start()
        write_json(parent_data / ready, {"port": port, "pid": os.getpid()})
        server.run(sockets=[listener])
        if not server.started or server.lifespan.shutdown_failed or server.lifespan.error_occurred or active_algorithms():
            raise RuntimeError("平原场景的后台工作未确认安全退出")
