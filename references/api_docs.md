# API reference

## CLI

```text
python scripts/send_to_editor.py ensure --uproject <path> [launch timeouts]
python scripts/send_to_editor.py launch --uproject <path> [launch timeouts]
python scripts/send_to_editor.py relaunch-remote --uproject <path> [--confirm-saved] [launch/close timeouts]
python scripts/send_to_editor.py inject --script <path> [--vars-json <object> | --vars-file <path> | --inject "K=V"] [remote timeouts]
python scripts/send_to_editor.py run-plan --plan <path> [remote timeouts]
python scripts/send_to_editor.py close [--uproject <path>] [--session-file <path>] [--close-timeout <seconds>] [--force]
```

`launch` is retained as an alias for `ensure`. Both attach to an exact open,
Remote-ready project or launch a closed project with process-only flags. An open
project without a verifiable Remote node returns `restart_required`.

`relaunch-remote` first re-probes the exact project. If Remote is already ready it
attaches and skips restart. Otherwise it requires `--confirm-saved`, sends only a
normal window-close request, waits for the exact PID to exit, and relaunches with
process-local flags. It never terminates a process or edits project ini files.

`inject` accepts top-level-only scripts and scripts defining `main()`. Source is
compiled unchanged inside a new namespace whose `__name__` is `ue_remote_step`.
A callable `main` is invoked once after top-level execution; the namespace is then
cleared. `--vars-json` and `--vars-file` require a JSON object. The legacy string-only
`--inject` syntax is supported for compatibility but cannot be combined with either.

`run-plan` keeps one verified Remote channel for ordered steps. Its JSON schema is:

```json
{
  "steps": [
    {"script": "inspect.py", "vars": {"ROOT": "/Game/Environment"}},
    {"script": "report.py", "vars": {"OUTPUT": "C:/reports/result.json"}}
  ]
}
```

Relative script paths resolve from the plan file. Each step has a separate script
namespace. Execution stops after the first Remote-reported failure. A command whose
final outcome is unknown is never retried automatically.

Common lookup options are `--uproject` and `--session-file`. Remote options are
`--connect-timeout` and `--command-timeout`. Launch options are
`--wait-exe-timeout`, `--wait-existing-node-timeout`, and
`--wait-launched-node-timeout`. All timeout values must be positive.

## Result contract

Every CLI action prints one final compact JSON object after any diagnostic lines:

```json
{"result_version":1,"status":"succeeded","action":"ensure","message":"Editor Remote session is ready","data":{}}
```

Statuses and exit codes:

| Status | Exit | Meaning |
|---|---:|---|
| `succeeded` | 0 | Requested action completed |
| `failed` | 1 | Validation, connection, or script-reported failure |
| `restart_required` | 2 | Exact open Editor needs a user-authorized restart |
| `needs_user` | 2 | A modal, duplicate process, lock, or other user decision blocks progress |
| `outcome_unknown` | 3 | Command may have run; inspect state and do not auto-retry |

Consumers should parse the last non-empty stdout line and branch on `status`, not
on localized diagnostic text.

## Session schema

Default: `%LOCALAPPDATA%\ue-editor-remote-exec\sessions\<sha256>.json`, where the
hash comes from the normalized absolute `.uproject` path. Set
`UE_REMOTE_SESSION_ROOT` to place session files elsewhere.

| Field | Meaning |
|---|---|
| `schema_version` | Current schema version (`2`) |
| `project` | Exact absolute `.uproject` path |
| `pid` | Verified Editor PID |
| `create_time` | Process creation time; prevents PID reuse |
| `executable` | Exact Editor executable path |
| `node_id_hint` | Last verified Remote node; a hint, never proof |
| `ownership` | `attached` or `launched`; informational only |
| `last_verified` | UTC timestamp of the latest successful identity verification |

Sessions hold no socket or port. Each CLI request rediscovers and verifies identity.
`run-plan` reuses a connection only inside its process. Machine-level transport and
project lifecycle locks prevent conflicting requests.

The coordinator locates the native client in this order: `UE_REMOTE_CLIENT_EXE`,
`bin/ue-remote-client.exe`, then a local Rust release build under
`native/ue-remote-client/target/release`. The first location is useful for development;
public packages should ship a checksummed binary in `bin`.

## Process-local Remote settings

Defaults match Unreal Python Remote Execution local multicast behavior. The multicast
group and loopback interface can be overridden without changing project files:

| Environment variable | Default |
|---|---|
| `UE_REMOTE_MULTICAST_GROUP_ENDPOINT` | `239.0.0.1:6766` |
| `UE_REMOTE_MULTICAST_BIND_ADDRESS` | `127.0.0.1` |
| `UE_REMOTE_MULTICAST_TTL` | `0` |

Unsafe ini delimiters are rejected. The group must be IPv4 multicast, the bind
address must be loopback, and TTL must remain zero. The Python launcher and Rust
client consume the same group/bind environment values.

## Python API

```python
from main_processor import launch_session, inject_script, run_script_steps

project = r"X:\Unreal\ExampleProject\ExampleProject.uproject"
session = launch_session(project)
ok, output = inject_script(
    r"X:\Automation\inspect.py",
    inject_vars={"ROOT": "/Game/Environment"},
    uproject=project,
)
results = run_script_steps(
    [
        {"script": r"X:\Automation\step1.py", "vars": {}},
        {"script": r"X:\Automation\step2.py", "vars": {"DRY_RUN": True}},
    ],
    uproject=project,
)
```
