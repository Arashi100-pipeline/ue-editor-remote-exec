# Security policy

## Supported scope

This tool is designed for a single Windows workstation whose Unreal Editor and
automation caller share the same trust boundary. It is not a remote administration
server and must not be exposed across a LAN, VPN, container bridge, or the internet.

## Enforced boundaries

- TCP callback listeners bind to loopback and accept loopback peers only.
- Multicast bind addresses must be loopback and multicast TTL must remain zero.
- Exact process and in-editor identities must agree before business code is sent.
- Response size and all network phases have finite limits.
- The lifecycle layer never terminates or force-closes the Editor.
- Discovery metadata is redacted from default CLI output.

Remote Python execution is intentionally powerful: a submitted script has the same
permissions as Unreal Editor. Only execute trusted, reviewed scripts. Do not pass
untrusted paths, plans, variables, or source code to this tool.

## Reporting

Use GitHub private vulnerability reporting from the repository's **Security** tab.
If private reporting is unavailable, ask the maintainer for a private channel before
sharing details; do not open a public issue containing sensitive reproduction data.

Do not include project paths, user names, host names, session JSON, proprietary asset
names, or scripts in a public report. Include the client version, Unreal version,
status code, and a redacted reproduction. Treat `outcome_unknown` as potentially
executed and inspect Editor/project state before any retry.

## Release checklist

- Run Rust tests, Clippy with warnings denied, Python tests, and dependency audit.
- Build with `--locked` from `Cargo.lock`.
- Scan source and artifacts for credentials, absolute paths, and private names.
- Publish checksums for prebuilt binaries and preserve `LICENSE` plus third-party
  license notices.
