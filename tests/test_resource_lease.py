import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from resource_lease import ResourceLease, project_launch_lock_path


def test_resource_lease_serializes_processes_and_releases(tmp_path):
    path = tmp_path / "transport.lock"
    first = ResourceLease(path)
    second = ResourceLease(path)

    assert first.acquire()
    assert not second.acquire(timeout=0)
    first.release()
    assert second.acquire(timeout=0)
    second.release()


def test_project_launch_lock_is_stable_and_project_specific(tmp_path):
    first = tmp_path / "A" / "Game.uproject"
    same = tmp_path / "A" / "." / "Game.uproject"
    other = tmp_path / "B" / "Game.uproject"

    assert project_launch_lock_path(str(first)) == project_launch_lock_path(str(same))
    assert project_launch_lock_path(str(first)) != project_launch_lock_path(str(other))
