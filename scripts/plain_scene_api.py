"""Publish an independently configured plain workspace at the source app's origin."""
from __future__ import annotations

import asyncio
from http.client import HTTPConnection, HTTPException
from urllib.parse import quote

from starlette.responses import JSONResponse


PREFIX = "/scenes/plain"
API_PREFIX = PREFIX + "/api/v1"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
COOKIES = {
    b"radio_local_session": b"radio_plain_session",
    b"radio_local_challenge": b"radio_plain_challenge",
    b"radio_integration_session": b"radio_plain_integration_session",
}
PLAIN_COOKIES = {value: key for key, value in COOKIES.items()}
HOP_HEADERS = {
    b"connection", b"keep-alive", b"proxy-connection", b"proxy-authenticate",
    b"proxy-authorization", b"te", b"trailer", b"transfer-encoding", b"upgrade",
}


def end_to_end_headers(headers):
    excluded = set(HOP_HEADERS)
    for name, value in headers:
        if name == b"connection":
            excluded.update(item.strip().lower() for item in value.split(b","))
    return [(name, value) for name, value in headers if name not in excluded]


def request_headers(headers, scheme):
    result = {}
    for name, value in end_to_end_headers(headers):
        if name == b"cookie":
            pairs = [part.strip().partition(b"=") for part in value.split(b";")]
            cookies = [PLAIN_COOKIES[key] + sep + item for key, sep, item in pairs if sep and key in PLAIN_COOKIES]
            if cookies:
                result["Cookie"] = b"; ".join(cookies).decode("latin-1")
        elif name not in {b"content-length", b"forwarded"} and not name.startswith(b"x-forwarded-"):
            result[name.decode("ascii")] = value.decode("latin-1")
    result["X-Forwarded-Proto"] = scheme
    return result


def response_headers(headers, origin):
    for name, value in end_to_end_headers(headers):
        if name == b"set-cookie":
            cookie, separator, content = value.partition(b"=")
            if cookie not in COOKIES:
                continue
            parts = [part for part in content.split(b";") if not part.lstrip().lower().startswith(b"path=")]
            value = COOKIES[cookie] + separator + b";".join(parts) + b"; Path=/scenes/plain/"
        elif name == b"location":
            if value.startswith(origin + b"/"):
                value = origin + PREFIX.encode("ascii") + value[len(origin):]
            elif value.startswith(b"/") and not value.startswith(b"//"):
                value = PREFIX.encode("ascii") + value
        yield name, value


class PlainSceneApi:
    def __init__(self, port: int):
        self.port = port

    async def __call__(self, scope, receive, send):
        path = scope["path"]
        if (not (path == API_PREFIX or path.startswith(API_PREFIX + "/"))
                or "\\" in path or any(part in {".", ".."} for part in path.split("/"))):
            await JSONResponse({"detail": "平原接口路径无效"}, status_code=404)(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                await JSONResponse({"detail": "平原接口请求超过大小限制"}, status_code=413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        target = quote(path.removeprefix(PREFIX), safe="/")
        if query := scope["query_string"]:
            target += "?" + query.decode("ascii")
        headers = request_headers(scope["headers"], scope["scheme"])
        host = headers.get("host", "")
        origin = (scope["scheme"] + "://" + host).encode("ascii")
        connection = HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            try:
                await asyncio.to_thread(connection.request, scope["method"], target, body, headers)
                response = await asyncio.to_thread(connection.getresponse)
            except (OSError, HTTPException):
                await JSONResponse({"detail": "平原工作区服务暂不可用"}, status_code=503)(scope, receive, send)
                return
            upstream = [(key.lower().encode("ascii"), value.encode("latin-1")) for key, value in response.getheaders()]
            await send({"type": "http.response.start", "status": response.status,
                        "headers": list(response_headers(upstream, origin))})
            while chunk := await asyncio.to_thread(response.read, 128 * 1024):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            connection.close()
