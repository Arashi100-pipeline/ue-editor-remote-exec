"""Small, deterministic helpers for UE Editor Remote Execution."""

import json
import ipaddress
import os
import re
import winreg
from pathlib import Path

PYTHON_PLUGIN = "PythonScriptPlugin"
PYTHON_SETTINGS_SECTION = "/Script/PythonScriptPlugin.PythonScriptPluginSettings"


def normalize_path(value: str) -> str:
    """Return a case-insensitive absolute Windows path for comparisons."""

    if not value:
        return ""
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def derive_project_dir(content_dir: str) -> str:
    """Return the project root above the nearest ``Content`` directory."""

    path = Path(content_dir)
    for index, part in enumerate(path.parts):
        if part.lower() == "content":
            return str(Path(*path.parts[:index]))
    raise ValueError(f"Cannot find 'Content' directory in path: {content_dir}")


def derive_import_dest(ue4_content_dir: str) -> str:
    """Convert a physical path below Content to its ``/Game`` path."""

    path = Path(ue4_content_dir)
    for index, part in enumerate(path.parts):
        if part.lower() == "content":
            relative = path.parts[index + 1 :]
            return "/Game/" + "/".join(relative) if relative else "/Game/"
    raise ValueError(f"Cannot find 'Content' in ue4_content_dir: {ue4_content_dir}")


