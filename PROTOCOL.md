# Protocol interoperability notes

This client implements the minimum wire behavior needed for Unreal Python Remote
Execution. Message names, field names, endpoint defaults, and request/response
behavior are isolated in `native/ue-remote-client/src/protocol.rs`.

## Transport sequence

1. Send a version-1 `ping` JSON datagram to the local multicast group.
2. Collect addressed `pong` messages and retain node IDs only by default.
3. Bind a TCP listener to `127.0.0.1:0` and send `open_connection` to a candidate.
4. Accept loopback peers only, then switch the accepted socket to blocking mode and
   enforce explicit read/write deadlines.
5. Send a read-only identity probe. Accept the node only when its in-editor process
   ID and canonical project directory equal the expected OS process identity.
6. Send one `command`, or ordered commands on the same verified connection.
7. Reassemble fragmented TCP JSON up to 16 MiB, validate source/destination/version,
   and parse the typed `command_result`.
8. Send `close_connection` after completion or known failure.

The public client enforces a loopback multicast bind, loopback TCP callback, and
multicast TTL zero. A node hint only changes candidate order; it is never proof.

## Outcome boundary

Before a business command is sent, discovery, connection, and identity failures are
known failures and may be retried by a caller. Once a business command may have been
written to TCP, timeout, EOF, malformed response, wrong destination, or disconnect is
reported as `outcome_unknown`. The client does not retry that command.

## Privacy

Unreal discovery metadata may expose local paths, machine names, or user names.
`discover` returns node IDs only unless `--include-metadata` is explicitly supplied.
No discovery payload is written to the repository or fixtures.
