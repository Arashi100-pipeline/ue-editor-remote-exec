# Releasing

1. Confirm ownership and provenance for every tracked file and the full Git history.
2. Run the commands in `CONTRIBUTING.md` and a dependency vulnerability audit.
3. Regenerate `THIRD_PARTY_LICENSES.html` from the locked Rust dependency graph:

   ```powershell
   cargo about generate scripts/licenses.hbs --locked --fail `
       --manifest-path native/ue-remote-client/Cargo.toml `
       --output-file THIRD_PARTY_LICENSES.html
   ```
4. Test `ensure` and a read-only `inject` against each Unreal version advertised in
   the compatibility table.
5. Update `CHANGELOG.md`, `OPEN_SOURCE_AUDIT.md`, and the version in `pyproject.toml`
   plus `native/ue-remote-client/Cargo.toml`.
6. Run `scripts/package_release.ps1 -Version <version>` and inspect the archive.
7. Create and push an annotated `v<version>` tag. The release workflow rebuilds the
   native client on a clean Windows runner and publishes the archive and checksums.

Never release from a working tree containing proprietary scripts, local session data,
or an unreviewed prebuilt binary.
