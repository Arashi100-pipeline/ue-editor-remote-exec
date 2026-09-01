"""Safely attach to one exact Unreal project and inject Python over Remote Execution."""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import psutil

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import native_transport
from resource_lease import (
    ResourceLease,
    machine_lock_path,
    project_launch_lock_path,
)
from utils import (
    derive_launch_args_from_uproject,
    load_script_vars,
    normalize_path,
    parse_send_result,
    prepare_remote_script,
    project_arguments,
)
from validator import preflight_check, preflight_check_script

WAIT_FOR_EXE_TIMEOUT = 60
WAIT_FOR_EXISTING_NODE_TIMEOUT = 8
WAIT_FOR_LAUNCHED_NODE_TIMEOUT = 300
COMMAND_TIMEOUT = 3600
TRANSPORT_LOCK_TIMEOUT = 8
LAUNCH_LOCK_TIMEOUT = 8
GRACEFUL_CLOSE_TIMEOUT = 45
SESSION_SCHEMA_VERSION = 2
SESSION_APP_DIRECTORY = Path("ue-editor-remote-exec") / "sessions"


class NeedsUser(RuntimeError):
    """The workflow is safe to continue only after a user action."""


class RestartRequired(NeedsUser):
    """The exact Editor must be restarted with process-local Remote flags."""


class OutcomeUnknown(NeedsUser):
    """A mutating Remote command may have run but no final result arrived."""


class RemoteUnavailable(RuntimeError):
    """The exact editor process could not be verified over Remote Execution."""


@contextmanager
def _required_lease(path: Path, timeout: float, description: str):
    lease = ResourceLease(path)
    if not lease.acquire(timeout=timeout):
        raise NeedsUser(
            f"{description}正被另一个请求使用；已等待 {timeout:g} 秒。"
            "请稍后重试，本次没有启动、关闭或修改 Editor。"
        )
    try:
        yield
    finally:
        lease.release()


