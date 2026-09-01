import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from validator import preflight_check, preflight_check_script


def test_project_preflight_accepts_uproject(tmp_path):
    project = tmp_path / "Game.uproject"
    project.write_text("{}")
    result = preflight_check(str(project))
    assert result.ok
    assert result.checks[0][0] == "uproject"


def test_project_preflight_rejects_missing_project(tmp_path):
    result = preflight_check(str(tmp_path / "missing.uproject"))
    assert not result.ok
    assert "not found" in result.checks[0][2]


def test_project_preflight_rejects_wrong_extension(tmp_path):
    project = tmp_path / "Game.json"
    project.write_text("{}")
    assert not preflight_check(str(project)).ok


def test_script_preflight(tmp_path):
    script = tmp_path / "work.py"
    script.write_text("def main(): pass")
    assert preflight_check_script(str(script)).ok
    assert not preflight_check_script(str(tmp_path / "missing.py")).ok


def test_report_prefix(tmp_path):
    project = tmp_path / "Game.uproject"
    project.write_text("{}")
    assert preflight_check(str(project)).report_lines()[0].startswith("[preflight] OK")
