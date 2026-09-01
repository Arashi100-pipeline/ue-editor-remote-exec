# UE Editor Remote Exec

Local-only automation for one exact Unreal Editor project. A Python lifecycle
coordinator finds or launches the Editor, while a Rust client performs
Remote Execution discovery, verifies the in-editor PID and project directory, and
executes isolated Python scripts.

## Install with one prompt

Send this to Codex:

```text
Please install this skill:
https://github.com/Arashi100-pipeline/ue-editor-remote-exec
```

The skill lives at the repository root and includes a prebuilt Windows x64 client;
normal users do not need Rust. Codex resolves the root as skill path `.` with the
installed name `ue-editor-remote-exec`, verifies the bundled executable, and prepares
the locked Python environment with `uv`. See [INSTALL.md](INSTALL.md) for the exact
installer contract, prerequisites, and manual fallback.

## Architecture

```text
SKILL.md policy and CLI examples
  -> Python lifecycle, exact-process identity, sessions, and leases
  -> Python/native JSON bridge
  -> independent Rust discovery and command transport
  -> Unreal Python Remote Execution
  -> isolated Python execution inside the verified Editor
```

The Codex skill is the policy and usage layer, while the lifecycle and transport
components handle project discovery, verification, and execution. See `PROTOCOL.md`
for the minimum interoperability sequence.

## Safety properties

- An exact `.uproject`, process ID, creation time, and executable are required.
- A Remote node is trusted only after an in-editor PID/project probe matches.
- TCP callbacks bind to an OS-assigned loopback port; multicast TTL is zero.
- No process kill, forced close, or project configuration write is implemented.
- A lost or invalid result after command dispatch becomes `outcome_unknown` and is
  never automatically retried.
- Scripts run in fresh Python namespaces; ordered plans reuse one verified TCP
  connection and stop at the first reported failure.

## Requirements

- Windows 10 or 11 and Unreal Editor with the Python plugin available.
- Python 3.11 or 3.12 plus `uv`.
- The bundled `bin/ue-remote-client.exe`. Rust 1.85+ is needed only to rebuild it.

Create the locked Python environment:

```powershell
uv sync --locked
```

Build the native client:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_native.ps1
```

## Basic use

```powershell
uv run --locked python scripts/send_to_editor.py ensure `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject"

uv run --locked python scripts/send_to_editor.py inject `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject" `
    --script "X:\Automation\inspect_assets.py"
```

Read `SKILL.md` before using lifecycle commands. Detailed CLI contracts and failure
handling are in `references/api_docs.md`, `references/examples.md`, and
`references/gotchas.md`.

## Development

```powershell
uv sync --locked --group dev
uv run --locked python -m pytest -q
cargo fmt --check --manifest-path native/ue-remote-client/Cargo.toml
cargo clippy --locked --all-targets --manifest-path native/ue-remote-client/Cargo.toml -- -D warnings
cargo test --locked --manifest-path native/ue-remote-client/Cargo.toml
```

## Compatibility evidence

| Unreal version | Platform | Verified scope |
|---|---|---|
| UE 5.3 | Windows | exact-project `ensure` and isolated read-only `inject` |
| UE 5.7 | Windows | exact-project `ensure`, asset mutation, save, and independent read-back verification |

UE4 and unlisted UE5 releases require release-specific integration validation. The
wire transport also has offline fragmentation, validation, Unicode, and safety tests.

## Security and releases

Remote Python has the same permissions as Unreal Editor. Only run trusted scripts and
keep the tool on a single trusted workstation. Read `SECURITY.md` before deployment.

Release archives contain the source, Windows native client, checksums, Apache-2.0
license, NOTICE, and generated third-party license texts. See `RELEASING.md` for the
maintainer checklist.

## License

Apache License 2.0. Third-party Rust dependencies and their license texts are recorded
in `THIRD_PARTY_NOTICES.md` and `THIRD_PARTY_LICENSES.html`.
