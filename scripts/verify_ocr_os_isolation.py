"""Observe Linux process isolation for the manual DesignDocument OCR smoke.

This is a read-only procfs observer. It does not create namespaces, configure
firewall rules or mounts, start Uvicorn, execute OCR, or establish attestation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

_FLAGS = {
    "server_process_verified": False,
    "egress_denied_verified": False,
    "runtime_attested": False,
    "quality_verified": False,
    "release_ready": False,
}
_MAX_PROC_FILE = 2 * 1024 * 1024
_MAX_CMDLINE = 64 * 1024
_PID = re.compile(r"[1-9][0-9]{0,8}")
_NAMESPACE = re.compile(r"(?:net|mnt):\[[0-9]+\]")
_HEX8 = re.compile(r"[0-9a-fA-F]{8}")
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_MODEL_PROFILE_ENV = b"POSTER_DESIGN_OCR_MODEL_CONFIG_JSON"
_MODEL_PROFILE_KEYS = {
    "schema_version",
    "text_detection_model_dir",
    "text_recognition_model_dir",
    "text_detection_sha256",
    "text_recognition_sha256",
}


class IsolationError(ValueError):
    """The target server does not meet the observed isolation contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IsolationError(message)


def _read(path: Path, limit: int = _MAX_PROC_FILE) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, "procfs input exceeds limit")
    return raw


def _text(path: Path, limit: int = _MAX_PROC_FILE) -> str:
    try:
        return _read(path, limit).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise IsolationError("invalid procfs text") from None


def _namespace(path: Path, expected: str) -> str:
    try:
        value = os.readlink(path)
    except OSError:
        raise IsolationError("target namespace is unavailable") from None
    _require(
        _NAMESPACE.fullmatch(value) is not None and value.startswith(expected + ":["),
        "invalid process namespace identity",
    )
    return value


def _status_pid(process: Path, pid: int) -> None:
    status = _text(process / "status", 64 * 1024)
    pid_lines = [line for line in status.splitlines() if line.startswith("Pid:")]
    _require(
        len(pid_lines) == 1 and pid_lines[0].split() == ["Pid:", str(pid)],
        "procfs PID identity mismatch",
    )


def _process_start_time(process: Path, pid: int) -> str:
    raw = _text(process / "stat", 64 * 1024).strip()
    closing_paren = raw.rfind(")")
    _require(closing_paren > 0, "invalid process stat record")
    identity = raw[: closing_paren + 1].split(" ", 1)[0]
    fields = raw[closing_paren + 1 :].split()
    _require(
        identity == str(pid)
        and len(fields) > 19
        and len(fields[0]) == 1
        and fields[0] not in {"Z", "X"}
        and fields[19].isdigit()
        and int(fields[19]) > 0,
        "invalid or non-running process start-time identity",
    )
    return fields[19]


def _children_snapshot(process: Path, pid: int) -> dict[str, str]:
    task_root = process / "task"
    tasks = sorted(
        (entry for entry in task_root.iterdir() if entry.is_dir()),
        key=lambda entry: entry.name,
    )
    _require(
        bool(tasks) and any(task.name == str(pid) for task in tasks),
        "server task list is unavailable",
    )
    result: dict[str, str] = {}
    for task in tasks:
        _require(_PID.fullmatch(task.name) is not None, "invalid server task identity")
        children = _text(task / "children", 4096).strip()
        _require(not children, "server process has child processes")
        result[task.name] = children
    return result


def _server_command(raw: bytes) -> dict:
    _require(
        raw.endswith(b"\0") and raw.count(b"\0") >= 2,
        "invalid server command line",
    )
    pieces = raw.split(b"\0")[:-1]
    _require(bool(pieces) and all(pieces), "empty server command argument")
    try:
        tokens = [part.decode("utf-8", errors="strict") for part in pieces]
    except UnicodeError:
        raise IsolationError("invalid server command encoding") from None

    executable = Path(tokens[0]).name
    if executable == "uvicorn":
        _require(
            len(tokens) >= 2 and tokens[1] == "app.main:app",
            "target is not the expected Uvicorn application",
        )
        args = tokens[2:]
    elif executable == "python" or re.fullmatch(r"python3(?:\.[0-9]+)?", executable):
        _require(
            len(tokens) >= 4 and tokens[1:4] == ["-m", "uvicorn", "app.main:app"],
            "target is not the expected Python Uvicorn application",
        )
        args = tokens[4:]
    else:
        raise IsolationError("target is not a direct Uvicorn or Python process")

    def option_values(name: str) -> list[str]:
        values: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token == name:
                _require(index + 1 < len(args), "incomplete server option")
                values.append(args[index + 1])
                index += 2
            elif token.startswith(name + "="):
                values.append(token.partition("=")[2])
                index += 1
            else:
                index += 1
        return values

    _require(option_values("--host") == ["127.0.0.1"], "server must bind IPv4 loopback")
    _require(option_values("--workers") == ["1"], "server must explicitly use one worker")
    forbidden = (
        "--reload",
        "--reload-dir",
        "--reload-include",
        "--reload-exclude",
        "--fd",
        "--uds",
        "--factory",
    )
    _require(
        not any(
            token == option or token.startswith(option + "=")
            for token in args
            for option in forbidden
        ),
        "server command enables an unsupported process or socket mode",
    )
    return {
        "single_worker_command_observed": True,
        "loopback_bind_observed": True,
    }


