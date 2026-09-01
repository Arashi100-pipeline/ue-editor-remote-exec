# Third-party notices

The native client uses Rust crates resolved by `native/ue-remote-client/Cargo.lock`.
The shipped Windows dependency graph is composed of crates offered under permissive
licenses, primarily `MIT OR Apache-2.0`; some crates use `MIT`, `Apache-2.0 OR MIT`,
or `Unlicense OR MIT`. `unicode-ident` additionally carries `Unicode-3.0` terms.

Direct dependencies:

| Crate | License expression |
|---|---|
| clap | MIT OR Apache-2.0 |
| serde | MIT OR Apache-2.0 |
| serde_json | MIT OR Apache-2.0 |
| socket2 | MIT OR Apache-2.0 |
| thiserror | MIT OR Apache-2.0 |
| uuid | Apache-2.0 OR MIT |

Exact versions and transitive packages are locked in `Cargo.lock`. Their source,
license files, and repository URLs are available from crates.io and `cargo metadata`.
Redistributors of a prebuilt binary should preserve this file and the license texts
required by the selected license option for each locked crate.

`THIRD_PARTY_LICENSES.html` is generated from that locked graph with `cargo-about`
and is included with binary releases. The generation template in
`scripts/licenses.hbs` is adapted from the cargo-about template, copyright 2020-2024
Embark Studios, and is available under MIT OR Apache-2.0.
