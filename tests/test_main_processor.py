import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import main_processor as m


def test_same_project_multiple_processes_needs_user(monkeypatch):
    monkeypatch.setattr(
        m,
        "_find_project_processes",
        lambda project, exe: [{"pid": 10}, {"pid": 20}],
    )
    with pytest.raises(m.NeedsUser, match="10, 20"):
        m._select_exact_process(
            r"X:\Fixture\ExampleProject\ExampleProject.uproject", "UnrealEditor.exe"
        )


def test_session_contains_only_compact_identity_fields():
    identity = {
        "project": r"X:\Fixture\ExampleProject\ExampleProject.uproject",
        "pid": 10,
        "create_time": 123.0,
        "executable": r"X:\Fixture\UnrealEditor.exe",
    }
    session = m._session_for(identity, "node-a", "attached")
    assert set(session) == {
        "schema_version",
        "project",
        "pid",
        "create_time",
        "executable",
        "node_id_hint",
        "ownership",
        "last_verified",
    }
    assert session["schema_version"] == 2
    assert session["last_verified"].endswith("+00:00")


def test_default_session_is_outside_project_and_hides_project_name(
    monkeypatch, tmp_path
):
    local_app_data = tmp_path / "LocalAppData"
    project = tmp_path / "SecretProject" / "SecretProject.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    session_path = Path(m._default_session_path(str(project)))

    assert session_path.parent == (
        local_app_data / "ue-editor-remote-exec" / "sessions"
    )
    assert session_path.suffix == ".json"
    assert len(session_path.stem) == 64
    assert "SecretProject" not in str(session_path)
    assert project.parent not in session_path.parents


