"""Bridge the Python lifecycle coordinator to the independent Rust client."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


class NativeTransportError(RuntimeError):
    """The native client could not complete a known-safe operation."""


class NativeUnavailable(NativeTransportError):
    """No exact Unreal Remote Execution identity was verified."""


class NativeOutcomeUnknown(NativeTransportError):
    """A command may have executed but no trustworthy result was received."""


_SKILL_ROOT = Path(__file__).resolve().parent.parent
_BINARY_NAME = "ue-remote-client.exe" if os.name == "nt" else "ue-remote-client"


def _binary_path() -> Path:
    override = os.environ.get("UE_REMOTE_CLIENT_EXE")
    candidates = [
        Path(override).expanduser() if override else None,
        _SKILL_ROOT / "bin" / _BINARY_NAME,
        _SKILL_ROOT
        / "native"
        / "ue-remote-client"
        / "target"
        / "release"
        / _BINARY_NAME,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise NativeUnavailable(
        "Native Unreal Remote client was not found. Install a release binary in "
        f"{_SKILL_ROOT / 'bin' / _BINARY_NAME} or set UE_REMOTE_CLIENT_EXE."
    )


def _positive_timeout(value: float, name: str) -> float:
    result = float(value)
    if not (result > 0.0 and result < float("inf")):
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _parse_result(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("result_version") == 1:
            return value
    raise NativeTransportError("Native client returned no structured result")


def _run(arguments: list[str], process_timeout: float, mutating: bool) -> dict:
    command = [str(_binary_path()), *arguments]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=process_timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        if mutating:
            raise NativeOutcomeUnknown(
                "Native client exceeded its process deadline after command dispatch may have begun"
            ) from exc
        raise NativeUnavailable("Native identity verification timed out") from exc
    except OSError as exc:
        raise NativeUnavailable(f"Native client could not start: {exc}") from exc

    result = _parse_result(completed.stdout)
    status = result.get("status")
    if status == "outcome_unknown":
        raise NativeOutcomeUnknown(str(result.get("message") or "outcome unknown"))
    if status == "failed" and not result.get("data"):
        error = str(result.get("message") or "native client failed")
        if mutating:
            raise NativeTransportError(error)
        raise NativeUnavailable(error)
    return result


def _identity_arguments(identity: dict, node_id_hint: str) -> list[str]:
    project = Path(str(identity["project"])).resolve()
    arguments = [
        "--expected-pid",
        str(int(identity["pid"])),
        "--expected-project-dir",
        str(project.parent),
    ]
    if node_id_hint:
        arguments.extend(["--node-id-hint", node_id_hint])
    return arguments


def _network_arguments() -> list[str]:
    return [
        "--multicast-endpoint",
        os.environ.get("UE_REMOTE_MULTICAST_GROUP_ENDPOINT", "239.0.0.1:6766"),
        "--multicast-bind-address",
        os.environ.get("UE_REMOTE_MULTICAST_BIND_ADDRESS", "127.0.0.1"),
    ]


def verify(identity: dict, timeout: float, node_id_hint: str = "") -> str:
    timeout = _positive_timeout(timeout, "timeout")
    arguments = [
        "verify",
        *_network_arguments(),
        *_identity_arguments(identity, node_id_hint),
        "--discovery-timeout-secs",
        str(timeout),
        "--connect-timeout-secs",
        str(timeout),
    ]
    result = _run(arguments, process_timeout=(timeout * 2.0) + 5.0, mutating=False)
    node_id = str((result.get("data") or {}).get("node_id") or "")
    if not node_id:
        raise NativeUnavailable("Native client verified no node identity")
    return node_id


def execute(
    identity: dict,
    source: str,
    connect_timeout: float,
    command_timeout: float,
    node_id_hint: str = "",
) -> tuple[str, dict]:
    node_id, results = execute_many(
        identity,
        [source],
        connect_timeout,
        command_timeout,
        node_id_hint,
    )
    return node_id, results[0]


def execute_many(
    identity: dict,
    sources: list[str],
    connect_timeout: float,
    command_timeout: float,
    node_id_hint: str = "",
) -> tuple[str, list[dict]]:
    connect_timeout = _positive_timeout(connect_timeout, "connect_timeout")
    command_timeout = _positive_timeout(command_timeout, "command_timeout")
    if not sources:
        raise ValueError("sources must contain at least one script")
    temporary_scripts = []
    plan = None
    try:
        for source in sources:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="ue-remote-script-", suffix=".py"
            )
            temporary = Path(temporary_name)
            temporary_scripts.append(temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(source)
        plan_descriptor, plan_name = tempfile.mkstemp(
            prefix="ue-remote-plan-", suffix=".json"
        )
        plan = Path(plan_name)
        with os.fdopen(plan_descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump({"scripts": [str(path) for path in temporary_scripts]}, stream)
        arguments = [
            "execute-plan",
            *_network_arguments(),
            "--plan",
            str(plan),
            *_identity_arguments(identity, node_id_hint),
            "--discovery-timeout-secs",
            str(connect_timeout),
            "--connect-timeout-secs",
            str(connect_timeout),
            "--command-timeout-secs",
            str(command_timeout),
        ]
        result = _run(
            arguments,
            process_timeout=(connect_timeout * 2.0)
            + (command_timeout * len(sources))
            + 10.0,
            mutating=True,
        )
    finally:
        if plan is not None:
            plan.unlink(missing_ok=True)
        for temporary in temporary_scripts:
            temporary.unlink(missing_ok=True)

    data = result.get("data") or {}
    node_id = str(data.get("node_id") or "")
    command_results = data.get("results")
    if (
        not node_id
        or not isinstance(command_results, list)
        or not command_results
        or any(not isinstance(item, dict) for item in command_results)
    ):
        raise NativeTransportError(
            "Native client returned an incomplete command result"
        )
    return node_id, command_results
