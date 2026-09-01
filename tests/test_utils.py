import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import utils as u


def test_derive_project_dir_and_game_path():
    assert u.derive_project_dir(r"X:\Fixture\ExampleProject\Content\Maps") == str(
        Path(r"X:\Fixture\ExampleProject")
    )
    assert (
        u.derive_import_dest(r"X:\Fixture\ExampleProject\Content\Maps\A")
        == "/Game/Maps/A"
    )


def test_paths_without_content_fail():
    with pytest.raises(ValueError):
        u.derive_project_dir(r"X:\Fixture\ExampleProject\Assets")
    with pytest.raises(ValueError):
        u.derive_import_dest(r"X:\Fixture\ExampleProject\Assets")


def test_temporary_remote_arguments_are_process_only():
    args = u.temporary_remote_arguments()
    joined = " ".join(args)
    assert "-EnablePlugins=PythonScriptPlugin" in joined
    assert "bRemoteExecution=True" in joined
    assert "239.0.0.1:6766" in joined
    override = next(item for item in args if item.startswith("-ini:Engine:"))
    section = f"[{u.PYTHON_SETTINGS_SECTION}]:"
    assert override.count(section) == 4
    assert f",{section}RemoteExecutionMulticastGroupEndpoint=" in override
    assert f",{section}RemoteExecutionMulticastBindAddress=" in override
    assert f",{section}RemoteExecutionMulticastTtl=" in override


def test_temporary_remote_arguments_support_safe_environment_overrides(monkeypatch):
    monkeypatch.setenv("UE_REMOTE_MULTICAST_GROUP_ENDPOINT", "239.1.2.3:7777")
    monkeypatch.setenv("UE_REMOTE_MULTICAST_BIND_ADDRESS", "127.0.0.2")
    monkeypatch.setenv("UE_REMOTE_MULTICAST_TTL", "0")
    joined = " ".join(u.temporary_remote_arguments())
    assert "239.1.2.3:7777" in joined
    assert "127.0.0.2" in joined
    assert "RemoteExecutionMulticastTtl=0" in joined


def test_temporary_remote_arguments_reject_nonlocal_network_settings():
    with pytest.raises(ValueError, match="TTL must be zero"):
        u.temporary_remote_arguments(ttl=1)
    with pytest.raises(ValueError, match="bind address must be loopback"):
        u.temporary_remote_arguments(bind_address="0.0.0.0")
    with pytest.raises(ValueError, match="must be IPv4 multicast"):
        u.temporary_remote_arguments(group_endpoint="127.0.0.1:6766")


def test_temporary_remote_arguments_reject_ini_injection():
    with pytest.raises(ValueError, match="unsafe"):
        u.temporary_remote_arguments(group_endpoint="239.0.0.1:6766,Injected=True")


def test_project_arguments_support_common_spellings(tmp_path):
    project = tmp_path / "Game.uproject"
    project.write_text("{}")
    expected = u.normalize_path(str(project))
    assert list(u.project_arguments(["Editor.exe", str(project)])) == [expected]
    assert list(u.project_arguments(["Editor.exe", "-project", str(project)])) == [
        expected
    ]
    assert list(u.project_arguments(["Editor.exe", f"-project={project}"])) == [
        expected
    ]


def test_isolated_wrapper_calls_main_once_and_preserves_guard(capsys):
    source = (
        "def main():\n"
        "    print('main', PATH, COUNT)\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    prepared = u.prepare_remote_script(
        source, {"PATH": r"X:\Fixture Folder\file.txt", "COUNT": 2}
    )
    persistent_remote_globals = {}
    exec(prepared, persistent_remote_globals, persistent_remote_globals)

    assert capsys.readouterr().out.strip() == (r"main X:\Fixture Folder\file.txt 2")
    assert "main" not in persistent_remote_globals
    assert "PATH" not in persistent_remote_globals
    assert "_ue_remote_exec_step_v2" not in persistent_remote_globals


def test_second_top_level_script_cannot_call_stale_main(capsys):
    persistent_remote_globals = {}
    first = u.prepare_remote_script("def main():\n    print('old main')\n")
    second = u.prepare_remote_script("print('top level only')\n")

    exec(first, persistent_remote_globals, persistent_remote_globals)
    exec(second, persistent_remote_globals, persistent_remote_globals)

    assert capsys.readouterr().out.splitlines() == ["old main", "top level only"]


def test_script_vars_json_supports_spaces_and_types(tmp_path):
    vars_file = tmp_path / "vars.json"
    vars_file.write_text(
        '{"LABEL":"hello world","COUNT":2,"ENABLED":true,"ITEMS":[1,2]}',
        encoding="utf-8",
    )
    assert u.load_script_vars(vars_file=str(vars_file)) == {
        "LABEL": "hello world",
        "COUNT": 2,
        "ENABLED": True,
        "ITEMS": [1, 2],
    }
    assert u.load_script_vars('{"VALUE":null}') == {"VALUE": None}


def test_script_vars_rejects_ambiguous_or_invalid_input():
    with pytest.raises(ValueError, match="only one"):
        u.load_script_vars('{"A":1}', legacy_inject="B=2")
    with pytest.raises(ValueError, match="invalid script variable name"):
        u.load_script_vars('{"not-valid":1}')


def test_parse_result():

    success, lines = u.parse_send_result(
        {"success": True, "output": [{"type": "log", "output": "done"}]}
    )
    assert success
    assert lines == ["[log] done"]
