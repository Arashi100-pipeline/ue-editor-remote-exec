# Contributing

Thank you for helping improve UE Editor Remote Exec.

## Development setup

- Windows 10 or 11
- Python 3.11 and `uv`
- Rust 1.85 or newer with `rustfmt` and Clippy

Install the locked Python environment and run the checks:

```powershell
uv sync --locked --group dev
uv run --locked python -m pytest -q
cargo fmt --check --manifest-path native/ue-remote-client/Cargo.toml
cargo clippy --locked --all-targets --manifest-path native/ue-remote-client/Cargo.toml -- -D warnings
cargo test --locked --manifest-path native/ue-remote-client/Cargo.toml
```

## Safety invariants

Changes must preserve these boundaries unless a proposal explicitly explains and
reviews a safer replacement:

- require an exact `.uproject` and verify PID plus in-editor project identity;
- keep multicast binding and TCP callbacks on loopback with multicast TTL zero;
- never force-kill Unreal Editor or silently edit project configuration;
- never automatically retry a command reported as `outcome_unknown`;
- do not add proprietary project paths, assets, scripts, or discovery metadata.

## Pull requests

Keep changes focused, add meaningful tests, and update user-facing documentation when
CLI contracts or safety behavior change. Do not commit generated caches, local Unreal
projects, session files, or `native/ue-remote-client/target`.

By contributing, you agree that your contribution is licensed under Apache-2.0.
