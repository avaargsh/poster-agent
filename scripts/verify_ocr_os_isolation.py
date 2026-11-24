"""Observe Linux process isolation for the manual DesignDocument OCR smoke.

This tool only reads the target process's procfs state. It never changes
network or mount configuration, starts a server, or invokes an OCR model.
Its output is local, mutable evidence and is never independent attestation.
"""

from __future__ import annotations

import argparse
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
        return _read(path, limit).decode("utf-8")
    except UnicodeDecodeError:
        raise IsolationError("invalid procfs text") from None


def _namespace(path: Path, expected: str) -> str:
    try:
        value = os.readlink(path)
    except OSError:
        raise IsolationError("target namespace is unavailable") from None
    _require(_NAMESPACE.fullmatch(value) is not None, "invalid namespace identity")
    _require(value.startswith(expected + ":["), "unexpected namespace type")
    return value


def _server_command(process: Path) -> dict:
    raw = _read(process / "cmdline", _MAX_CMDLINE)
    _require(raw.endswith(b"\0") and raw.count(b"\0") >= 2, "invalid server argv")
    try:
        tokens = [part.decode("utf-8") for part in raw.split(b"\0")[:-1]]
    except UnicodeDecodeError:
        raise IsolationError("invalid server argv encoding") from None
    _require(
        any(Path(token).name == "uvicorn" for token in tokens)
        and tokens.count("app.main:app") == 1,
        "target is not the expected Uvicorn application",
    )

    def option(name: str) -> str:
        values: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == name:
                _require(index + 1 < len(tokens), "incomplete server option")
                values.append(tokens[index + 1])
                index += 2
            elif token.startswith(name + "="):
                values.append(token.split("=", 1)[1])
                index += 1
            else:
                index += 1
        _require(len(values) == 1, "server option must be explicit and unique")
        return values[0]

    _require(option("--host") == "127.0.0.1", "server must bind IPv4 loopback")
    _require(option("--workers") == "1", "server must explicitly use one worker")
    def require_no_children() -> None:
        task_root = process / "task"
        tasks = sorted(
            (entry for entry in task_root.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name,
        )
        _require(bool(tasks) and any(task.name == process.name for task in tasks), "missing server task")
        for task in tasks:
            _require(_PID.fullmatch(task.name) is not None, "invalid task identity")
            _require(
                not _text(task / "children", 4096).strip(),
                "server process has child processes",
            )

    require_no_children()
    status = _text(process / "status", 64 * 1024)
    pid_lines = [line for line in status.splitlines() if line.startswith("Pid:")]
    _require(
        len(pid_lines) == 1 and pid_lines[0].split() == ["Pid:", process.name],
        "procfs PID identity mismatch",
    )
    return {
        "single_worker_command_observed": True,
        "loopback_bind_observed": True,
    }


def _interfaces(raw: str) -> list[str]:
    lines = raw.splitlines()
    _require(len(lines) >= 2, "invalid network interface table")
    names: list[str] = []
    for line in lines[2:]:
        if not line.strip():
            continue
        name, separator, _ = line.partition(":")
        name = name.strip()
        _require(bool(separator and name) and name not in names, "invalid network interface row")
        names.append(name)
    _require(names == ["lo"], "network namespace has a non-loopback interface")
    return names


def _ipv4_routes(raw: str) -> list[str]:
    lines = [line for line in raw.splitlines() if line.strip()]
    _require(bool(lines), "missing IPv4 route table")
    interfaces: list[str] = []
    for line in lines[1:]:
        fields = line.split()
        _require(len(fields) == 11, "invalid IPv4 route row")
        interface, destination = fields[0], fields[1].lower()
        _require(
            re.fullmatch(r"[0-9a-f]{8}", destination) is not None
            and re.fullmatch(r"[0-9a-f]{8}", fields[2].lower()) is not None
            and re.fullmatch(r"[0-9a-f]{4}", fields[3].lower()) is not None
            and re.fullmatch(r"[0-9a-f]{8}", fields[7].lower()) is not None,
            "invalid IPv4 route address fields",
        )
        _require(interface == "lo", "IPv4 route uses a non-loopback interface")
        _require(destination != "00000000", "IPv4 default route is present")
        interfaces.append(interface)
    return sorted(set(interfaces))


def _ipv6_routes(raw: str) -> list[str]:
    interfaces: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        _require(len(fields) == 10, "invalid IPv6 route row")
        destination, prefix, interface = fields[0].lower(), fields[1].lower(), fields[-1]
        _require(
            re.fullmatch(r"[0-9a-f]{32}", destination) is not None
            and re.fullmatch(r"[0-9a-f]{2}", prefix) is not None
            and re.fullmatch(r"[0-9a-f]{32}", fields[2].lower()) is not None
            and re.fullmatch(r"[0-9a-f]{2}", fields[3].lower()) is not None
            and re.fullmatch(r"[0-9a-f]{32}", fields[4].lower()) is not None
            and all(re.fullmatch(r"[0-9a-f]{8}", value.lower()) for value in fields[5:9]),
            "invalid IPv6 route address fields",
        )
        _require(interface == "lo", "IPv6 route uses a non-loopback interface")
        _require(
            destination == "0" * 32 and prefix == "00",
            "IPv6 default route is present",
        )
        interfaces.append(interface)
    return sorted(set(interfaces))


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _canonical_model_path(value: str) -> str:
    path = PurePosixPath(value)
    _require(
        path.is_absolute() and value == path.as_posix() and value != "/" and ".." not in path.parts,
        "model directory must be an absolute canonical path",
    )
    return value


def _mount_for(model_path: str, mountinfo: str) -> dict:
    mounts: list[tuple[str, set[str]]] = []
    for line in mountinfo.splitlines():
        if not line.strip():
            continue
        before, separator, _ = line.partition(" - ")
        fields = before.split()
        _require(bool(separator) and len(fields) >= 6, "invalid mountinfo row")
        mountpoint = _unescape_mount(fields[4])
        _require(
            PurePosixPath(mountpoint).is_absolute()
            and PurePosixPath(mountpoint).as_posix() == mountpoint,
            "invalid mountpoint path",
        )
        mounts.append((mountpoint, set(fields[5].split(","))))

    covering = [
        (mountpoint, options)
        for mountpoint, options in mounts
        if mountpoint == "/"
        or model_path == mountpoint
        or model_path.startswith(mountpoint.rstrip("/") + "/")
    ]
    _require(bool(covering), "model directory has no covering mount")
    longest = max(len(mountpoint) for mountpoint, _ in covering)
    nearest = [(mountpoint, options) for mountpoint, options in covering if len(mountpoint) == longest]
    _require(len(nearest) == 1, "ambiguous covering model mount")
    mountpoint, options = nearest[0]
    _require("ro" in options and "rw" not in options, "model mount is not read-only")

    nested = [
        (path, mount_options)
        for path, mount_options in mounts
        if path == model_path or path.startswith(model_path.rstrip("/") + "/")
    ]
    _require(
        all("ro" in mount_options and "rw" not in mount_options for _, mount_options in nested),
        "model directory contains a writable nested mount",
    )
    return {
        "mountpoint": mountpoint,
        "read_only_mounts": sorted({path for path, _ in nested}),
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
    """Inspect a running server's namespaces, interfaces, routes and model mounts."""
    _require(type(server_pid) is int and 1 < server_pid <= 999_999_999, "invalid server PID")
    detection_path = _canonical_model_path(detection_model_dir)
    recognition_path = _canonical_model_path(recognition_model_dir)
    _require(
        detection_path != recognition_path
        and detection_path not in PurePosixPath(recognition_path).parents
        and recognition_path not in PurePosixPath(detection_path).parents,
        "detection and recognition model directories must be separate",
    )
    process = proc_root / str(server_pid)
    try:
        command = _server_command(process)
        network_namespace = _namespace(process / "ns" / "net", "net")
        mount_namespace = _namespace(process / "ns" / "mnt", "mnt")
        interfaces = _interfaces(_text(process / "net" / "dev"))
        ipv4_interfaces = _ipv4_routes(_text(process / "net" / "route"))
        ipv6_interfaces = _ipv6_routes(_text(process / "net" / "ipv6_route"))
        mountinfo = _text(process / "mountinfo")
        model_mounts = []
        for role, value in (
            ("detection", detection_model_dir),
            ("recognition", recognition_model_dir),
        ):
            model_path = _model_directory(process, value)
            model_mounts.append(
                {
                    "role": role,
                    "path": model_path,
                    **_mount_for(model_path, mountinfo),
                }
            )
        command = _server_command(process)
    except IsolationError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        raise IsolationError("target process isolation could not be observed") from None

    return {
        "schema_version": 1,
        "status": "LOCAL_OS_ISOLATION_OBSERVED_UNATTESTED",
        "server_pid": server_pid,
        "network_namespace": network_namespace,
        "mount_namespace": mount_namespace,
        "network_interfaces": interfaces,
        "ipv4_route_interfaces": ipv4_interfaces,
        "ipv6_route_interfaces": ipv6_interfaces,
        "egress_policy_observed": "LOOPBACK_ONLY_NO_DEFAULT_ROUTE",
        "model_mounts": model_mounts,
        "model_mounts_read_only_observed": True,
        **command,
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
        with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(raw + "\n")
    except (IsolationError, OSError, ValueError, TypeError, RecursionError):
        parser.exit(2, "OCR OS isolation observation refused; inspect private operator setup.\n")
    print("OS isolation observation recorded; independent attestation remains false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
