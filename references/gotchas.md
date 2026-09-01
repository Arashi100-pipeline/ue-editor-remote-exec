# Gotchas

## G001 — Open project has no Remote Execution node

**Symptom:** `ensure` returns `restart_required` while the exact project is visibly open.

**Cause:** Python Remote Execution is disabled, the Python plugin is unavailable,
or UDP discovery cannot reach the Editor.

**Action:** Ask the user to save and authorize restart. Only after that confirmation,
run `relaunch-remote --confirm-saved`; it requests normal close and starts the exact
project with process-only Remote flags. Never change the ini, terminate the process,
or tell the user that their unsaved work is safe to discard.

## G002 — Same project has multiple Editor instances

**Symptom:** `ensure` returns `needs_user` and lists multiple PIDs.

**Cause:** More than one process command line contains the same exact `.uproject`.

**Action:** Ask the user to leave one instance open. Do not choose by process order.

## G003 — Remote node changed after an Editor reload

**Symptom:** The stored `node_id_hint` is absent.

**Action:** Rediscover nodes and verify project directory plus PID. Never fall back
to the first node. The session is updated only after verification.

## G004 — Normal close is waiting for the user

**Symptom:** `close` or `relaunch-remote` returns `needs_user` after a normal
window-close request.

**Cause:** Unreal is showing a save/discard dialog or another modal prompt.

**Action:** Let the user decide in the Editor. Preserve the session. Never call
`terminate`, `kill`, or a force-close API.

## G005 — Local Remote Execution channel is busy

**Symptom:** a command returns `needs_user` after waiting for the local
Remote Execution channel.

**Cause:** Another request owns the machine-level transport lock. Each TCP callback
uses an OS-assigned loopback port, but Remote sessions are intentionally serialized
to avoid Unreal multi-client races.

**Action:** Let the active request finish, then retry. Do not delete the lock file;
the operating system releases its lock automatically if the owning process exits.

## G006 — Remote command result is unknown

**Symptom:** Injection reports a timeout, connection loss, EOF, or invalid response
after command sending may have begun.

**Cause:** Unreal did not return a trustworthy final result. The script may still have
completed, may still be running, or may have failed.

**Action:** Inspect the Editor and resulting assets before deciding what to do. Never
automatically retry a mutating script with an unknown outcome, because that could
apply it twice.

## G007 — Unsafe UE4.26 bulk vertex-color probe

`EditorStaticMeshLibrary.has_vertex_colors` may crash on malformed or incomplete
UE4.26 mesh render data. Do not use it as an unguarded bulk prefilter. Prefer a
defensive project API that validates render data and returns per-component results.

## G008 — A process-only Remote port override is ignored

**Symptom:** The Editor command line contains the intended multicast endpoint, but
Remote discovery still uses the saved port.

**Cause:** Unreal's `-ini` parser requires every comma-separated property to repeat
its section: `-ini:Engine:[Section]:A=1,[Section]:B=2`. Writing
`[Section]:A=1,B=2` applies only the first property.

**Action:** Repeat `[/Script/PythonScriptPlugin.PythonScriptPluginSettings]:`
before every Remote Execution property. Verify the effective port by discovering
and probing the exact Editor PID; do not rely on the Project Settings display.

## G009 — A later script unexpectedly calls an earlier `main`

**Symptom:** A top-level-only step appears to invoke a `main()` function from a
previous Remote command.

**Cause:** The caller bypassed this skill's isolated wrapper and executed directly in
Unreal Remote Execution's persistent global namespace.

**Action:** Use `inject` or `run-plan`. Do not strip main guards, append `main()`, or
inject variables into the Remote global namespace. Each skill-managed step compiles
unchanged source in a fresh namespace and clears it afterward.