def derive_ue_exe_from_uproject(uproject_path: str) -> tuple[str, str]:
    """Resolve the editor executable registered by ``EngineAssociation``."""

    uproject = Path(uproject_path)
    data = json.loads(uproject.read_text(encoding="utf-8-sig"))
    association = str(data.get("EngineAssociation") or "")
    if not association:
        raise RuntimeError(
            f"EngineAssociation not found in {uproject_path}. "
            "Cannot auto-detect engine path."
        )

    installed_dir = ""
    registry_keys = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\EpicGames\Unreal Engine\{association}",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            rf"SOFTWARE\EpicGames\Unreal Engine\{association}",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Epic Games\Unreal Engine\Builds",
        ),
    ]
    for hive, key_path in registry_keys:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                if key_path.endswith("Builds"):
                    installed_dir, _ = winreg.QueryValueEx(key, association)
                else:
                    installed_dir, _ = winreg.QueryValueEx(key, "InstalledDirectory")
                break
        except FileNotFoundError:
            continue

    if not installed_dir:
        raise RuntimeError(
            f"Engine '{association}' was not found in the Windows registry."
        )

    major_match = re.match(r"(\d+)", association)
    if major_match:
        major = int(major_match.group(1))
    else:
        build_version = Path(installed_dir) / "Engine" / "Build" / "Build.version"
        try:
            major = int(json.loads(build_version.read_text())["MajorVersion"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            major = 5
    exe_name = "UnrealEditor.exe" if major >= 5 else "UE4Editor.exe"
    exe_path = Path(installed_dir) / "Engine" / "Binaries" / "Win64" / exe_name
    if not exe_path.is_file():
        raise RuntimeError(f"Editor executable not found: {exe_path}")
    return str(exe_path.resolve()), exe_name


def _safe_ini_value(name: str, value: object) -> str:
    text = str(value)
    if not text or any(character in text for character in ",[]\r\n"):
        raise ValueError(f"unsafe {name}: {text!r}")
    return text


def temporary_remote_arguments(
    group_endpoint: str | None = None,
    bind_address: str | None = None,
    ttl: int | str | None = None,
) -> list[str]:
    """Enable Python Remote Execution for this editor process only."""

    group_endpoint = _safe_ini_value(
        "Remote Execution multicast endpoint",
        group_endpoint
        or os.environ.get("UE_REMOTE_MULTICAST_GROUP_ENDPOINT")
        or "239.0.0.1:6766",
    )
    bind_address = _safe_ini_value(
        "Remote Execution bind address",
        bind_address
        or os.environ.get("UE_REMOTE_MULTICAST_BIND_ADDRESS")
        or "127.0.0.1",
    )
    ttl_text = _safe_ini_value(
        "Remote Execution multicast TTL",
        ttl if ttl is not None else os.environ.get("UE_REMOTE_MULTICAST_TTL", "0"),
    )
    try:
        ttl_value = int(ttl_text)
    except ValueError as exc:
        raise ValueError("Remote Execution multicast TTL must be an integer") from exc
    if ttl_value != 0:
        raise ValueError("Remote Execution multicast TTL must be zero")
    try:
        group_host, group_port_text = group_endpoint.rsplit(":", 1)
        group_host_value = ipaddress.IPv4Address(group_host)
        group_port = int(group_port_text)
        bind_value = ipaddress.IPv4Address(bind_address)
    except (ValueError, ipaddress.AddressValueError) as exc:
        raise ValueError(
            "Remote Execution endpoints must use valid IPv4 values"
        ) from exc
    if not group_host_value.is_multicast or not 1 <= group_port <= 65535:
        raise ValueError("Remote Execution group endpoint must be IPv4 multicast")
    if not bind_value.is_loopback:
        raise ValueError("Remote Execution bind address must be loopback")
    overrides = (
        "bRemoteExecution=True",
        f"RemoteExecutionMulticastGroupEndpoint={group_endpoint}",
        f"RemoteExecutionMulticastBindAddress={bind_address}",
        f"RemoteExecutionMulticastTtl={ttl_value}",
    )
    settings = "-ini:Engine:" + ",".join(
        f"[{PYTHON_SETTINGS_SECTION}]:{override}" for override in overrides
    )
    return [f"-EnablePlugins={PYTHON_PLUGIN}", settings]


def derive_launch_args_from_uproject(uproject_path: str) -> tuple[list[str], str]:
    """Build a shell-free editor launch argument list."""

    exe_path, exe_name = derive_ue_exe_from_uproject(uproject_path)
    project = str(Path(uproject_path).resolve())
    return [exe_path, project, *temporary_remote_arguments()], exe_name


def project_arguments(cmdline, cwd: str = ""):
    """Yield normalized .uproject arguments from a process command line."""

    tokens = list(cmdline or [])
    index = 0
    while index < len(tokens):
        token = str(tokens[index] or "").strip().strip('"')
        candidate = ""
        lowered = token.lower()
        if lowered in ("-project", "/project") and index + 1 < len(tokens):
            index += 1
            candidate = str(tokens[index] or "").strip().strip('"')
        elif "=" in token:
            prefix, value = token.split("=", 1)
            if prefix.lower() in ("-project", "/project", "project"):
                candidate = value.strip().strip('"')
        elif lowered.endswith(".uproject"):
            candidate = token
        if candidate and candidate.lower().endswith(".uproject"):
            if not os.path.isabs(candidate) and cwd:
                candidate = os.path.join(cwd, candidate)
            yield normalize_path(candidate)
        index += 1


def _validated_json_vars(inject_vars: dict[str, object]) -> str:
    """Return a JSON payload after validating safe namespace variable names."""

    if not isinstance(inject_vars, dict):
        raise ValueError("script variables must be a JSON object")
    reserved = {"__name__", "__file__", "__builtins__"}
    for key in inject_vars:
        if not isinstance(key, str) or not key.isidentifier() or key in reserved:
            raise ValueError(f"invalid script variable name: {key!r}")
    try:
        return json.dumps(inject_vars, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"script variables must contain JSON values: {exc}") from exc


def prepare_remote_script(
    src: str,
    inject_vars: dict[str, object] | None = None,
    script_path: str = "<remote-script>",
) -> str:
    """Wrap source in a fresh namespace and invoke a callable ``main`` once.

    Unreal's Remote Execution ``ExecuteFile`` mode reuses a persistent global
    namespace.  The wrapper deliberately keeps the submitted script out of that
    namespace so names from an earlier step cannot leak into a later one.
    ``__name__`` is not ``__main__``; a conventional main guard therefore stays
    intact, and the callable is invoked explicitly exactly once when present.
    Top-level-only scripts remain valid and execute exactly once.
    """

    variables_json = _validated_json_vars(inject_vars or {})
    source_literal = repr(src)
    path_literal = repr(str(script_path or "<remote-script>"))
    variables_literal = repr(variables_json)
    return (
        "def _ue_remote_exec_step_v2():\n"
        "    import json as _ue_remote_json\n"
        f"    _ue_remote_path = {path_literal}\n"
        "    _ue_remote_namespace = {\n"
        "        '__name__': 'ue_remote_step',\n"
        "        '__file__': _ue_remote_path,\n"
        "    }\n"
        f"    _ue_remote_namespace.update(_ue_remote_json.loads({variables_literal}))\n"
        "    try:\n"
        f"        exec(compile({source_literal}, _ue_remote_path, 'exec'), "
        "_ue_remote_namespace, _ue_remote_namespace)\n"
        "        _ue_remote_main = _ue_remote_namespace.get('main')\n"
        "        if callable(_ue_remote_main):\n"
        "            _ue_remote_main()\n"
        "    finally:\n"
        "        _ue_remote_namespace.clear()\n"
        "try:\n"
        "    _ue_remote_exec_step_v2()\n"
        "finally:\n"
        "    del _ue_remote_exec_step_v2\n"
    )


def inject_vars_into_src(src: str, inject_vars: dict[str, object]) -> str:
    """Compatibility alias for the isolated script wrapper."""

    return prepare_remote_script(src, inject_vars)


def load_script_vars(
    vars_json: str = "",
    vars_file: str | None = None,
    legacy_inject: str = "",
) -> dict[str, object]:
    """Load script variables from one unambiguous CLI source."""

    supplied = sum(bool(value) for value in (vars_json, vars_file, legacy_inject))
    if supplied > 1:
        raise ValueError("use only one of --vars-json, --vars-file, or --inject")
    if vars_file:
        raw = Path(vars_file).read_text(encoding="utf-8-sig")
    elif vars_json:
        raw = vars_json
    elif legacy_inject:
        values = {}
        for token in legacy_inject.split():
            if "=" not in token:
                raise ValueError(f"legacy --inject token must be KEY=VALUE: {token!r}")
            key, value = token.split("=", 1)
            values[key.strip()] = value
        _validated_json_vars(values)
        return values
    else:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid script variables JSON: {exc}") from exc
    _validated_json_vars(values)
    return values


def parse_send_result(result: dict) -> tuple[bool, list[str]]:
    """Extract a compact success flag and non-empty output lines."""

    success = bool(result.get("success", False))
    lines = [
        f"[{item.get('type', '?')}] {item.get('output', '')}"
        for item in result.get("output", [])
        if item.get("output", "").strip()
    ]
    return success, lines
