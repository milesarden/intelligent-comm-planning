"""便携包控制入口，只管理本目录的本机服务。"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hmac
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
# 沿用后端现有运行目录校验，不放宽安全边界或修改业务代码。
DATA = APP / "data/runtime"
STATE = DATA / "service.json"


_JS_TOKENS = re.compile(
    r"(?P<comment>//[^\r\n]*|/\*[\s\S]*?\*/)"
    r"|(?P<string>\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'|`(?:\\[\s\S]|[^`\\])*`)"
    r"|(?P<word>[A-Za-z_$][\w$]*)|(?P<symbol>=>|\?\.|[^\s])"
)
_JS_REGEXP = re.compile(r"/(?:\\.|\[(?:\\.|[^\]\\\r\n])*\]|[^/\\\[\r\n])+/[a-z]*")
_VITE_DEPENDENCIES = re.compile(r"\bconst\s+__vite__mapDeps\s*=[^;]*?\.f\s*=\s*(\[[^\]]*\])")
_CSS_REFERENCES = re.compile(
    r"/\*[\s\S]*?\*/|(?:url\(\s*|@import\s+)(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<bare>[^)\s\"']+))"
    r"|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", re.IGNORECASE
)


def javascript_asset_references(source):
    """读取构建产物的 import/from、字面量 import() 及 Vite 预加载表，不执行 JS。"""
    def literal(value):
        def escaped(match):
            value = match.group(1)
            if value.startswith(("u", "x")) and len(value) > 1:
                return chr(int(value[1:].strip("{}"), 16))
            return {"n": "\n", "r": "\r", "t": "\t", "\n": "", "\r\n": ""}.get(value, value)
        return re.sub(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])", escaped, value[1:-1])

    previous, pending, candidate, position = None, None, None, 0
    while token := _JS_TOKENS.search(source, position):
        position = token.end()
        if token.lastgroup == "comment":
            continue
        value, kind = token.group(), token.lastgroup
        # 正则表达式中的 import 字样不是模块依赖；除法不能吞掉后面的真实导入。
        if value == "/" and (previous is None or previous in ("=", "(", "[", "{", ",", ":", ";", "!", "?", "return", "throw", "=>")):
            regexp = _JS_REGEXP.match(source, token.start())
            if regexp:
                position = regexp.end()
                previous, pending = "regexp", None
                continue
        if candidate is not None:
            if value in (")", ","):
                yield candidate
            candidate = None
        if kind == "string" and pending in ("import", "from", "dynamic"):
            if not (value.startswith("`") and "${" in value):
                if pending == "dynamic":
                    candidate = literal(value)
                else:
                    yield literal(value)
            pending = None
        elif value == "(" and pending == "import":
            pending = "dynamic"
        else:
            pending = value if kind == "word" and value in ("import", "from") and previous not in (".", "?.") else None
        previous = value if kind != "string" else "string"
    for mapping in _VITE_DEPENDENCIES.finditer(source):
        for token in _JS_TOKENS.finditer(mapping.group(1)):
            if token.lastgroup == "string":
                yield literal(token.group())


class _FrontendHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if values.get("src"):
            self.references.append(values["src"])
        if tag == "link" and values.get("href") and set(values.get("rel", "").lower().split()) & {
            "stylesheet", "modulepreload", "preload", "icon", "manifest",
        }:
            self.references.append(values["href"])


def check_frontend_assets(dist=None):
    """启动前只读检查首页及其 JS 依赖闭包；不读写运行数据、不计算整包哈希。"""
    dist = Path(dist).resolve() if dist is not None else (APP / "front_design/radio-planning-20261022/dist").resolve()
    pending = [(dist / "index.html", "./index.html")]
    visited = set()
    while pending:
        parent, reference = pending.pop()
        url = urlsplit(reference.replace("\\", "/"))
        if url.scheme or url.netloc or not url.path:
            continue  # 外部地址、内嵌数据和页内锚点不访问网络。
        path = unquote(url.path)
        asset = (dist / path.lstrip("/") if path.startswith("/") else parent.parent / path).resolve()
        origin = parent.relative_to(dist).as_posix()
        if not asset.is_relative_to(dist):
            raise RuntimeError("前端资源引用越界：" + reference + "（引用来源：" + origin + "）")
        relative = asset.relative_to(dist).as_posix()
        if not asset.is_file():
            raise RuntimeError("前端资源缺失：" + relative + "（引用来源：" + origin + "）；请恢复同一版本的完整前端文件后重试")
        if asset in visited:
            continue
        visited.add(asset)
        if asset.suffix.lower() not in (".html", ".js", ".mjs", ".css"):
            continue
        source = asset.read_text(encoding="utf-8-sig")
        if asset.suffix.lower() == ".html":
            html = _FrontendHTML()
            html.feed(source)
            references = html.references
        elif asset.suffix.lower() == ".css":
            references = (next(value for value in match.groups() if value is not None)
                          for match in _CSS_REFERENCES.finditer(source) if any(value is not None for value in match.groups()))
        else:
            references = javascript_asset_references(source)
        pending.extend((asset, value) for value in references)
    return tuple(sorted(visited))


def secure_data_directory():
    """NTFS 上限制凭据目录；exFAT 无 ACL，明确记录本地保管边界。"""
    if os.name != "nt":
        secure_posix_data_directory(DATA)
        return
    DATA.mkdir(parents=True, exist_ok=True)
    volume = ctypes.create_unicode_buffer(261)
    filesystem = ctypes.create_unicode_buffer(261)
    if not ctypes.windll.kernel32.GetVolumePathNameW(str(DATA), volume, len(volume)):
        raise OSError("无法核对用户数据磁盘")
    if not ctypes.windll.kernel32.GetVolumeInformationW(volume.value, None, 0, None, None, None, filesystem, len(filesystem)):
        raise OSError("无法核对用户数据文件系统")
    if filesystem.value.upper() != "NTFS":
        (DATA / "本地数据保管提示.txt").write_text("当前文件系统不支持 NTFS 访问控制；请保管整个目录，勿放在多人可读写的共享盘。\n", encoding="utf-8")
        return
    import re
    system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    identity = subprocess.check_output([str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"], creationflags=subprocess.CREATE_NO_WINDOW)
    sid = re.search(rb"S-\d+(?:-\d+)+", identity)
    if sid is None:
        raise RuntimeError("不能确定本地用户 SID，未写入凭据")
    subprocess.run([str(system / "icacls.exe"), str(DATA), "/inheritance:r", "/grant:r",
        "*" + sid.group().decode("ascii") + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


def secure_posix_data_directory(directory):
    """Linux 运行数据由启动服务的本地用户独占。"""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise PermissionError("本地运行目录必须由当前用户拥有，且不能是符号链接")
    directory.chmod(0o700)
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise PermissionError("文件系统无法限制凭据目录访问，请使用支持 Linux 权限的本地文件系统")


def write_json(path, value):
    """同目录原子替换；不截断已有有效配置。"""
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def read_state():
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        if value.get("root") != str(ROOT) or not 1024 <= value.get("port", 0) <= 65535:
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def control(state, action="status"):
    """禁止系统代理转发本机管理请求；令牌不进入 URL 或日志。"""
    request = urllib.request.Request(
        f"http://127.0.0.1:{state['port']}/__portable/{action}",
        headers={"X-Portable-Token": state["token"]},
        data=b"" if action == "stop" else None,
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as reply:
            value = json.load(reply)
    except urllib.error.HTTPError as exc:
        if action == "stop" and exc.code == 409:
            raise RuntimeError("存在运行中的计算或提交，请等待完成或在页面取消后再次停止") from None
        raise
    if value.get("instance") != state.get("instance"):
        raise ValueError("服务身份不匹配")
    return value


def available_state():
    state = read_state()
    if state and state.get("state") == "RUNNING":
        try:
            current = control(state)
            return {**state, "plain_failed": True} if current.get("state") == "PLAIN_FAILED" else state
        except (OSError, ValueError, KeyError):
            pass
    return None


def configure(port, key):
    # 不继承开发电脑的库、目录、演示身份和监听地址。
    for name in list(os.environ):
        if name in ("DATABASE_URL", "AUTH_MODE", "RESOURCE_DATASET_MODE", "ALGORITHM_TASK_DIR") or name.startswith(
            ("LOCAL_IDENTITY_", "FRONTEND_INTEGRATION_", "SR4_", "SR5_", "SR6_", "COVERAGE_", "FROZEN_COVERAGE_", "INTERFERENCE_")
        ):
            os.environ.pop(name, None)
    work = DATA / "r2/portable"
    folders = {name: work / name for name in ("tmp", "logs", "tasks", "plans", "reports", "snapshots", "frozen_coverage", "coverage", "coverage_tasks")}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        # SQLite URL 也会解码百分号；先编码字面路径，避免 %27 等目录被读成其他字符。
        "DATABASE_URL": "sqlite:///" + quote((DATA / "r2.sqlite").as_posix(), safe="/:"),
        "RESOURCE_DATASET_MODE": "database", "AUTH_MODE": "LOCAL_SINGLE_USER",
        "LOCAL_IDENTITY_REQUEST_KEY": key,
        "LOCAL_IDENTITY_ALLOW_INSECURE_LOOPBACK": "1",
        "LOCAL_IDENTITY_ALLOWED_ORIGINS": json.dumps([f"http://127.0.0.1:{port}"]),
        "INTERFERENCE_WORKBENCH_ENABLED": "1", "FROZEN_COVERAGE_ENABLED": "1",
        "INTERFERENCE_RUNTIME_DIR": str(work),
        "FROZEN_COVERAGE_RUNTIME_DIR": str(folders["frozen_coverage"]),
        "ALGORITHM_TASK_DIR": str(folders["tasks"]), "SR4_PLAN_DIR": str(folders["plans"]),
        "SR4_SNAPSHOT_DIR": str(folders["snapshots"]),
        "SR5_STORE_PATH": str(DATA / "fault_scenarios.json"), "SR5_REPORT_DIR": str(folders["reports"]),
        "SR6_STORE_PATH": str(DATA / "sr6_graph.json"),
        "COVERAGE_CACHE_DIR": str(folders["coverage"]), "COVERAGE_TASK_DIR": str(folders["coverage_tasks"]),
        "TEMP": str(folders["tmp"]), "TMP": str(folders["tmp"]), "TMPDIR": str(folders["tmp"]),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    for path in (APP, APP / "algorithm-service", APP / "scripts"):
        sys.path.insert(0, str(path))


def settings():
    path = DATA / "settings.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if len(base64.b64decode(value["request_key"], validate=True)) != 32:
            raise ValueError("本地密钥配置损坏；不会自动覆盖")
        return value
    if (DATA / "r2.sqlite").exists() or (DATA / "initialized.json").exists():
        raise RuntimeError("已存在数据库或初始化标记但缺少配置，请恢复原 app/data/runtime；不会重置数据库")
    value = {"request_key": base64.b64encode(secrets.token_bytes(32)).decode("ascii"), "accounts": {
        role: {"username": "portable_" + role, "password": "Aa1!" + secrets.token_urlsafe(20)}
        for role in ("admin", "editor", "reviewer")
    }}
    write_json(path, value)
    text = ["本包保留的本地账号（仅适用于此解压目录）", "系统默认以本机统一入口直接进入GIS，无需输入账号密码或切换角色。", "以下账号供单独配置LOCAL_PASSWORD模式时使用；首次密码登录后须修改初始密码。", "请妥善保管此文件及 settings.json，不要公开发送 app/data/runtime。", ""]
    for role, name in (("editor", "业务编辑者"), ("reviewer", "知识审核者"), ("admin", "系统管理员")):
        account = value["accounts"][role]
        text += [name, "账号：" + account["username"], "初始密码：" + account["password"], ""]
    (DATA / "首次登录账号.txt").write_text("\n".join(text), encoding="utf-8")
    return value


def migration_config():
    """迁移目录直接来自本包位置，避开 INI 的 here 对路径中百分号的二次解释。"""
    from alembic.config import Config
    config = Config()
    config.set_main_option("script_location", str(APP / "algorithm-service/migrations").replace("%", "%%"))
    return config


def initialize(config):
    """仅首次初始化本包新库；故障保留现场，重启不覆盖资源和账号。"""
    database = DATA / "r2.sqlite"
    marker = DATA / "initialized.json"
    if marker.exists() and not database.is_file():
        raise RuntimeError("已初始化但数据库缺失；请恢复完整数据目录，不会创建空库")
    if database.exists():
        if not marker.is_file():
            raise RuntimeError("上次初始化未完成；请查看日志并保留运行目录，不自动重建")
        from alembic.script import ScriptDirectory
        import sqlite3
        expected = ScriptDirectory.from_config(migration_config()).get_current_head()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            actual = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if actual != (expected,):
            raise RuntimeError("数据库版本与本包不一致；请单独升级，不自动迁移已有数据")
        return
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.permission_registry import SERVER_PERMISSION_REGISTRY
    from app.system_management_schemas import ADMIN_PERMISSIONS, SystemActor
    from app.system_management_service import SystemManagementService
    command.upgrade(migration_config(), "head")
    from app.resource_dataset_service import initialize as seed_resources
    seed_resources()
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        service = SystemManagementService(session_factory=factory, permission_registry=SERVER_PERMISSION_REGISTRY,
                                         request_key=base64.b64decode(config["request_key"]))
        admin = config["accounts"]["admin"]
        aid = service.initialize(username=admin["username"], password=admin["password"], name="便携包管理员", department="本地验收")
        from app.system_management_models import LocalCredential
        with factory.begin() as session:
            session.get(LocalCredential, aid).must_change_password = True
        actor = SystemActor(aid, "portable-initialization", "1", ADMIN_PERMISSIONS)
        for role, label in (("editor", "业务编辑者"), ("reviewer", "知识审核者")):
            permissions = ([row["code"] for row in SERVER_PERMISSION_REGISTRY if not row["code"].startswith("system.")
                            and row["code"] not in ("knowledge.review", "knowledge.publish", "knowledge.rollback", "knowledge.reset")]
                           if role == "editor" else ["situation.view", "resources.view", "planning.view", "diagnosis.view",
                                                    "knowledge.view", "knowledge.review", "knowledge.publish", "knowledge.rollback"])
            revision = service.read("options", actor)["revision"]
            created = service.execute("role.create", actor, {"baseRevision": revision, "requestId": "portable-role-" + role,
                "role": {"name": label, "description": "本地便携包独立身份", "permissions": permissions}})
            revision = service.read("options", actor)["revision"]
            account = config["accounts"][role]
            service.execute("account.create", actor, {"baseRevision": revision, "requestId": "portable-account-" + role,
                "account": {"username": account["username"], "name": label, "department": "本地验收", "roleId": created["objectId"]},
                "initialPassword": account["password"]})
    finally:
        engine.dispose()
    write_json(marker, {"schema": 1, "created_at": time.time(), "data_origin": "SYNTHETIC_INTEGRATION"})


def bind_listener(preferred=18480):
    for port in range(preferred, preferred + 100):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            return listener, port
        except OSError:
            listener.close()
    raise RuntimeError(f"{preferred}—{preferred + 99} 端口均不可用；未停止其他服务")


def acquire_service_lock(lock):
    if os.name != "nt":
        import fcntl
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    import msvcrt
    lock.seek(0)
    if not lock.read(1):
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


def unified_package():
    try:
        manifest = json.loads((ROOT / "package-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("terrain_scene") == "unified"


def check_package_frontends():
    check_frontend_assets()
    if unified_package():
        check_frontend_assets(ROOT / "scenes/plain/app/front_design/radio-planning-20261022/dist")


def unified_runtime_module():
    # Windows 的嵌入式 Python 可能没有将 launcher.py 同目录加入 sys.path。
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import unified_runtime
    return unified_runtime


def configure_plain_worker(public_port):
    """只在独立子进程切换应用根，移除共享运行时 _pth 中的丘陵应用路径。"""
    global APP, DATA
    hills_paths = {APP, APP / "algorithm-service", APP / "scripts"}
    sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() not in hills_paths]
    APP = (ROOT / "scenes/plain/app").resolve()
    if APP != ROOT / "scenes/plain/app":
        raise RuntimeError("平原场景的应用目录不应重定向到其他位置")
    DATA = APP / "data/runtime"
    secure_data_directory()
    config = settings()
    configure(public_port, config["request_key"])
    os.chdir(APP)
    initialize(config)


def serve():
    check_package_frontends()
    secure_data_directory()
    with (DATA / "service.lock").open("a+b") as lock:
        if not acquire_service_lock(lock):
            return 0  # 另一启动入口已经持有本包锁。
        listener = None
        state = None
        plain_scene = None
        clean_shutdown = False
        try:
            listener, port = bind_listener()
            config = settings()
            configure(port, config["request_key"])
            os.chdir(APP)
            initialize(config)
            from scripts.serve_delivery import create_delivery_app
            from starlette.responses import JSONResponse
            from starlette.routing import Route
            import uvicorn
            app = create_delivery_app()
            shutdown = {"requested": False, "writes": 0}
            delivery_app = app
            if unified_package():
                plain_scene = unified_runtime_module().PlainScene(ROOT, DATA, port, shutdown)
                plain_scene.start()
                delivery_app = plain_scene.dispatch(app)
            server = uvicorn.Server(uvicorn.Config(delivery_app, host="127.0.0.1", port=port, access_log=False, proxy_headers=False,
                                                  loop="asyncio", http="h11", timeout_graceful_shutdown=30))
            state = {"root": str(ROOT), "port": port, "pid": os.getpid(), "instance": secrets.token_hex(16),
                     "token": secrets.token_urlsafe(32), "state": "RUNNING", "started_at": time.time()}

            @app.middleware("http")
            async def write_guard(request, call_next):
                writing = request.url.path.startswith("/api/v1/") and request.method not in ("GET", "HEAD", "OPTIONS")
                if writing and shutdown["requested"]:
                    return JSONResponse({"code": 5002, "message": "系统正在停止，请稍后重试", "data": None}, status_code=503)
                if writing:
                    shutdown["writes"] += 1
                try:
                    return await call_next(request)
                finally:
                    if writing:
                        shutdown["writes"] -= 1

            async def endpoint(request):
                supplied = request.headers.get("X-Portable-Token", "")
                if not supplied.isascii() or not hmac.compare_digest(supplied, state["token"]):
                    return JSONResponse({"error": "forbidden"}, status_code=403)
                if request.url.path.endswith("/stop"):
                    if shutdown["requested"]:
                        return JSONResponse({"instance": state["instance"], "state": "STOPPING"})
                    shutdown["requested"] = True
                    if (shutdown["writes"] or active_algorithm_threads()
                            or plain_scene is not None and not await plain_scene.request_stop()):
                        shutdown["requested"] = False
                        return JSONResponse({"error": "BUSY", "instance": state["instance"]}, status_code=409)
                    server.should_exit = True
                elif plain_scene is not None and plain_scene.process.poll() is not None:
                    return JSONResponse({"instance": state["instance"], "state": "PLAIN_FAILED"})
                return JSONResponse({"instance": state["instance"], "state": "STOPPING" if server.should_exit else "RUNNING"})

            app.router.routes[0:0] = [Route("/__portable/status", endpoint, methods=["GET"]),
                                      Route("/__portable/stop", endpoint, methods=["POST"])]
            write_json(STATE, state)
            server.run(sockets=[listener])
            clean_shutdown = server.started and not server.lifespan.shutdown_failed and not server.lifespan.error_occurred and not active_algorithm_threads()
            if plain_scene is not None:
                clean_shutdown = plain_scene.finish() and clean_shutdown
            if not server.started:
                raise RuntimeError("服务启动失败，请查看 user_data/logs")
            if not clean_shutdown:
                raise RuntimeError("后台任务未确认安全退出，停止失败，请查看运行日志")
        finally:
            if plain_scene is not None:
                plain_scene.cleanup()
            if listener:
                listener.close()
            if state:
                write_json(STATE, {**state, "state": "STOPPED" if clean_shutdown else "STOP_FAILED", "stopped_at": time.time()})


def active_algorithm_threads():
    # 普通规划/诊断通过既有 _spawn 启动；不能仅以其已标记取消认定线程已退出。
    return [thread for thread in threading.enumerate() if thread.is_alive()
            and getattr(getattr(thread, "_target", None), "__module__", "") in
            {"app.planning_w3", "app.coverage", "app.deployment"}]


def controlled_frontend_version(root=None):
    """只读取公开清单；无效或旧清单不沿用可缓存的固定入口。"""
    path = (Path(root) if root is not None else ROOT) / "package-manifest.json"
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            return None
        manifest = json.loads(path.read_text(encoding="utf-8"))
        version, snapshot = manifest.get("controlled_version"), manifest.get("content_snapshot_sha256")
        if (type(version) is str and re.fullmatch(r"LOCAL-[0-9]{8}-[0-9a-f]{12}", version)
                and type(snapshot) is str and re.fullmatch(r"[0-9a-f]{64}", snapshot)
                and version.endswith(snapshot[:12])):
            return version
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def frontend_entry_url(port):
    version = controlled_frontend_version() or "reload-" + secrets.token_hex(12)
    return f"http://127.0.0.1:{port}/?v={quote(version, safe='')}#/situation"


def open_entry(url):
    if os.name == "nt":
        os.startfile(url)
    else:
        import webbrowser
        webbrowser.open(url)


def start(open_browser=True, timeout=180):
    check_package_frontends()
    secure_data_directory()
    current = available_state()
    if current and current.get("plain_failed"):
        raise RuntimeError("平原场景进程已退出，请查看启动日志，停止本包后重启")
    if not current:
        logs = DATA / "logs"
        logs.mkdir(exist_ok=True)
        process_options = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                           else {"start_new_session": True})
        with (logs / "launcher.log").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-B", str(ROOT / "launcher.py"), "serve"], cwd=ROOT,
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, **process_options)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = available_state()
            if current:
                break
            if child.poll() not in (None, 0):
                raise RuntimeError("启动失败，请查看 app/data/runtime/logs/launcher.log")
            time.sleep(.4)
        if not current:
            raise RuntimeError("等待启动超时，请查看 app/data/runtime/logs/launcher.log；不会强行结束其他程序")
    url = frontend_entry_url(current['port'])
    (DATA / "访问地址.txt").write_text(url + "\n", encoding="utf-8")
    if open_browser:
        open_entry(url)
    print("系统已启动：" + url)
    print("已启用本机统一使用入口，无需登录；使用‘停止系统’退出后台服务。")
    return current


def stop(timeout=60):
    state = available_state()
    if not state:
        print("本目录系统未运行。")
        return
    control(state, "stop")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = read_state()
        if current and current.get("instance") == state["instance"] and current.get("state") == "STOPPED":
            print("系统已停止，用户数据已保留。")
            return
        if current and current.get("instance") == state["instance"] and current.get("state") == "STOP_FAILED":
            raise RuntimeError("后台工作流退出失败，请查看运行日志；未报告为安全停止")
        time.sleep(.4)
    raise RuntimeError("尚未完成安全停止，请等待当前计算结束并查看日志；未强制杀进程")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "serve", "plain-worker"))
    parser.add_argument("--no-browser", action="store_true", help="自动化验收不打开用户浏览器")
    parser.add_argument("--scene-setup", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "nt":
        if sys.platform != "linux":
            parser.error("此启动入口仅支持 Windows 和 Linux")
        os.umask(0o077)
    try:
        if args.action == "plain-worker":
            if not args.scene_setup or not unified_package():
                raise RuntimeError("本包未提供平原场景子进程")
            unified_runtime_module().run_plain_worker(
                ROOT, DATA, args.scene_setup, configure_plain_worker, active_algorithm_threads, write_json)
        elif args.action == "serve":
            serve()
        elif args.action == "stop":
            stop()
        else:
            start(not args.no_browser)
        return 0
    except Exception as exc:
        print("操作未完成：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
