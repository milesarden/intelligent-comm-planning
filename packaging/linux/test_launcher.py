"""Exercise the Linux launcher branches without modifying a running instance."""
import importlib.util
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def launcher():
    source = Path(__file__).resolve().parents[1] / "windows/launcher.py"
    spec = importlib.util.spec_from_file_location("portable_linux_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linux_start_detaches_child_and_retains_runtime(launcher, tmp_path, monkeypatch):
    root = tmp_path / "package"
    data = root / "app/data/runtime"
    data.mkdir(parents=True)
    (data / "r2.sqlite").write_bytes(b"existing-data")
    monkeypatch.setattr(launcher, "ROOT", root)
    monkeypatch.setattr(launcher, "DATA", data)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(launcher, "check_frontend_assets", Mock())
    monkeypatch.setattr(launcher, "secure_data_directory", Mock())
    ready = {"port": 18480, "pid": 1234}
    monkeypatch.setattr(launcher, "available_state", Mock(side_effect=[None, ready]))
    spawn = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)

    assert launcher.start(open_browser=False, timeout=2) == ready
    assert spawn.call_args.args[0][-2:] == [str(root / "launcher.py"), "serve"]
    assert spawn.call_args.kwargs["start_new_session"] is True
    assert "creationflags" not in spawn.call_args.kwargs
    assert (data / "访问地址.txt").read_text(encoding="utf-8").startswith("http://127.0.0.1:18480/")
    assert (data / "r2.sqlite").read_bytes() == b"existing-data"


def test_linux_lock_distinguishes_already_running_from_lock_errors(launcher, monkeypatch):
    blocked = BlockingIOError("owned by running instance")
    failed = OSError("file system cannot lock")
    flock = Mock(side_effect=[None, blocked, failed])
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix"))
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=flock))
    lock = SimpleNamespace(fileno=lambda: 17)

    assert launcher.acquire_service_lock(lock)
    assert not launcher.acquire_service_lock(lock)
    with pytest.raises(OSError, match="file system cannot lock"):
        launcher.acquire_service_lock(lock)
    assert all(call.args == (17, 6) for call in flock.call_args_list)


def test_linux_listener_stays_loopback_and_skips_occupied_port(launcher, monkeypatch):
    occupied, free = Mock(), Mock()
    occupied.bind.side_effect = OSError("in use")
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(launcher.socket, "socket", Mock(side_effect=[occupied, free]))

    assert launcher.bind_listener(18480) == (free, 18481)
    assert occupied.bind.call_args.args == (("127.0.0.1", 18480),)
    occupied.close.assert_called_once_with()
    assert free.bind.call_args.args == (("127.0.0.1", 18481),)
    free.listen.assert_called_once_with(128)


def test_linux_cli_sets_owner_only_mask(launcher, monkeypatch):
    activate = Mock()
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix", umask=activate))
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(sys, "argv", ["launcher.py", "serve"])
    serve = Mock()
    monkeypatch.setattr(launcher, "serve", serve)

    assert launcher.main() == 0
    activate.assert_called_once_with(0o077)
    serve.assert_called_once_with()


def test_posix_directory_refuses_other_owners_and_symlinks(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "os", SimpleNamespace(geteuid=lambda: 1000))
    for mode, owner in [(stat.S_IFLNK, 1000), (stat.S_IFDIR, 1001)]:
        directory = Mock()
        directory.lstat.return_value = SimpleNamespace(st_mode=mode, st_uid=owner)
        with pytest.raises(PermissionError):
            launcher.secure_posix_data_directory(directory)
        directory.chmod.assert_not_called()


@pytest.mark.skipif(sys.platform != "linux", reason="需要原生 Linux 文件权限和 fcntl")
def test_linux_real_runtime_directory_and_lock(launcher, tmp_path):
    data = tmp_path / "app/data/runtime"
    launcher.secure_posix_data_directory(data)
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    assert data.stat().st_uid == os.geteuid()
    path = data / "service.lock"
    with path.open("ab") as owner, path.open("ab") as other:
        assert launcher.acquire_service_lock(owner)
        assert not launcher.acquire_service_lock(other)
    with path.open("ab") as next_owner:
        assert launcher.acquire_service_lock(next_owner)
