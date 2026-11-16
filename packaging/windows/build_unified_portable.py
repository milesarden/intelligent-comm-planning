"""Build one dual-scene portable package from two verified scene archives."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

from build_frontend_release import (
    DIST, canonical, digest, folded, reject_private, snapshot_hash, validate_base,
)


TEMPLATES = Path(__file__).resolve().parent
EXAMPLES = "系统使用例程/"
PLAIN = "scenes/plain/"
SHELL_FILES = ("launcher.py", "unified_runtime.py")

GUIDE = """无线网络智能规划系统便携使用说明

运行“启动系统.cmd”，用窗口打开的地址进入系统。从页面“地形场景”选择丘陵或平原。
两种场景使用同一应用入口和端口。选择场景后，设备、规划、覆盖及故障诊断使用所选场景的数据。
如果离开当前页面前有未处理的编辑，按页面提示保存、放弃或取消切换。

对应例程位于“系统使用例程”的“丘陵”和“平原”文件夹。请先阅读所选场景的《使用例程说明》。
集成访问沿用原无线模块路由。平原场景也可在部署地址后使用 /scenes/plain/ 再接同一模块路由。

运行“停止系统.cmd”关闭两种场景。计算或提交进行中时，待完成或在页面取消后再退出。
"""


def frontend_members(directory):
    directory = directory.resolve(strict=True)
    index = directory / "index.html"
    assets = directory / "assets"
    if not index.is_file() or not assets.is_dir():
        raise ValueError("编译目录缺少入口或资产")
    yield DIST + "index.html", index
    for path in sorted(assets.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError("编译目录包含链接")
        if path.is_file():
            yield DIST + path.relative_to(directory).as_posix(), path


def build(hills, hills_sha, plain, plain_sha, hills_dist, plain_dist, output):
    if output.exists() or output.suffix.lower() != ".zip":
        raise ValueError("请选择一个尚不存在的 ZIP 输出路径")
    for file, expected in ((hills, hills_sha), (plain, plain_sha)):
        if digest(file) != expected.lower():
            raise ValueError(f"基包摘要不符：{file.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        archives = [stack.enter_context(ZipFile(path)) for path in (hills, plain)]
        (_, hills_manifest, hills_entries), (_, plain_manifest, plain_entries) = (
            validate_base(archive) for archive in archives
        )
        if plain_manifest.get("terrain_scene") != "plain" or hills_manifest.get("terrain_scene") == "plain":
            raise ValueError("丘陵或平原基包类型不符")
        sources = {}
        hashes = {}

        def register(name, source, sha):
            canonical(name)
            reject_private(name.removeprefix(PLAIN))
            key = folded(name)
            if key in sources:
                raise ValueError(f"合包文件名重复：{name}")
            sources[key] = (name, source)
            hashes[name] = sha

        maps_and_fonts = (DIST + "maps/", DIST + "fonts/")
        shared = sorted(name for name, sha in plain_manifest["files"].items()
                        if name.startswith(maps_and_fonts) and hills_manifest["files"].get(name) == sha)
        shared_paths = set(shared)
        for scene, archive, prefix, manifest, entries, dist, folder in (
            ("hills", archives[0], "", hills_manifest, hills_entries, hills_dist, "丘陵"),
            ("plain", archives[1], PLAIN, plain_manifest, plain_entries, plain_dist, "平原"),
        ):
            for name, info in entries.items():
                if name.startswith(EXAMPLES):
                    destination = EXAMPLES + folder + "/" + name.removeprefix(EXAMPLES)
                elif name == DIST + "index.html" or name.startswith(DIST + "assets/"):
                    continue
                elif scene == "plain":
                    if not name.startswith("app/") or name in shared_paths:
                        continue
                    destination = prefix + name
                else:
                    if name == "使用说明.txt" or name in SHELL_FILES:
                        continue
                    destination = name
                register(destination, (archive, info), manifest["files"][name])
            for name, path in frontend_members(dist):
                register(prefix + name, path, digest(path))

        for filename in SHELL_FILES:
            source = TEMPLATES / filename
            register(filename, source, digest(source))
        guide = GUIDE.encode("utf-8-sig")
        register("使用说明.txt", guide, hashlib.sha256(guide).hexdigest())
        content = snapshot_hash(hashes)
        date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
        manifest = {
            **hills_manifest,
            "terrain_scene": "unified",
            "plain_shared_static": [name.removeprefix(DIST) for name in shared],
            "controlled_version": f"LOCAL-{date}-{content[:12]}",
            "content_snapshot_sha256": content,
            "first_use_examples": {
                "entry": "使用说明.txt",
                "files": sorted(name for name in hashes if name.startswith(EXAMPLES)),
                "policy": "IMPORT_AND_COMPUTE_ON_FRESH_DATABASE",
            },
            "files": hashes,
        }
        root = output.stem + "/"
        temporary = output.with_suffix(".zip.pending")
        with ZipFile(temporary, "x", compression=ZIP_DEFLATED, compresslevel=5) as target:
            for name, source in sorted(sources.values()):
                if isinstance(source, tuple):
                    archive, info = source
                    with archive.open(info) as reader, target.open(root + name, "w") as writer:
                        shutil.copyfileobj(reader, writer)
                elif isinstance(source, Path):
                    target.write(source, root + name)
                else:
                    target.writestr(root + name, source)
            target.writestr(root + "package-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        with ZipFile(temporary) as checked:
            validate_base(checked)
        temporary.rename(output)
    receipt = {"file": str(output), "version": manifest["controlled_version"],
               "sha256": digest(output), "bytes": output.stat().st_size, "shared_static": len(shared)}
    output.with_suffix(".zip.sha256").write_text(f"{receipt['sha256']}  {output.name}\n", encoding="ascii")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("hills", "plain", "hills-dist", "plain-dist", "output"):
        parser.add_argument(f"--{option}", type=Path, required=True)
    for option in ("hills-sha", "plain-sha"):
        parser.add_argument(f"--{option}", required=True)
    build(**vars(parser.parse_args()))
