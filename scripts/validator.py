"""Fast path validation for project attachment and script injection."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PreflightResult:
    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def report_lines(self) -> list[str]:
        lines = []
        for label, passed, message in self.checks:
            status = "OK  " if passed else "FAIL"
            lines.append(f"[preflight] {status} {label}: {message}")
        return lines


def preflight_check(uproject: str) -> PreflightResult:
    """Validate the exact project path required for safe editor binding."""

    project = Path(uproject)
    if not project.exists():
        check = ("uproject", False, f"not found: {project}")
    elif project.suffix.lower() != ".uproject":
        check = (
            "uproject",
            False,
            f"expected .uproject, got '{project.suffix}': {project}",
        )
    else:
        check = ("uproject", True, str(project))
    return PreflightResult(ok=check[1], checks=[check])


def preflight_check_script(script: str) -> PreflightResult:
    """Validate one Python script immediately before injection."""

    path = Path(script)
    if not path.exists():
        check = ("script", False, f"not found: {path}")
    elif path.suffix.lower() != ".py":
        check = (
            "script",
            False,
            f"expected .py, got '{path.suffix}': {path}",
        )
    else:
        check = ("script", True, str(path))
    return PreflightResult(ok=check[1], checks=[check])
