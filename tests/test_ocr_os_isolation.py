"""Tests for read-only Linux OCR server isolation observation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_ocr_os_isolation import IsolationError, inspect_isolation

_PID = 4242
_CMDLINE = (
    b"\0".join(
        (
            b"python",
            b"-m",
            b"uvicorn",
            b"app.main:app",
            b"--host",
            b"127.0.0.1",
            b"--port",
            b"8000",
            b"--workers",
            b"1",
        )
    )
    + b"\0"
)
_NET_DEV = (
    "Inter-| Receive | Transmit\n"
    " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
    "lo: 1024 10 0 0 0 0 0 0 1024 10 0 0 0 0 0 0\n"
)
_IPV4_ROUTES = (
    "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
    "lo 0000007F 00000000 00000003 0 0 0 000000FF 0 0 0\n"
)
_IPV6_ROUTES = (
    "00000000000000000000000000000001 80 "
    "00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 00000000 00000000 00000000 00000001 lo\n"
)
_MOUNTINFO = (
    "31 25 0:28 / /models/det ro,nosuid,nodev - ext4 /dev/loop0 ro\n"
    "32 25 0:29 / /models/rec ro,nosuid,nodev - ext4 /dev/loop1 ro\n"
)


def _proc_fixture(
    tmp_path: Path,
    *,
    cmdline: bytes = _CMDLINE,
    net_dev: str = _NET_DEV,
    ipv4_routes: str = _IPV4_ROUTES,
    ipv6_routes: str = _IPV6_ROUTES,
    mountinfo: str = _MOUNTINFO,
    children: str = "",
) -> Path:
    process = tmp_path / "proc" / str(_PID)
    server_root = tmp_path / "server-root"
    (server_root / "models" / "det").mkdir(parents=True)
    (server_root / "models" / "rec").mkdir(parents=True)
    (process / "ns").mkdir(parents=True)
    (process / "root").symlink_to(server_root, target_is_directory=True)
    (process / "ns" / "net").symlink_to("net:[4026533001]")
    (process / "ns" / "mnt").symlink_to("mnt:[4026533002]")
    (process / "task" / str(_PID)).mkdir(parents=True)
    (process / "task" / str(_PID) / "children").write_text(children, encoding="ascii")
    (process / "cmdline").write_bytes(cmdline)
    (process / "status").write_text(f"Name:\tpython\nPid:\t{_PID}\n", encoding="ascii")
    (process / "mountinfo").write_text(mountinfo, encoding="utf-8")
    (process / "net").mkdir()
    (process / "net" / "dev").write_text(net_dev, encoding="utf-8")
    (process / "net" / "route").write_text(ipv4_routes, encoding="utf-8")
    (process / "net" / "ipv6_route").write_text(ipv6_routes, encoding="utf-8")
    return tmp_path / "proc"


def _inspect(proc_root: Path) -> dict:
    return inspect_isolation(
        proc_root,
        _PID,
        detection_model_dir="/models/det",
        recognition_model_dir="/models/rec",
    )


def test_reports_loopback_only_and_read_only_model_mounts_without_authority(tmp_path):
    proc_root = _proc_fixture(tmp_path)

    report = _inspect(proc_root)

    assert report["status"] == "LOCAL_OS_ISOLATION_OBSERVED_UNATTESTED"
    assert report["network_interfaces"] == ["lo"]
    assert report["ipv4_route_interfaces"] == ["lo"]
    assert report["ipv6_route_interfaces"] == ["lo"]
    assert report["egress_policy_observed"] == "LOOPBACK_ONLY_NO_DEFAULT_ROUTE"
    assert report["model_mounts_read_only_observed"] is True
    assert [item["role"] for item in report["model_mounts"]] == [
        "detection",
        "recognition",
    ]
    assert all(item["read_only"] is True for item in report["model_mounts"])
    assert report["single_worker_command_observed"] is True
    assert report["loopback_bind_observed"] is True
    assert report["server_process_verified"] is False
    assert report["egress_denied_verified"] is False
    assert report["runtime_attested"] is False
    assert report["quality_verified"] is False
    assert report["release_ready"] is False


def test_rejects_non_loopback_network_interface(tmp_path):
    net_dev = _NET_DEV + "eth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    proc_root = _proc_fixture(tmp_path, net_dev=net_dev)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_ipv4_route_through_non_loopback_interface(tmp_path):
    routes = _IPV4_ROUTES + ("eth0 00000000 0100000A 0003 0 0 100 00000000 0 0 0\n")
    proc_root = _proc_fixture(tmp_path, ipv4_routes=routes)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_ipv4_default_route_even_when_bound_to_loopback(tmp_path):
    routes = _IPV4_ROUTES + ("lo 00000000 00000000 0001 0 0 0 00000000 0 0 0\n")
    proc_root = _proc_fixture(tmp_path, ipv4_routes=routes)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_ipv6_route_through_non_loopback_interface(tmp_path):
    routes = _IPV6_ROUTES + (
        "20010db8000000000000000000000000 40 "
        "00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 00000000 00000000 00000000 00000001 eth0\n"
    )
    proc_root = _proc_fixture(tmp_path, ipv6_routes=routes)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_ipv6_default_route(tmp_path):
    routes = _IPV6_ROUTES + (
        "00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 00000000 00000000 00000000 00000001 lo\n"
    )
    proc_root = _proc_fixture(tmp_path, ipv6_routes=routes)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_writable_detection_model_mount(tmp_path):
    mountinfo = _MOUNTINFO.replace("/models/det ro,nosuid,nodev", "/models/det rw,nosuid,nodev")
    proc_root = _proc_fixture(tmp_path, mountinfo=mountinfo)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_model_directory_without_a_covering_mount(tmp_path):
    proc_root = _proc_fixture(tmp_path, mountinfo=_MOUNTINFO.splitlines()[0] + "\n")

    with pytest.raises(IsolationError):
        _inspect(proc_root)


@pytest.mark.parametrize(
    ("option", "value"),
    [("--host", "0.0.0.0"), ("--workers", "2")],
)
def test_rejects_non_loopback_or_multiworker_server_command(tmp_path, option, value):
    tokens = _CMDLINE.rstrip(b"\0").split(b"\0")
    index = tokens.index(option.encode()) + 1
    tokens[index] = value.encode()
    proc_root = _proc_fixture(tmp_path, cmdline=b"\0".join(tokens) + b"\0")

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_server_without_explicit_single_worker_flag(tmp_path):
    tokens = [
        token
        for token in _CMDLINE.rstrip(b"\0").split(b"\0")
        if token != b"--workers" and token != b"1"
    ]
    proc_root = _proc_fixture(tmp_path, cmdline=b"\0".join(tokens) + b"\0")

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_server_with_child_processes(tmp_path):
    proc_root = _proc_fixture(tmp_path, children="4243")

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_duplicate_or_noncanonical_model_paths(tmp_path):
    proc_root = _proc_fixture(tmp_path)

    with pytest.raises(IsolationError):
        inspect_isolation(
            proc_root,
            _PID,
            detection_model_dir="/models/det",
            recognition_model_dir="/models/det",
        )
    with pytest.raises(IsolationError):
        inspect_isolation(
            proc_root,
            _PID,
            detection_model_dir="/models/../models/det",
            recognition_model_dir="/models/rec",
        )


def test_rejects_writable_nested_mount_inside_model_directory(tmp_path):
    mountinfo = _MOUNTINFO + (
        "33 25 0:30 / /models/det/weights rw,nosuid,nodev - ext4 /dev/loop2 rw\n"
    )
    proc_root = _proc_fixture(tmp_path, mountinfo=mountinfo)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_process_status_with_mismatched_pid(tmp_path):
    proc_root = _proc_fixture(tmp_path)
    status = proc_root / str(_PID) / "status"
    status.write_text("Name:\tpython\nPid:\t4243\n", encoding="ascii")

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_children_spawned_by_a_non_leader_thread(tmp_path):
    proc_root = _proc_fixture(tmp_path)
    thread = proc_root / str(_PID) / "task" / "4243"
    thread.mkdir()
    (thread / "children").write_text("4244", encoding="ascii")

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_malformed_ipv4_route_rows(tmp_path):
    routes = _IPV4_ROUTES + "lo not-a-destination 00000000 0003 0 0 0 00000000 0 0 0\n"
    proc_root = _proc_fixture(tmp_path, ipv4_routes=routes)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_malformed_ipv4_route_address(tmp_path):
    routes = _IPV4_ROUTES + "lo not-hex 00000000 0003 0 0 0 000000FF 0 0 0\n"
    proc_root = _proc_fixture(tmp_path, ipv4_routes=routes)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_malformed_ipv6_route_address(tmp_path):
    routes = _IPV6_ROUTES + (
        "not-hex 80 00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 00000000 00000000 00000000 00000001 lo\n"
    )
    proc_root = _proc_fixture(tmp_path, ipv6_routes=routes)

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_symlinked_model_directory(tmp_path):
    proc_root = _proc_fixture(tmp_path)
    process = proc_root / str(_PID)
    model_dir = process / "root" / "models" / "det"
    model_dir.rmdir()
    (process / "root" / "models" / "alternate-det").mkdir()
    model_dir.symlink_to(
        process / "root" / "models" / "alternate-det",
        target_is_directory=True,
    )

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_nested_detection_and_recognition_model_directories(tmp_path):
    proc_root = _proc_fixture(tmp_path)

    with pytest.raises(IsolationError):
        inspect_isolation(
            proc_root,
            _PID,
            detection_model_dir="/models",
            recognition_model_dir="/models/rec",
        )


def test_rejects_uvicorn_name_embedded_in_unrelated_command(tmp_path):
    tokens = (
        b"python",
        b"-c",
        b"print('uvicorn')",
        b"uvicorn",
        b"app.main:app",
        b"--host",
        b"127.0.0.1",
        b"--port",
        b"8000",
        b"--workers",
        b"1",
    )
    proc_root = _proc_fixture(tmp_path, cmdline=b"\0".join(tokens) + b"\0")

    with pytest.raises(IsolationError):
        _inspect(proc_root)


def test_rejects_uvicorn_reload_mode(tmp_path):
    tokens = _CMDLINE.rstrip(b"\0").split(b"\0") + [b"--reload"]
    proc_root = _proc_fixture(tmp_path, cmdline=b"\0".join(tokens) + b"\0")

    with pytest.raises(IsolationError):
        _inspect(proc_root)
