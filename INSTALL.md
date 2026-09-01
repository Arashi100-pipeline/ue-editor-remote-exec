# Installation

This repository is a single Codex skill. The skill root is the repository root,
not a nested directory.

## Install with Codex

Send Codex this prompt:

```text
Please install this skill:
https://github.com/Arashi100-pipeline/ue-editor-remote-exec
```

Codex should use its built-in skill installer with these resolved values:

```text
repository: Arashi100-pipeline/ue-editor-remote-exec
skill path: .
installed name: ue-editor-remote-exec
```

After the files are copied, Codex should:

1. Verify that `SKILL.md`, `pyproject.toml`, `uv.lock`, and
   `bin/ue-remote-client.exe` exist in the installed directory.
2. Verify `bin/ue-remote-client.exe` against `bin/SHA256SUMS`.
3. Check whether `uv` is available. On Windows, install it with the official
   WinGet package when it is missing:

   ```powershell
   winget install --id=astral-sh.uv -e
   ```

4. From the installed skill directory, prepare the locked Python environment and
   verify the command-line entry point:

   ```powershell
   uv sync --locked
   uv run --locked python scripts/send_to_editor.py --help
   ```

5. Tell the user that the skill becomes available to Codex on the next turn. If
   the client does not discover the newly installed skill, start a new task or
   restart Codex.

The repository ships a prebuilt Windows x64 Rust client. Do not install Rust or
compile the native client during normal installation. Rust is needed only when the
prebuilt executable is missing, its checksum is invalid, or the native source is
being changed.

## Requirements

- Windows 10 or 11 on x64.
- Network access during the first dependency sync.
- Unreal Editor with the Python Editor Script Plugin available when the skill is
  used.

Installing the skill does not modify an Unreal project. If an already-open Editor
does not have Remote Execution enabled, the skill reports `restart_required` and
waits for explicit save/restart authorization.

## Manual installation

Clone the repository directly into the user skill directory:

```powershell
git clone https://github.com/Arashi100-pipeline/ue-editor-remote-exec `
    "$env:USERPROFILE\.codex\skills\ue-editor-remote-exec"
Set-Location "$env:USERPROFILE\.codex\skills\ue-editor-remote-exec"
uv sync --locked
```

If `CODEX_HOME` is set, install under `$env:CODEX_HOME\skills` instead.
