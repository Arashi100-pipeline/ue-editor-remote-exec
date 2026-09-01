"""
send_to_editor.py — CLI entry point for ue-editor-remote-exec.

Exact-project workflow:

  STEP 1 — Attach to the exact project, or launch it when closed:
    uv run --python 3.11 --with psutil python scripts/send_to_editor.py ensure \\
        --uproject "X:\\Unreal\\ExampleProject\\ExampleProject.uproject"

  STEP 2 — Inject a script (repeat as many times as needed):
    uv run --python 3.11 --with psutil python scripts/send_to_editor.py inject \\
        --script "X:\\Automation\\inspect.py"

    uv run --python 3.11 --with psutil python scripts/send_to_editor.py inject \\
        --script "X:\\Automation\\report.py" \\
        --vars-json '{"KEY":"value"}'

  Use run-plan when several ordered scripts should reuse one verified channel.

  If ensure reports restart_required, relaunch only after explicit confirmation:
    uv run --python 3.11 --with psutil python scripts/send_to_editor.py relaunch-remote \\
        --uproject "X:\\Unreal\\ExampleProject\\ExampleProject.uproject" --confirm-saved

  STEP 3 — Request a normal close when the user explicitly asks:
    uv run --python 3.11 --with psutil python scripts/send_to_editor.py close

See references/api_docs.md for full argument reference.
See references/examples.md for copy-paste examples.
See references/gotchas.md for known issues.
"""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from main_processor import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
