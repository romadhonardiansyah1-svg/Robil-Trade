# Kiro Tool Equivalents

How the action-language in Superpowers skills maps to Kiro's tools.

| Skill action | Kiro tool |
|--------------|-----------|
| Invoke / load a skill | `disclose_context` with the skill's exact `name` |
| Dispatch a subagent (implementer, reviewer, investigator) | `invoke_sub_agent` — agents: `general-task-execution`, `context-gatherer`, etc. |
| Create a todo / checklist item | Track as a task and work through them in order |
| Read a file | `read_file` / `read_files` / `read_code` |
| Edit or create a file | `fs_write`, `fs_append`, `str_replace` |
| Search code/files | `grep_search`, `file_search` |
| Run a command | `execute_pwsh` (PowerShell on this machine) |
| Start a long-running process (dev server, watcher) | `control_pwsh_process` (action `start`) |
| Check compile/lint/type errors | `get_diagnostics` |

## Subagent dispatch notes

- Pass the task brief and any requirements via the `prompt`, and attach files via `contextFiles`
  (with optional line ranges). The subagent does not inherit your conversation — give it exactly what it needs.
- Dispatch implementation subagents **one at a time**; parallel implementers editing the same tree conflict.
- Parallel dispatch is appropriate for independent, read-only investigation (use `dispatching-parallel-agents`).

## Platform notes (Windows / PowerShell)

- The bundled helper scripts (`scripts/*.sh`, `*.cjs`) are not required. Prefer the underlying git commands:
  - review diff for a range: `git diff -U10 BASE HEAD` and `git log --oneline BASE..HEAD`
  - branch start point: `git merge-base main HEAD`
- Use `;` (not `&&`) to chain PowerShell commands.
- Never run implementation directly on `main`/`master` without explicit user consent.
