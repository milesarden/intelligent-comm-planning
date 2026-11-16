"""只提供最新版前端及同源 API；显式使用已有数据库，不自动迁移或重建。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if __package__:
    from .plain_scene_api import API_PREFIX, PREFIX, PlainSceneApi
else:
    from plain_scene_api import API_PREFIX, PREFIX, PlainSceneApi

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = ROOT / "front_design" / "radio-planning-20261022" / "dist"


class DeliveryStaticFiles(StaticFiles):
    """HTML每次读取当前入口；带内容指纹的脚本仍使用原静态缓存规则。"""
    def is_not_modified(self, response_headers, request_headers):
        if response_headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/html":
            return False
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/html":
            response.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                                     "Pragma": "no-cache", "Expires": "0"})
        return response


def terrain_file_response(file):
    async def serve(request):
        return FileResponse(file)

    return serve


def mount_workspace_static(app, dist, plain_api_port=None, plain_map_dir=None):
    if (plain_api_port is None) != (plain_map_dir is None):
        raise ValueError("平原场景须配置独立后端端口与底图目录")
    if plain_api_port is not None:
        terrain_files = [plain_map_dir / name for name in ("sr4-terrain.json", "sr4-terrain.png")]
        if not all(file.is_file() for file in terrain_files):
            raise ValueError("平原底图目录须包含 sr4-terrain.json 和 sr4-terrain.png")
        app.mount(API_PREFIX, PlainSceneApi(plain_api_port), name="plain-api")
        for file in terrain_files:
            app.add_route(PREFIX + "/maps/" + file.name, terrain_file_response(file), methods=["GET"])
        app.mount(PREFIX, DeliveryStaticFiles(directory=str(dist), html=True), name="plain-workspace")
    # API 路由在前、静态页面在后；不挂载旧前端或仓库根目录。
    app.mount("/", DeliveryStaticFiles(directory=str(dist), html=True), name="delivery")


def create_delivery_app(plain_api_port=None, plain_map_dir=None):
    if not (FRONTEND_DIST / "index.html").is_file():
        raise RuntimeError("请先在最新版前端目录执行 npm run build -- --mode integration")
    if not os.environ.get("DATABASE_URL", "").strip():
        raise RuntimeError("请显式配置 DATABASE_URL；启动入口不会创建或替换任务数据库")
    # SQLite 指向不存在的文件时提前失败，避免误以为空库任务丢失。
    from sqlalchemy.engine import make_url
    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() == "sqlite":
        database = url.database
        if not database or database == ":memory:" or not Path(database).is_absolute() or not Path(database).is_file():
            raise RuntimeError("交付入口要求 SQLite 使用已存在数据库的绝对路径，请先单独执行迁移")
    sys.path.insert(0, str(ROOT / "algorithm-service"))
    from app.main import app
    from app import resource_dataset_service
    # 交付入口只使用已初始化的数据库资源，旧文件模式不作为页面降级路径。
    os.environ["RESOURCE_DATASET_MODE"] = "database"
    resource_dataset_service.current()
    mount_workspace_static(app, FRONTEND_DIST, plain_api_port, plain_map_dir)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5183, type=int)
    parser.add_argument("--plain-api-port", type=int, help="独立平原后端的 127.0.0.1 端口")
    parser.add_argument("--plain-map-dir", type=Path, help="该平原工作区生成的底图目录")
    args = parser.parse_args()
    if args.plain_api_port is not None and (not 1024 <= args.plain_api_port <= 65535
            or args.plain_api_port == args.port):
        parser.error("平原后端端口须在 1024 至 65535 之间，且与主程序端口不同")
    if (args.plain_api_port is None) != (args.plain_map_dir is None):
        parser.error("--plain-api-port 与 --plain-map-dir 必须一起配置")
    import uvicorn
    uvicorn.run(create_delivery_app(args.plain_api_port, args.plain_map_dir),
                host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
