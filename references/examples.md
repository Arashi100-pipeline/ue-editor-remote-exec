# Examples

## Attach to an open Remote-ready project

```powershell
uv run --python 3.11 --with psutil python scripts/send_to_editor.py ensure `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject"
```

Success is based on an in-editor PID and project-directory probe, not discovery
order or window focus.

## Recover when an open Editor has Remote disabled

1. Run `ensure`; it returns `restart_required` without changing or closing anything.
2. Ask the user to save and authorize restarting that exact Editor.
3. Run:

```powershell
uv run --python 3.11 --with psutil python scripts/send_to_editor.py relaunch-remote `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject" `
    --confirm-saved
```

If a save or modal dialog still blocks exit, the command returns `needs_user` and
does not escalate to process termination.

## Inject typed variables

```powershell
uv run --python 3.11 --with psutil python scripts/send_to_editor.py inject `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject" `
    --script "X:\Automation\inspect.py" `
    --vars-json '{"LABEL":"environment review","LIMIT":25,"DRY_RUN":true}'
```

For large input, save the same JSON object to a UTF-8 file and use `--vars-file`.

## Reuse one channel for multiple isolated steps

Create `X:\Automation\plan.json`:

```json
{
  "steps": [
    {"script": "inspect.py", "vars": {"ROOT": "/Game/Environment"}},
    {"script": "write_report.py", "vars": {"FORMAT": "json"}}
  ]
}
```

Then run:

```powershell
uv run --python 3.11 --with psutil python scripts/send_to_editor.py run-plan `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject" `
    --plan "X:\Automation\plan.json"
```

The connection is reused, but names created by `inspect.py` cannot leak into
`write_report.py`.

## Request a normal close

Only after the user asks:

```powershell
uv run --python 3.11 --with psutil python scripts/send_to_editor.py close `
    --uproject "X:\Unreal\ExampleProject\ExampleProject.uproject"
```