def _session_root() -> Path:
    override = os.environ.get("UE_REMOTE_SESSION_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / SESSION_APP_DIRECTORY
    return Path.home() / "AppData" / "Local" / SESSION_APP_DIRECTORY


def _default_session_path(uproject: str) -> str:
    project = normalize_path(str(Path(uproject).resolve()))
    digest = hashlib.sha256(project.encode("utf-8")).hexdigest()
    return str(_session_root() / f"{digest}.json")


def _uproject_from_working_directory() -> str:
    for directory in (Path.cwd(), *Path.cwd().parents):
        try:
            projects = sorted(directory.glob("*.uproject"))
        except OSError:
            continue
        if len(projects) == 1:
            return str(projects[0].resolve())
        if len(projects) > 1:
            raise RuntimeError(
                f"Multiple .uproject files found in {directory}; pass --session-file explicitly"
            )
    raise RuntimeError(
        "No .uproject found in the working directory or its parents; "
        "run from the project tree or pass --session-file explicitly"
    )


def _read_session(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_session(path: str, session: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(session, temporary, indent=2, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _remove_session(path: str) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _process_info(proc: psutil.Process) -> dict | None:
    try:
        info = proc.as_dict(
            attrs=["pid", "name", "cmdline", "cwd", "create_time", "exe"]
        )
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return info
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def _identity_for_process(proc: psutil.Process, project: str) -> dict | None:
    info = _process_info(proc)
    if not info:
        return None
    expected = normalize_path(project)
    projects = set(project_arguments(info.get("cmdline") or [], info.get("cwd") or ""))
    if expected not in projects:
        return None
    executable = normalize_path(info.get("exe") or "")
    if not executable:
        return None
    return {
        "project": str(Path(project).resolve()),
        "pid": int(info["pid"]),
        "create_time": float(info.get("create_time") or 0),
        "executable": executable,
    }


def _find_project_processes(project: str, exe_name: str) -> list[dict]:
    matches = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if str(proc.info.get("name") or "").casefold() != exe_name.casefold():
                continue
            identity = _identity_for_process(proc, project)
            if identity:
                matches.append(identity)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return matches


def _select_exact_process(project: str, exe_name: str) -> dict | None:
    matches = _find_project_processes(project, exe_name)
    if len(matches) > 1:
        pids = ", ".join(str(item["pid"]) for item in matches)
        raise NeedsUser(
            f"同一工程存在多个编辑器实例（PID: {pids}）。请只保留一个后重试。"
        )
    return matches[0] if matches else None


def _same_process(identity: dict) -> bool:
    try:
        proc = psutil.Process(int(identity.get("pid") or 0))
    except (ValueError, psutil.NoSuchProcess):
        return False
    current = _identity_for_process(proc, str(identity.get("project") or ""))
    if not current:
        return False
    try:
        return bool(
            abs(float(current["create_time"]) - float(identity["create_time"])) < 1.0
            and normalize_path(current["executable"])
            == normalize_path(str(identity.get("executable") or ""))
        )
    except (KeyError, TypeError, ValueError):
        return False


def _wait_for_project_process(
    project: str,
    exe_name: str,
    timeout: float,
    launched_pid: int,
) -> dict:
    print(
        f"[remote-exec] Waiting for exact project process "
        f"(launched PID {launched_pid}, up to {timeout:g}s)..."
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        identity = _select_exact_process(project, exe_name)
        if identity:
            print(f"[remote-exec] Exact project process: PID {identity['pid']}")
            return identity
        time.sleep(0.25)
    raise RuntimeError(
        f"Editor process for the exact project did not appear within {timeout:g}s"
    )


def _connect_verified(
    identity: dict,
    timeout: float,
    node_id_hint: str = "",
) -> str:
    """Verify the exact Editor identity through the independent native client."""

    try:
        node_id = native_transport.verify(identity, timeout, node_id_hint)
    except native_transport.NativeUnavailable as exc:
        raise RemoteUnavailable(
            f"Remote Execution for PID {identity['pid']} was not verified "
            f"within {timeout:g}s"
        ) from exc
    print(
        f"[remote-exec] Verified node {node_id} -> "
        f"PID {identity['pid']} / {identity['project']}"
    )
    return node_id


def _session_for(identity: dict, node_id: str, ownership: str) -> dict:
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "project": identity["project"],
        "pid": identity["pid"],
        "create_time": identity["create_time"],
        "executable": identity["executable"],
        "node_id_hint": node_id,
        "ownership": ownership,
        "last_verified": datetime.now(timezone.utc).isoformat(),
    }


def launch_session(
    uproject: str,
    session_file: str | None = None,
    wait_exe_timeout: int = WAIT_FOR_EXE_TIMEOUT,
    wait_existing_node_timeout: int = WAIT_FOR_EXISTING_NODE_TIMEOUT,
    wait_launched_node_timeout: int = WAIT_FOR_LAUNCHED_NODE_TIMEOUT,
) -> dict:
    """Attach to the exact open project, or launch it with temporary Remote flags."""

    preflight = preflight_check(uproject)
    for line in preflight.report_lines():
        print(line)
    if not preflight.ok:
        raise RuntimeError("project preflight failed")

    project = str(Path(uproject).resolve())
    launch_args, exe_name = derive_launch_args_from_uproject(project)
    target_session = session_file or _default_session_path(project)
    with _required_lease(
        project_launch_lock_path(project),
        LAUNCH_LOCK_TIMEOUT,
        "该 Unreal 工程的启动流程",
    ):
        with _required_lease(
            machine_lock_path("remote-execution-session.lock"),
            TRANSPORT_LOCK_TIMEOUT,
            "本机 Unreal Remote Execution 通道",
        ):
            cached = _read_session(target_session)
            hint = ""
            if normalize_path(str(cached.get("project") or "")) == normalize_path(
                project
            ):
                hint = str(cached.get("node_id_hint") or "")

            identity = _select_exact_process(project, exe_name)
            if identity:
                print(
                    f"[remote-exec] Exact project is already open (PID {identity['pid']})."
                )
                try:
                    node_id = _connect_verified(
                        identity, wait_existing_node_timeout, hint
                    )
                except RemoteUnavailable as exc:
                    raise RestartRequired(
                        "目标工程已打开，但 Remote Execution 未开启或无法验证。"
                        "请先保存工程；随后可运行 relaunch-remote --confirm-saved，"
                        "它只会请求正常关闭并用临时参数重新启动。"
                        "本次没有修改配置，也没有关闭或终止任何进程。"
                    ) from exc
                session = _session_for(identity, node_id, "attached")
            else:
                print(
                    "[remote-exec] Exact project is closed; launching with temporary Remote flags."
                )
                process = subprocess.Popen(
                    launch_args,
                    cwd=str(Path(project).parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                identity = _wait_for_project_process(
                    project, exe_name, wait_exe_timeout, int(process.pid)
                )
                try:
                    node_id = _connect_verified(
                        identity, wait_launched_node_timeout, ""
                    )
                except RemoteUnavailable as exc:
                    raise RuntimeError(
                        "Editor was launched, but its Remote Execution identity could not "
                        "be verified. The editor was left open for the user to inspect."
                    ) from exc
                session = _session_for(identity, node_id, "launched")

            _write_session(target_session, session)
    print(f"[remote-exec] Session ready: {target_session}")
    return session


def _resolve_session_file(
    session_file: str | None,
    uproject: str | None = None,
) -> str:
    project = uproject or (None if session_file else _uproject_from_working_directory())
    path = session_file or _default_session_path(str(project))
    if not Path(path).is_file():
        raise RuntimeError(f"Session file not found: {path}")
    return path


@contextmanager
def _locked_session(path: str):
    initial = _read_session(path)
    project = str(initial.get("project") or "")
    if not project:
        raise RuntimeError(f"Session has no project identity: {path}")
    with _required_lease(
        project_launch_lock_path(project),
        LAUNCH_LOCK_TIMEOUT,
        "该 Unreal 工程的生命周期",
    ):
        current = _read_session(path)
        if normalize_path(str(current.get("project") or "")) != normalize_path(project):
            raise NeedsUser("等待工程锁期间 session 已改变；本次未执行操作，请重试。")
        yield current


def _positive_timeout(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


def _prepare_script_step(script: str, variables: dict | None = None) -> tuple[str, str]:
    preflight = preflight_check_script(script)
    for line in preflight.report_lines():
        print(line)
    if not preflight.ok:
        raise RuntimeError(f"script preflight failed: {script}")
    path = str(Path(script).resolve())
    source = Path(path).read_text(encoding="utf-8-sig")
    return path, prepare_remote_script(source, variables or {}, path)


def run_script_steps(
    steps: list[dict],
    session_file: str | None = None,
    command_timeout: float = COMMAND_TIMEOUT,
    connect_timeout: float = WAIT_FOR_EXISTING_NODE_TIMEOUT,
    uproject: str | None = None,
) -> list[dict]:
    """Run ordered scripts through one verified Remote connection.

    Each step is ``{"script": <path>, "vars": <JSON object>}``. The scripts
    still receive separate Python namespaces; only the verified transport is
    reused for the request.
    """

    command_timeout = _positive_timeout(command_timeout, "command_timeout")
    connect_timeout = _positive_timeout(connect_timeout, "connect_timeout")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    prepared = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not step.get("script"):
            raise ValueError(f"step {index} must contain a script path")
        variables = step.get("vars") or {}
        path, source = _prepare_script_step(str(step["script"]), variables)
        prepared.append((path, source))

    target_session = _resolve_session_file(session_file, uproject)
    results = []
    try:
        with _locked_session(target_session) as session:
            if not _same_process(session):
                raise RuntimeError(
                    "Saved editor identity is stale or no longer matches the exact "
                    "project. Run ensure again."
                )
            with _required_lease(
                machine_lock_path("remote-execution-session.lock"),
                TRANSPORT_LOCK_TIMEOUT,
                "本机 Unreal Remote Execution 通道",
            ):
                node_id = str(session.get("node_id_hint") or "")
                node_id, command_results = native_transport.execute_many(
                    session,
                    [source for _path, source in prepared],
                    connect_timeout,
                    command_timeout,
                    node_id,
                )
                for (path, _source), result in zip(prepared, command_results):
                    success, output = parse_send_result(result)
                    results.append(
                        {"script": path, "success": success, "output": output}
                    )
                    if not success:
                        break

            session["node_id_hint"] = node_id
            session["last_verified"] = datetime.now(timezone.utc).isoformat()
            _write_session(target_session, session)
    except native_transport.NativeOutcomeUnknown as exc:
        raise OutcomeUnknown(
            "Remote 命令可能已经发送，但没有收到可信的最终结果；执行结果未知。"
            "请先检查 Editor 和资产状态，禁止自动重试该脚本。"
        ) from exc

    for step_result in results:
        print(
            f"[remote-exec] Script result: success={step_result['success']} "
            f"script={step_result['script']}"
        )
        for line in step_result["output"]:
            print(f"  {line}")
    return results


def inject_script(
    script: str,
    inject_vars: dict | None = None,
    session_file: str | None = None,
    command_timeout: float = COMMAND_TIMEOUT,
    uproject: str | None = None,
    connect_timeout: float = WAIT_FOR_EXISTING_NODE_TIMEOUT,
) -> tuple[bool, list[str]]:
    """Verify the saved editor identity, then inject one isolated script."""

    results = run_script_steps(
        [{"script": script, "vars": inject_vars or {}}],
        session_file=session_file,
        command_timeout=command_timeout,
        connect_timeout=connect_timeout,
        uproject=uproject,
    )
    result = results[0]
    return bool(result["success"]), list(result["output"])


def run_plan(
    plan_file: str,
    session_file: str | None = None,
    command_timeout: float = COMMAND_TIMEOUT,
    connect_timeout: float = WAIT_FOR_EXISTING_NODE_TIMEOUT,
    uproject: str | None = None,
) -> list[dict]:
    """Run a JSON plan containing ``steps`` over one verified connection."""

    plan_path = Path(plan_file).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    steps = plan.get("steps")
    if not isinstance(steps, list):
        raise ValueError("plan.steps must be a list")
    normalized_steps = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("every plan step must be an object")
        item = dict(step)
        script_value = item.get("script")
        if not isinstance(script_value, str) or not script_value.strip():
            raise ValueError("every plan step must contain a non-empty script path")
        script = Path(script_value)
        if not script.is_absolute():
            item["script"] = str((plan_path.parent / script).resolve())
        normalized_steps.append(item)
    return run_script_steps(
        normalized_steps,
        session_file=session_file,
        command_timeout=command_timeout,
        connect_timeout=connect_timeout,
        uproject=uproject,
    )


def _main_window_for_pid(pid: int) -> int:
    if os.name != "nt":
        return 0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    candidates = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    ]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetClassNameW.restype = ctypes.c_int

    @callback_type
    def collect(hwnd, _lparam):
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) != int(pid):
            return True
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
            return True
        title_length = user32.GetWindowTextLengthW(hwnd)
        if title_length <= 0:
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        unreal_bonus = 1 if class_name.value == "UnrealWindow" else 0
        candidates.append((unreal_bonus, area, int(hwnd)))
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if not candidates:
        return 0
    candidates.sort(reverse=True)
    return candidates[0][2]


def _request_graceful_close(pid: int) -> None:
    hwnd = _main_window_for_pid(pid)
    if not hwnd:
        raise NeedsUser(
            f"找不到 PID {pid} 的安全主窗口。请在 Unreal Editor 中手动保存并关闭。"
        )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    if not user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
        raise ctypes.WinError(ctypes.get_last_error())


def close_session(
    session_file: str | None = None,
    force: bool = False,
    uproject: str | None = None,
    close_timeout: float = GRACEFUL_CLOSE_TIMEOUT,
) -> None:
    """Request a normal window close; never terminate or kill the editor."""

    del force  # Retained only as a compatibility spelling; it grants no kill authority.
    close_timeout = _positive_timeout(close_timeout, "close_timeout")
    target_session = _resolve_session_file(session_file, uproject)
    with _locked_session(target_session) as session:
        if not _same_process(session):
            _remove_session(target_session)
            print(
                "[remote-exec] Editor already exited or session was stale; session removed."
            )
            return

        pid = int(session["pid"])
        print(f"[remote-exec] Requesting normal Editor close for PID {pid}...")
        _request_graceful_close(pid)
        deadline = time.monotonic() + close_timeout
        while time.monotonic() < deadline and _same_process(session):
            time.sleep(0.25)
        if _same_process(session):
            raise NeedsUser(
                "编辑器没有退出，通常是正在等待保存/放弃更改。"
                "请在编辑器中处理提示并手动关闭；会话记录已保留，未执行强制终止。"
            )
        _remove_session(target_session)
        print("[remote-exec] Editor closed normally; session removed.")


def relaunch_remote(
    uproject: str,
    confirm_saved: bool = False,
    session_file: str | None = None,
    close_timeout: float = GRACEFUL_CLOSE_TIMEOUT,
    wait_exe_timeout: float = WAIT_FOR_EXE_TIMEOUT,
    wait_existing_node_timeout: float = WAIT_FOR_EXISTING_NODE_TIMEOUT,
    wait_launched_node_timeout: float = WAIT_FOR_LAUNCHED_NODE_TIMEOUT,
) -> dict:
    """Safely enable Remote Execution by normal-close and process-local relaunch.

    The exact project and process identity are checked again immediately before
    closing. No process termination API and no project settings write is used.
    """

    preflight = preflight_check(uproject)
    for line in preflight.report_lines():
        print(line)
    if not preflight.ok:
        raise RuntimeError("project preflight failed")
    project = str(Path(uproject).resolve())
    _launch_args, exe_name = derive_launch_args_from_uproject(project)
    target_session = session_file or _default_session_path(project)
    identity = _select_exact_process(project, exe_name)
    if not identity:
        return launch_session(
            project,
            session_file=target_session,
            wait_exe_timeout=wait_exe_timeout,
            wait_existing_node_timeout=wait_existing_node_timeout,
            wait_launched_node_timeout=wait_launched_node_timeout,
        )

    cached = _read_session(target_session)
    hint = str(cached.get("node_id_hint") or "")
    try:
        with _required_lease(
            machine_lock_path("remote-execution-session.lock"),
            TRANSPORT_LOCK_TIMEOUT,
            "本机 Unreal Remote Execution 通道",
        ):
            node_id = _connect_verified(
                identity,
                _positive_timeout(
                    wait_existing_node_timeout, "wait_existing_node_timeout"
                ),
                hint,
            )
        session = _session_for(identity, node_id, "attached")
        _write_session(target_session, session)
        print("[remote-exec] Remote Execution is already ready; restart skipped.")
        return session
    except RemoteUnavailable:
        pass

    if not confirm_saved:
        raise RestartRequired(
            "目标工程已打开且 Remote Execution 不可用。请先保存工程，再明确传入 "
            "--confirm-saved；未获得确认前不会关闭 Editor。"
        )

    _write_session(target_session, _session_for(identity, "", "attached"))
    close_session(
        session_file=target_session,
        close_timeout=close_timeout,
    )
    return launch_session(
        project,
        session_file=target_session,
        wait_exe_timeout=wait_exe_timeout,
        wait_existing_node_timeout=wait_existing_node_timeout,
        wait_launched_node_timeout=wait_launched_node_timeout,
    )


def _emit_result(
    status: str,
    action: str,
    message: str = "",
    data: object | None = None,
) -> None:
    payload = {
        "result_version": 1,
        "status": status,
        "action": action,
        "message": message,
    }
    if data is not None:
        payload["data"] = data
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _add_session_lookup_arguments(parser) -> None:
    parser.add_argument("--session-file", default=None)
    parser.add_argument(
        "--uproject",
        default=None,
        help="Project used to derive the default LocalAppData session",
    )


def _add_remote_command_arguments(parser) -> None:
    _add_session_lookup_arguments(parser)
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=COMMAND_TIMEOUT,
        help="Maximum seconds to wait after each Remote command is sent",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=WAIT_FOR_EXISTING_NODE_TIMEOUT,
        help="Maximum seconds to verify the exact Remote node",
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    class StructuredArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(message)

    parser = StructuredArgumentParser(
        description="Attach to one exact Unreal project and execute Python safely."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("ensure", "launch"):
        launch_parser = subparsers.add_parser(
            command,
            help="Attach to or launch one exact .uproject",
        )
        launch_parser.add_argument("--uproject", required=True)
        launch_parser.add_argument("--session-file", default=None)
        launch_parser.add_argument(
            "--wait-exe-timeout", type=float, default=WAIT_FOR_EXE_TIMEOUT
        )
        launch_parser.add_argument(
            "--wait-existing-node-timeout",
            type=float,
            default=WAIT_FOR_EXISTING_NODE_TIMEOUT,
        )
        launch_parser.add_argument(
            "--wait-launched-node-timeout",
            type=float,
            default=WAIT_FOR_LAUNCHED_NODE_TIMEOUT,
        )

    inject_parser = subparsers.add_parser(
        "inject", help="Inject one script into the verified editor"
    )
    inject_parser.add_argument("--script", required=True)
    inject_parser.add_argument(
        "--vars-json", default="", help="JSON object exposed to the script"
    )
    inject_parser.add_argument(
        "--vars-file", default=None, help="UTF-8 JSON file containing an object"
    )
    inject_parser.add_argument(
        "--inject",
        default="",
        help="Deprecated KEY=VALUE compatibility syntax",
    )
    _add_remote_command_arguments(inject_parser)

    plan_parser = subparsers.add_parser(
        "run-plan", help="Run ordered isolated scripts over one verified channel"
    )
    plan_parser.add_argument("--plan", required=True)
    _add_remote_command_arguments(plan_parser)

    close_parser = subparsers.add_parser(
        "close", help="Request a normal editor-window close"
    )
    _add_session_lookup_arguments(close_parser)
    close_parser.add_argument(
        "--force",
        action="store_true",
        help="Clean a stale session; never terminates a running editor",
    )
    close_parser.add_argument(
        "--close-timeout", type=float, default=GRACEFUL_CLOSE_TIMEOUT
    )

    relaunch_parser = subparsers.add_parser(
        "relaunch-remote",
        help="Normal-close and relaunch with process-local Remote flags",
    )
    relaunch_parser.add_argument("--uproject", required=True)
    relaunch_parser.add_argument("--session-file", default=None)
    relaunch_parser.add_argument("--confirm-saved", action="store_true")
    relaunch_parser.add_argument(
        "--close-timeout", type=float, default=GRACEFUL_CLOSE_TIMEOUT
    )
    relaunch_parser.add_argument(
        "--wait-exe-timeout", type=float, default=WAIT_FOR_EXE_TIMEOUT
    )
    relaunch_parser.add_argument(
        "--wait-existing-node-timeout",
        type=float,
        default=WAIT_FOR_EXISTING_NODE_TIMEOUT,
    )
    relaunch_parser.add_argument(
        "--wait-launched-node-timeout",
        type=float,
        default=WAIT_FOR_LAUNCHED_NODE_TIMEOUT,
    )

    raw_args = list(sys.argv[1:] if argv is None else argv)
    action = raw_args[0] if raw_args else "unknown"
    try:
        args = parser.parse_args(raw_args)
    except ValueError as exc:
        _emit_result("failed", action, str(exc))
        return 1
    action = args.command
    try:
        if args.command in ("ensure", "launch"):
            session = launch_session(
                args.uproject,
                session_file=args.session_file,
                wait_exe_timeout=args.wait_exe_timeout,
                wait_existing_node_timeout=args.wait_existing_node_timeout,
                wait_launched_node_timeout=args.wait_launched_node_timeout,
            )
            _emit_result("succeeded", action, "Editor Remote session is ready", session)
            return 0
        if args.command == "inject":
            variables = load_script_vars(args.vars_json, args.vars_file, args.inject)
            success, output = inject_script(
                args.script,
                inject_vars=variables or None,
                session_file=args.session_file,
                command_timeout=args.command_timeout,
                uproject=args.uproject,
                connect_timeout=args.connect_timeout,
            )
            status = "succeeded" if success else "failed"
            _emit_result(
                status,
                action,
                "Script completed" if success else "Script reported failure",
                {"script": str(Path(args.script).resolve()), "output": output},
            )
            return 0 if success else 1
        if args.command == "run-plan":
            results = run_plan(
                args.plan,
                session_file=args.session_file,
                command_timeout=args.command_timeout,
                connect_timeout=args.connect_timeout,
                uproject=args.uproject,
            )
            success = bool(results) and all(item["success"] for item in results)
            _emit_result(
                "succeeded" if success else "failed",
                action,
                "Plan completed" if success else "Plan stopped after a failed step",
                {"steps": results},
            )
            return 0 if success else 1
        if args.command == "close":
            close_session(
                args.session_file,
                force=args.force,
                uproject=args.uproject,
                close_timeout=args.close_timeout,
            )
            _emit_result("succeeded", action, "Editor is closed or was already absent")
            return 0
        if args.command == "relaunch-remote":
            session = relaunch_remote(
                args.uproject,
                confirm_saved=args.confirm_saved,
                session_file=args.session_file,
                close_timeout=args.close_timeout,
                wait_exe_timeout=args.wait_exe_timeout,
                wait_existing_node_timeout=args.wait_existing_node_timeout,
                wait_launched_node_timeout=args.wait_launched_node_timeout,
            )
            _emit_result("succeeded", action, "Editor Remote session is ready", session)
            return 0
    except OutcomeUnknown as exc:
        _emit_result("outcome_unknown", action, str(exc))
        return 3
    except RestartRequired as exc:
        _emit_result("restart_required", action, str(exc))
        return 2
    except NeedsUser as exc:
        _emit_result("needs_user", action, str(exc))
        return 2
    except Exception as exc:
        _emit_result("failed", action, str(exc))
        return 1
    _emit_result("failed", action, "Unknown command state")
    return 1


if __name__ == "__main__":
    sys.exit(main())
