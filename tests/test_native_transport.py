import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import native_transport as n


def test_parse_result_uses_structured_last_line():
    payload = {
        "result_version": 1,
        "status": "succeeded",
        "action": "verify",
    }
    assert n._parse_result("diagnostic\n" + json.dumps(payload)) == payload


def test_verify_passes_exact_pid_project_parent_and_hint(monkeypatch, tmp_path):
    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    calls = []

    def run(arguments, process_timeout, mutating):
        calls.append((arguments, process_timeout, mutating))
        return {"status": "succeeded", "data": {"node_id": "node-a"}}

    monkeypatch.setattr(n, "_run", run)
    node_id = n.verify(
        {"project": str(project), "pid": 42},
        timeout=3.0,
        node_id_hint="old-node",
    )

    assert node_id == "node-a"
    arguments, process_timeout, mutating = calls[0]
    assert arguments[0] == "verify"
    assert arguments[arguments.index("--expected-pid") + 1] == "42"
    assert (
        Path(arguments[arguments.index("--expected-project-dir") + 1]) == project.parent
    )
    assert arguments[arguments.index("--node-id-hint") + 1] == "old-node"
    assert process_timeout == 11.0
    assert mutating is False


def test_execute_uses_temporary_utf8_file_and_removes_it(monkeypatch, tmp_path):
    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    observed = {}

    def run(arguments, process_timeout, mutating):
        plan = Path(arguments[arguments.index("--plan") + 1])
        plan_value = json.loads(plan.read_text(encoding="utf-8"))
        script = Path(plan_value["scripts"][0])
        observed["plan"] = plan
        observed["script"] = script
        observed["source"] = script.read_text(encoding="utf-8")
        observed["mutating"] = mutating
        observed["process_timeout"] = process_timeout
        return {
            "status": "succeeded",
            "data": {
                "node_id": "node-b",
                "results": [{"success": True, "result": "None", "output": []}],
            },
        }

    monkeypatch.setattr(n, "_run", run)
    node_id, result = n.execute(
        {"project": str(project), "pid": 7},
        "print('你好')\n",
        connect_timeout=2.0,
        command_timeout=10.0,
        node_id_hint="node-a",
    )

    assert node_id == "node-b"
    assert result["success"] is True
    assert observed["source"] == "print('你好')\n"
    assert observed["mutating"] is True
    assert observed["process_timeout"] == 24.0
    assert not observed["script"].exists()
    assert not observed["plan"].exists()


def test_mutating_process_timeout_is_outcome_unknown(monkeypatch):
    monkeypatch.setattr(n, "_binary_path", lambda: Path("client.exe"))

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("client.exe", 1)

    monkeypatch.setattr(n.subprocess, "run", timeout)
    with pytest.raises(n.NativeOutcomeUnknown, match="may have begun"):
        n._run(["execute"], process_timeout=1.0, mutating=True)


def test_known_remote_failure_with_result_is_preserved(monkeypatch):
    monkeypatch.setattr(n, "_binary_path", lambda: Path("client.exe"))
    payload = {
        "result_version": 1,
        "status": "failed",
        "action": "execute",
        "message": "remote command reported failure",
        "data": {
            "node_id": "node-a",
            "result": {"success": False, "result": "error", "output": []},
        },
    }
    monkeypatch.setattr(
        n.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(payload), stderr="", returncode=1
        ),
    )

    assert n._run(["execute"], process_timeout=1.0, mutating=True) == payload
