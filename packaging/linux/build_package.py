"""Assemble the verified application for an offline CPython 3.12 Linux host."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "windows"))
from build_frontend_release import copy_checked, digest, snapshot_hash, validate_base


def build(base: Path, expected: str, output: Path) -> dict:
    if digest(base) != expected:
        raise ValueError("应用基包 SHA-256 校验失败")
    if output.exists() or output.suffix != ".zip":
        raise ValueError("请指定不存在的 ZIP 文件")
    additions = {name: (HERE / name).read_text(encoding="utf-8").encode("utf-8")
                 for name in ("install-offline.sh", "radio-planning.sh")}
    additions["使用说明.txt"] = (HERE / "README.md").read_bytes()
    root = output.stem + "/"
    with ZipFile(base) as source:
        _, original, entries = validate_base(source)
        retained = {name: sha for name, sha in original["files"].items()
                    if not name.startswith("runtime/") and name not in ("启动系统.cmd", "停止系统.cmd", "使用说明.txt")}
        if not {"launcher.py", "app/algorithm-service/requirements.txt"} <= retained.keys():
            raise ValueError("应用基包未提供离线启动器或依赖清单")
        files = {**retained, **{name: hashlib.sha256(raw).hexdigest() for name, raw in additions.items()}}
        checksum = snapshot_hash(files)
        manifest = {**original, "platform": "Linux", "python": {"requires": "CPython 3.12"},
                    "dependencies": "TARGET_OFFLINE_WHEELHOUSE",
                    "controlled_version": original["controlled_version"].rsplit("-", 1)[0] + "-" + checksum[:12],
                    "content_snapshot_sha256": checksum, "files": files}
        pending = output.with_suffix(".pending.zip")
        if pending.exists():
            raise ValueError("已有未完成的打包文件")
        try:
            with ZipFile(pending, "x", compression=ZIP_DEFLATED) as archive:
                for name, sha in sorted(retained.items()):
                    info = copy.copy(entries[name])
                    info.filename = root + name
                    with source.open(entries[name]) as reader, archive.open(info, "w", force_zip64=True) as writer:
                        copy_checked(reader, writer, sha)
                for name, raw in additions.items():
                    info = ZipInfo(root + name)
                    info.create_system = 3
                    info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
                    archive.writestr(info, raw)
                archive.writestr(root + "package-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            pending.rename(output)
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    sha = digest(output)
    output.with_suffix(".zip.sha256").write_text(f"{sha}  {output.name}\n", encoding="ascii")
    return {"path": output.as_posix(), "version": manifest["controlled_version"], "sha256": sha}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("sha256")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.base.resolve(), args.sha256, args.output.resolve()), ensure_ascii=False))