def test_resolve_default_session_from_project_working_tree(monkeypatch, tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    project = tmp_path / "Game" / "Game.uproject"
    working_directory = project.parent / "Content" / "Python"
    working_directory.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.chdir(working_directory)
    expected = Path(m._default_session_path(str(project)))
    expected.parent.mkdir(parents=True)
    expected.write_text("{}", encoding="utf-8")

    assert Path(m._resolve_session_file(None)) == expected


def test_resolve_default_session_from_explicit_project(monkeypatch, tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    expected = Path(m._default_session_path(str(project)))
    expected.parent.mkdir(parents=True)
    expected.write_text("{}", encoding="utf-8")

    assert Path(m._resolve_session_file(None, str(project))) == expected


def test_session_root_can_be_overridden(monkeypatch, tmp_path):
    explicit = tmp_path / "sessions"
    monkeypatch.setenv("UE_REMOTE_SESSION_ROOT", str(explicit))
    assert m._session_root() == explicit.resolve()


def test_connect_verified_delegates_exact_identity_to_native(monkeypatch):
    identity = {
        "project": r"X:\Fixture\ExampleA\ExampleA.uproject",
        "pid": 10,
    }
    calls = []
    monkeypatch.setattr(
        m.native_transport,
        "verify",
        lambda actual, timeout, hint: calls.append((actual, timeout, hint)) or "node-a",
    )

    assert m._connect_verified(identity, 2.5, "old-node") == "node-a"
    assert calls == [(identity, 2.5, "old-node")]


def test_launch_acquires_transport_before_starting_editor(monkeypatch, tmp_path):
    project = tmp_path / "Game.uproject"
    project.write_text("{}", encoding="utf-8")
    preflight = Mock(ok=True)
    preflight.report_lines.return_value = []
    monkeypatch.setattr(m, "preflight_check", lambda _project: preflight)
    monkeypatch.setattr(
        m,
        "derive_launch_args_from_uproject",
        lambda _project: (["UnrealEditor.exe", str(project)], "UnrealEditor.exe"),
    )
    popen = Mock()
    monkeypatch.setattr(m.subprocess, "Popen", popen)
    calls = []

    @contextmanager
    def required_lease(_path, _timeout, description):
        calls.append(description)
        if "Remote Execution" in description:
            raise m.NeedsUser("busy")
        yield

    monkeypatch.setattr(m, "_required_lease", required_lease)

    with pytest.raises(m.NeedsUser, match="busy"):
        m.launch_session(str(project))

    assert calls == ["该 Unreal 工程的启动流程", "本机 Unreal Remote Execution 通道"]
    popen.assert_not_called()


def test_inject_timeout_is_outcome_unknown_and_not_plain_failure(monkeypatch, tmp_path):
    session_file = tmp_path / "ue_session.json"
    session_file.write_text(
        json.dumps({"project": str(tmp_path / "Game.uproject")}),
        encoding="utf-8",
    )
    script = tmp_path / "work.py"
    script.write_text("def main(): pass", encoding="utf-8")
    monkeypatch.setattr(m, "_same_process", lambda _session: True)
    preflight = Mock(ok=True)
    preflight.report_lines.return_value = []
    monkeypatch.setattr(m, "preflight_check_script", lambda _script: preflight)

    def timed_out(*_args, **_kwargs):
        raise m.native_transport.NativeOutcomeUnknown("outcome is unknown")

    monkeypatch.setattr(m.native_transport, "execute_many", timed_out)

    with pytest.raises(m.OutcomeUnknown, match="结果未知"):
        m.inject_script(str(script), session_file=str(session_file))


def test_run_script_steps_reuses_one_native_connection_and_updates_hint(
    monkeypatch, tmp_path
):
    session_file = tmp_path / "session.json"
    session = {
        "project": str(tmp_path / "Game.uproject"),
        "node_id_hint": "old-node",
    }
    session_file.write_text(json.dumps(session), encoding="utf-8")
    scripts = []
    for index in range(2):
        path = tmp_path / f"step{index}.py"
        path.write_text(f"print('step {index}')", encoding="utf-8")
        scripts.append(path)

    preflight = Mock(ok=True)
    preflight.report_lines.return_value = []
    monkeypatch.setattr(m, "preflight_check_script", lambda _script: preflight)
    monkeypatch.setattr(m, "_same_process", lambda _session: True)
    writes = []
    monkeypatch.setattr(
        m, "_write_session", lambda path, value: writes.append((path, value))
    )
    calls = []

    def execute_many(identity, sources, connect_timeout, command_timeout, node_hint):
        calls.append((identity, sources, connect_timeout, command_timeout, node_hint))
        return "new-node", [{"success": True, "output": []} for _source in sources]

    monkeypatch.setattr(m.native_transport, "execute_many", execute_many)
    results = m.run_script_steps(
        [
            {"script": str(path), "vars": {"INDEX": index}}
            for index, path in enumerate(scripts)
        ],
        session_file=str(session_file),
    )

    assert len(calls) == 1
    assert len(calls[0][1]) == 2
    assert calls[0][-1] == "old-node"
    assert all(item["success"] for item in results)
    assert writes[-1][1]["node_id_hint"] == "new-node"


def test_relaunch_remote_requires_explicit_saved_confirmation(monkeypatch, tmp_path):
    project = tmp_path / "Game.uproject"
    project.write_text("{}", encoding="utf-8")
    preflight = Mock(ok=True)
    preflight.report_lines.return_value = []
    monkeypatch.setattr(m, "preflight_check", lambda _project: preflight)
    monkeypatch.setattr(
        m,
        "derive_launch_args_from_uproject",
        lambda _project: (["UnrealEditor.exe", str(project)], "UnrealEditor.exe"),
    )
    identity = {
        "project": str(project),
        "pid": 10,
        "create_time": 123.0,
        "executable": r"X:\Fixture\UnrealEditor.exe",
    }
    monkeypatch.setattr(m, "_select_exact_process", lambda *_args: identity)

    def unavailable(*_args, **_kwargs):
        raise m.RemoteUnavailable("disabled")

    monkeypatch.setattr(m, "_connect_verified", unavailable)
    close = Mock()
    monkeypatch.setattr(m, "close_session", close)

    with pytest.raises(m.RestartRequired, match="confirm-saved"):
        m.relaunch_remote(str(project), confirm_saved=False)

    close.assert_not_called()


def test_cli_emits_structured_restart_required(monkeypatch, capsys):
    def restart_required(*_args, **_kwargs):
        raise m.RestartRequired("save first")

    monkeypatch.setattr(m, "launch_session", restart_required)
    exit_code = m.main(
        [
            "ensure",
            "--uproject",
            r"X:\Fixture\ExampleProject\ExampleProject.uproject",
        ]
    )
    final_line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(final_line)

    assert exit_code == 2
    assert payload == {
        "result_version": 1,
        "status": "restart_required",
        "action": "ensure",
        "message": "save first",
    }


def test_cli_parse_error_is_structured_json(capsys):
    exit_code = m.main(["inject"])
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["action"] == "inject"


def test_cli_emits_outcome_unknown_without_retry(monkeypatch, tmp_path, capsys):
    script = tmp_path / "work.py"
    script.write_text("print('once')", encoding="utf-8")

    def outcome_unknown(*_args, **_kwargs):
        raise m.OutcomeUnknown("do not retry")

    monkeypatch.setattr(m, "inject_script", outcome_unknown)
    exit_code = m.main(
        [
            "inject",
            "--script",
            str(script),
            "--session-file",
            str(tmp_path / "session.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert exit_code == 3
    assert payload["status"] == "outcome_unknown"
    assert payload["message"] == "do not retry"


def test_close_source_has_no_process_termination_calls():
    source = Path(m.__file__).read_text(encoding="utf-8")
    assert ".terminate(" not in source
    assert ".kill(" not in source
