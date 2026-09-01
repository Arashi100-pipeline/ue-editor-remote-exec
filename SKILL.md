---
name: ue-editor-remote-exec
description: Launch or safely attach to one exact UE4/UE5 .uproject in GUI mode and execute Python through Unreal Remote Execution. Use for sending scripts to a running Unreal Editor, repeated GUI-session injections, asset operations in a visible editor, or requests mentioning Unreal Remote Execution, 远程执行, GUI 模式虚幻, or 持续注入脚本. The skill verifies the editor by project path and PID and never force-kills it.
---

# UE Editor Remote Exec

Use this lifecycle for one exact GUI Editor:

```text
ensure -> inject or run-plan -> inject ... -> close only when requested
```

`ensure` attaches when Remote Execution is already available and launches the exact
project with process-local Remote flags when it is closed. One request may use
`run-plan` to reuse a verified channel across ordered steps. Every submitted script
still runs in a fresh Python namespace. Discovery and command transport use the
independent Rust client in `native/ue-remote-client`; do not restore or vendor Epic's
Python Remote Execution implementation.

## Installation and first run

For installation or repair requests, read `INSTALL.md`. This repository is one
skill rooted at `.` and should be installed as `ue-editor-remote-exec`. Use the
bundled `bin/ue-remote-client.exe`, verify it with `bin/SHA256SUMS`, and prepare
Python dependencies with `uv sync --locked`. Do not install Rust or rebuild the
native client unless the executable is missing, fails checksum verification, or
the user is changing native source.

## Safety rules

- Require the exact `.uproject` path. Never select the first Unreal process or Remote node.
- Accept a node only after an in-editor probe matches the project directory and PID.
- Never terminate or kill Unreal Editor, and never edit project ini files to recover Remote Execution.
- When the exact project is open without Remote Execution, report `restart_required`. Run `relaunch-remote --confirm-saved` only after the user explicitly confirms the project is saved and authorizes the restart.
- A relaunch uses `WM_CLOSE`, waits for normal exit, and leaves save/modal decisions to the user. If the Editor does not exit, report `needs_user` and stop.
- If the same project has multiple Editor instances, ask the user to leave only one.
- Treat `outcome_unknown` as non-retriable until the user or caller inspects Editor state.
- Call `close` only when the user explicitly asks to close the Editor.

## Ensure a controllable Editor

Prepare `uv` and ensure the checksum-verified `bin/ue-remote-client.exe` exists.
Then run from this skill directory:

```powershell
foreach ($p in @("$env:USERPROFILE\.local\bin","$env:USERPROFILE\.cargo\bin","$env:LOCALAPPDATA\uv\bin")) { if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) { $env:PATH = "$p;$env:PATH" } };

uv run --locked python scripts/send_to_editor.py ensure `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject"
```

Read the final JSON line. Outcomes are `succeeded`, `restart_required`,
`needs_user`, `failed`, or `outcome_unknown`.

If `ensure` returns `restart_required`, do not restart automatically. After explicit
save/restart confirmation:

```powershell
uv run --locked python scripts/send_to_editor.py relaunch-remote `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject" `
    --confirm-saved
```

The relaunch flags affect only that process. They do not alter Project Settings.

## Execute scripts

Use JSON variables so spaces, Unicode, booleans, arrays, and nested objects retain
their types:

```powershell
uv run --locked python scripts/send_to_editor.py inject `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject" `
    --script "X:\Automation\inspect_assets.py" `
    --vars-json '{"ROOT":"/Game/Environment","DRY_RUN":true}'
```

Scripts may contain top-level code, a callable `main()`, or a conventional
`if __name__ == "__main__"` guard. The wrapper does not rewrite source. It executes
top-level code once and calls a callable `main()` exactly once in an isolated
namespace, then clears that namespace.

For several ordered steps, prefer one plan so transport discovery and identity
verification happen once:

```powershell
uv run --locked python scripts/send_to_editor.py run-plan `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject" `
    --plan "X:\Automation\plan.json"
```

The plan schema and timeout options are in `references/api_docs.md`. TCP callback
listeners use OS-assigned loopback ports. Sessions store identity and a node hint,
never a live socket or callback port.

## Close only when requested

```powershell
uv run --locked python scripts/send_to_editor.py close `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject"
```

If a modal dialog prevents exit, preserve the session and report `needs_user`.
The legacy `--force` spelling grants no termination authority.

## References and tests

Read `references/api_docs.md` for the CLI, result contract, session schema, and
configuration. Read `references/examples.md` for copyable flows and
`references/gotchas.md` for failure handling.

```powershell
uv sync --locked --group dev
uv run --locked python -m pytest -q
cargo test --locked --manifest-path native/ue-remote-client/Cargo.toml
```
