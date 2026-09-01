# Open-source readiness audit

Audit date: 2026-09-01

## Result

Technically ready for a controlled public review under Apache-2.0. The distribution
contains an independent Rust transport and no Epic Games Remote Execution source
file. A publisher should still complete its own legal, ownership, and trademark
approval before making the repository public.

This was not conducted as a formal two-team clean-room legal process. The technical
review found no copied Python implementation in the deliverable, but it cannot make
a legal non-infringement determination.

## Evidence completed

- Removed the previously bundled Epic-licensed Python transport and its bytecode.
- Rust formatting, unit tests, and Clippy with warnings denied passed.
- Python lifecycle/bridge tests passed (39 tests).
- Skill metadata passed the Skill Creator `quick_validate.py` check.
- RustSec `cargo-audit` 0.22.2 scanned the locked graph (51 packages) against 1,235
  advisories and reported no vulnerabilities.
- `cargo metadata` found license expressions with a permissive distribution option;
  the Unicode identifier dependency also requires Unicode-3.0 notice handling.
- A stripped Windows release binary was built with `--locked`; its SHA-256 is stored
  in `bin/SHA256SUMS`.
- The packaged binary completed exact-project `ensure` and isolated `inject` against
  Unreal Engine 5.3 on Windows without saving a level or modifying an asset.
- Default discovery output was checked to contain node IDs only, not Unreal-provided
  machine/user/path metadata.
- Text and binary scans found no private project name, test project path, user name,
  credentials, game automation configuration, or project-specific behavior in the
  package.
- The packaged client completed exact-project execution, asset mutation, save, and
  independent read-back verification against Unreal Engine 5.7 on Windows.
- Python dependencies are locked in `uv.lock`; CI actions are pinned to full commit
  hashes; a tag-driven clean-runner release workflow packages checksums and complete
  generated Rust dependency license texts.

## Remaining release gates

- Repository owner confirms copyright ownership of all Python, Rust, documentation,
  fixtures, and earlier repository history (if any).
- Legal review confirms the interoperability/protocol provenance and trademark text.
- The first tag release completes successfully on GitHub Actions and its archive is
  manually inspected before being promoted as stable.
- UE4 receives explicit compatibility testing before it is advertised as verified.