def _unique_model_profile(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate model profile key")
        result[key] = value
    return result


def _reject_model_profile_constant(value: str) -> None:
    raise IsolationError("non-finite model profile value")


def _model_profile(environment: bytes, detection_path: str, recognition_path: str) -> dict:
    _require(environment.endswith(b"\0"), "invalid process environment")
    entries = environment.split(b"\0")[:-1]
    values = [
        entry.partition(b"=")[2] for entry in entries if entry.startswith(_MODEL_PROFILE_ENV + b"=")
    ]
    _require(
        len(values) == 1 and len(values[0]) <= 4096,
        "model profile environment must be unique and bounded",
    )
    try:
        profile = json.loads(
            values[0].decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_model_profile,
            parse_constant=_reject_model_profile_constant,
        )
    except IsolationError:
        raise
    except (UnicodeError, ValueError, TypeError):
        raise IsolationError("invalid model profile environment") from None
    _require(
        type(profile) is dict
        and set(profile) == _MODEL_PROFILE_KEYS
        and type(profile["schema_version"]) is int
        and profile["schema_version"] == 1,
        "unsupported model profile schema",
    )
    configured_detection = _canonical_model_path(profile["text_detection_model_dir"])
    configured_recognition = _canonical_model_path(profile["text_recognition_model_dir"])
    _require(
        configured_detection == detection_path and configured_recognition == recognition_path,
        "model paths do not match the server environment",
    )
    detection_sha = profile["text_detection_sha256"]
    recognition_sha = profile["text_recognition_sha256"]
    _require(
        isinstance(detection_sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", detection_sha) is not None
        and isinstance(recognition_sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", recognition_sha) is not None,
        "invalid model profile digest",
    )
    canonical = json.dumps(
        profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {
        "model_profile_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "detection_model_sha256": detection_sha,
        "recognition_model_sha256": recognition_sha,
    }


def _process_snapshot(process: Path, pid: int) -> dict:
    _status_pid(process, pid)
    start_before = _process_start_time(process, pid)
    command_line = _read(process / "cmdline", _MAX_CMDLINE)
    environment = _read(process / "environ", _MAX_PROC_FILE)
    command = _server_command(command_line)
    children = _children_snapshot(process, pid)
    start_after = _process_start_time(process, pid)
    _require(start_before == start_after, "server process identity changed during observation")
    return {
        "command_line": command_line,
        "environment": environment,
        "children": children,
        "start_time": start_before,
        "command": command,
    }


def _interfaces(raw: str) -> list[str]:
    lines = raw.splitlines()
    _require(len(lines) >= 3, "invalid network interface table")
    names: list[str] = []
    for line in lines[2:]:
        if not line.strip():
            continue
        _require(":" in line, "invalid network interface row")
        name, counters = line.rsplit(":", 1)
        interface = name.strip()
        values = counters.split()
        _require(
            bool(interface) and len(values) >= 16 and all(value.isdigit() for value in values),
            "invalid network interface counters",
        )
        names.append(interface)
    _require(names == ["lo"], "network namespace has a non-loopback interface")
    return names


def _ipv4_routes(raw: str) -> list[str]:
    lines = raw.splitlines()
    _require(bool(lines) and lines[0].startswith("Iface "), "missing IPv4 route table header")
    interfaces: list[str] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        _require(len(fields) == 11, "invalid IPv4 route row")
        interface, destination = fields[0], fields[1]
        _require(
            _HEX8.fullmatch(destination) is not None
            and _HEX8.fullmatch(fields[2]) is not None
            and re.fullmatch(r"[0-9a-fA-F]{1,8}", fields[3]) is not None
            and all(value.isdigit() for value in fields[4:7])
            and _HEX8.fullmatch(fields[7]) is not None
            and all(value.isdigit() for value in fields[8:]),
            "invalid IPv4 route fields",
        )
        _require(interface == "lo", "IPv4 route uses a non-loopback interface")
        _require(destination.lower() != "00000000", "IPv4 default route is present")
        interfaces.append(interface)
    return sorted(set(interfaces))


def _ipv6_routes(raw: str) -> list[str]:
    interfaces: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        _require(len(fields) == 10, "invalid IPv6 route row")
        destination, prefix, interface = fields[0], fields[1], fields[-1]
        _require(
            _HEX32.fullmatch(destination) is not None
            and re.fullmatch(r"[0-9a-fA-F]{2}", prefix) is not None
            and _HEX32.fullmatch(fields[2]) is not None
            and re.fullmatch(r"[0-9a-fA-F]{2}", fields[3]) is not None
            and _HEX32.fullmatch(fields[4]) is not None
            and all(_HEX8.fullmatch(value) is not None for value in fields[5:9]),
            "invalid IPv6 route fields",
        )
        _require(
            int(prefix, 16) <= 128 and int(fields[3], 16) <= 128,
            "invalid IPv6 route prefix length",
        )
        is_default = destination.lower() == "0" * 32 and int(prefix, 16) == 0
        # Linux exposes an unreachable null route in /proc/net/ipv6_route.
        # Accept only the exact reject/non-nexthop sentinel, not a usable default.
        is_kernel_reject = (
            is_default
            and interface == "lo"
            and fields[2].lower() == "0" * 32
            and int(fields[3], 16) == 0
            and fields[4].lower() == "0" * 32
            and int(fields[5], 16) == 0xFFFFFFFF
            and int(fields[8], 16) == 0x00200200
        )
        _require(
            interface == "lo" and (not is_default or is_kernel_reject),
            "non-loopback or default IPv6 route",
        )
        interfaces.append(interface)
    return sorted(set(interfaces))


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _canonical_model_path(value: str) -> str:
    _require(
        isinstance(value, str)
        and "\0" not in value
        and "\\" not in value
        and not value.startswith("//")
        and not value.endswith("/"),
        "model directory must be a canonical absolute path",
    )
    parts = value.split("/")
    _require(
        value.startswith("/")
        and value != "/"
        and all(part and part not in (".", "..") for part in parts[1:]),
        "model directory must be a canonical absolute path",
    )
    path = PurePosixPath(value)
    _require(path.is_absolute() and path.as_posix() == value, "invalid model directory")
    return value


def _mount_for(model_path: str, mountinfo: str) -> dict:
    mounts: list[tuple[PurePosixPath, set[str]]] = []
    for line in mountinfo.splitlines():
        if not line.strip():
            continue
        before, separator, _ = line.partition(" - ")
        fields = before.split()
        _require(bool(separator) and len(fields) >= 6, "invalid mountinfo row")
        mountpoint_text = _unescape_mount(fields[4])
        mountpoint = PurePosixPath(mountpoint_text)
        _require(
            mountpoint.is_absolute() and mountpoint.as_posix() == mountpoint_text,
            "invalid mountpoint path",
        )
        mounts.append((mountpoint, set(fields[5].split(","))))

    target = PurePosixPath(model_path)
    covering = [
        (mountpoint, options)
        for mountpoint, options in mounts
        if target == mountpoint or target.is_relative_to(mountpoint)
    ]
    _require(bool(covering), "model directory has no covering mount")
    depth = max(len(mountpoint.parts) for mountpoint, _ in covering)
    nearest = [
        (mountpoint, options) for mountpoint, options in covering if len(mountpoint.parts) == depth
    ]
    _require(len(nearest) == 1, "ambiguous covering model mount")
    mountpoint, options = nearest[0]
    _require("ro" in options and "rw" not in options, "model mount is not read-only")

    nested = [
        (path, mount_options)
        for path, mount_options in mounts
        if path == target or path.is_relative_to(target)
    ]
    _require(
        all("ro" in mount_options and "rw" not in mount_options for _, mount_options in nested),
        "model directory contains a writable nested mount",
    )
    return {
        "mountpoint": mountpoint.as_posix(),
        "read_only_mounts": sorted({path.as_posix() for path, _ in nested}),
        "read_only": True,
    }


def _model_directory(process: Path, value: str) -> str:
    model_path = _canonical_model_path(value)
    process_root = (process / "root").resolve(strict=True)
    candidate = (process / "root" / model_path.lstrip("/")).resolve(strict=True)
    expected = process_root / model_path.lstrip("/")
    _require(candidate == expected, "model directory path traverses a symlink")
    _require(stat.S_ISDIR(candidate.stat().st_mode), "model path is not a directory")
    return model_path


def inspect_isolation(
    proc_root: Path,
    server_pid: int,
    *,
    detection_model_dir: str,
    recognition_model_dir: str,
) -> dict:
    """Inspect one server process's namespaces, interfaces, routes and model mounts."""
    try:
        _require(type(server_pid) is int and 1 < server_pid <= 999_999_999, "invalid server PID")
        _require(proc_root.is_absolute(), "procfs root must be absolute")
        detection_path = _canonical_model_path(detection_model_dir)
        recognition_path = _canonical_model_path(recognition_model_dir)
        detection = PurePosixPath(detection_path)
        recognition = PurePosixPath(recognition_path)
        _require(
            detection != recognition
            and detection not in recognition.parents
            and recognition not in detection.parents,
            "detection and recognition model directories must be separate",
        )

        process = proc_root / str(server_pid)
        network_namespace = _namespace(process / "ns" / "net", "net")
        mount_namespace = _namespace(process / "ns" / "mnt", "mnt")
        process_before = _process_snapshot(process, server_pid)
        model_profile = _model_profile(
            process_before["environment"], detection_path, recognition_path
        )

        interface_table = _text(process / "net" / "dev")
        interfaces = _interfaces(interface_table)
        ipv4_table = _text(process / "net" / "route")
        ipv4_interfaces = _ipv4_routes(ipv4_table)
        ipv6_table = _text(process / "net" / "ipv6_route")
        ipv6_interfaces = _ipv6_routes(ipv6_table)
        mountinfo = _text(process / "mountinfo")
        model_mounts = []
        for role, value in (
            ("detection", detection_path),
            ("recognition", recognition_path),
        ):
            model_directory = _model_directory(process, value)
            model_mounts.append(
                {
                    "role": role,
                    "path": model_directory,
                    **_mount_for(model_directory, mountinfo),
                }
            )

        _require(
            _namespace(process / "ns" / "net", "net") == network_namespace,
            "network namespace changed during observation",
        )
        _require(
            _namespace(process / "ns" / "mnt", "mnt") == mount_namespace,
            "mount namespace changed during observation",
        )
        _require(
            process_before == _process_snapshot(process, server_pid),
            "server process changed during observation",
        )
        _require(
            interfaces == _interfaces(_text(process / "net" / "dev")),
            "network interfaces changed during observation",
        )
        _require(
            ipv4_table == _text(process / "net" / "route"), "IPv4 routes changed during observation"
        )
        _require(
            ipv6_table == _text(process / "net" / "ipv6_route"),
            "IPv6 routes changed during observation",
        )
        _require(
            mountinfo == _text(process / "mountinfo"), "mount table changed during observation"
        )
    except IsolationError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        raise IsolationError("target process isolation could not be observed") from None

    return {
        "schema_version": 1,
        "status": "LOCAL_OS_ISOLATION_OBSERVED_UNATTESTED",
        "server_pid": server_pid,
        "server_start_ticks": int(process_before["start_time"]),
        "network_namespace": network_namespace,
        "mount_namespace": mount_namespace,
        "network_interfaces": interfaces,
        "ipv4_route_interfaces": ipv4_interfaces,
        "ipv6_route_interfaces": ipv6_interfaces,
        "egress_policy_observed": "LOOPBACK_ONLY_NO_DEFAULT_ROUTE",
        **model_profile,
        "model_mounts": model_mounts,
        "model_mounts_read_only_observed": True,
        **process_before["command"],
        **_FLAGS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--detection-model-dir", required=True)
    parser.add_argument("--recognition-model-dir", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve(strict=True)
        _require(args.output.is_absolute(), "output path must be absolute")
        _require(not args.output.is_symlink() and not args.output.exists(), "output already exists")
        output = args.output.parent.resolve(strict=True) / args.output.name
        _require(not output.exists(), "output already exists")
        _require(not output.is_relative_to(repo), "output must be outside the repository")
        report = inspect_isolation(
            Path("/proc"),
            args.server_pid,
            detection_model_dir=args.detection_model_dir,
            recognition_model_dir=args.recognition_model_dir,
        )
        raw = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(output, flags, 0o600), "w", encoding="utf-8") as stream:
            stream.write(raw + "\n")
    except (IsolationError, OSError, ValueError, TypeError, RecursionError):
        parser.exit(2, "OCR OS isolation observation refused; inspect private operator setup.\n")
    print("OS isolation observation recorded; independent attestation remains false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
