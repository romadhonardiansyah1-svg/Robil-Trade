---
inclusion: always
---

# Superpowers — Agentic Development Methodology

This workspace has the **Superpowers** skills framework installed (by Jesse Vincent / Prime Radiant,
MIT licensed). The skills live in `.kiro/skills/` and are a complete, opinionated software-development
methodology. This steering file bootstraps that methodology and maps it onto Kiro's tooling.

## The Core Rule

**Before any response or action, check whether a skill applies — and if it does, activate it FIRST.**
Even a 1% chance a skill applies means you activate it to check. If the skill turns out not to fit, you
don't have to follow it. Activate a skill with the `disclose_context` tool (pass the skill's exact
`name`). This is Kiro's equivalent of Claude Code's `Skill` tool.

If a skill applies to your task, using it is not optional.

### Instruction Priority

1. **The user's explicit instructions** (direct requests, other steering files) — highest priority.
2. **Superpowers skills** — override default behavior where they conflict.
3. **Default behavior** — lowest priority.

If the user says "don't use TDD" and a skill says "always use TDD," follow the user.

## When to Activate (quick triggers)

- About to build a feature, component, or any new behavior → activate **brainstorming** first
  (refine intent and design before writing code). Do not write code until the design is approved.
- Have an approved design / spec for multi-step work → activate **writing-plans**.
- Ready to execute a plan in this session → activate **subagent-driven-development**;
  for a separate/parallel session with human checkpoints → activate **executing-plans**.
- Need an isolated workspace before feature work → activate **using-git-worktrees**.
- Implementing any feature or bugfix → activate **test-driven-development** (RED-GREEN-REFACTOR).
- Hit a bug, test failure, or unexpected behavior → activate **systematic-debugging** before proposing fixes.
- 2+ independent tasks with no shared state → activate **dispatching-parallel-agents**.
- Finishing a task / before merge → activate **requesting-code-review**, then **verification-before-completion**.
- Receiving review feedback → activate **receiving-code-review**.
- Implementation complete, tests pass → activate **finishing-a-development-branch**.
- Creating or editing a skill → activate **writing-skills**.

**Skill priority when several apply:** process skills first (brainstorming, systematic-debugging)
to decide HOW to approach, then implementation skills.

**Rigid skills** (TDD, systematic-debugging): follow exactly, don't adapt away the discipline.
**Flexible skills** (patterns): adapt the principles to context. The skill tells you which it is.

## Kiro Tool Mapping

The skill files speak in actions ("invoke a skill", "dispatch a subagent", "create a todo"). On Kiro:

| Skill says | Use on Kiro |
|------------|-------------|
| "Use the Skill tool" / "invoke the skill" | `disclose_context` with the skill `name` |
| "Dispatch a subagent" / "implementer / reviewer subagent" | `invoke_sub_agent` (e.g. `general-task-execution`), one task at a time, with curated context |
| "Create a todo per item" | Track each checklist item as a task and work through them |
| "Read this first — it is your requirements" | Pass the brief/spec via the sub-agent `prompt` and `contextFiles` |
| Shell scripts (`scripts/*.sh`, `*.cjs`) | Optional helpers; on Windows/PowerShell, prefer the equivalent `git` commands directly |

Notes for this Windows + PowerShell workspace:
- The skills' bundled `.sh` helper scripts won't run natively. Use the underlying `git` commands
  (`git diff`, `git log`, `git merge-base`) directly via `execute_pwsh`, or run scripts through `bash` only if available.
- Never start implementation on `main`/`master` without explicit consent; prefer a worktree or feature branch.
- Dispatch implementation sub-agents one at a time (parallel implementers conflict). Parallel is fine
  for independent read-only investigation.

## Red Flags (you are rationalizing — stop and check for a skill)

"This is just a simple question" · "I need more context first" · "Let me explore the codebase first" ·
"This doesn't need a formal skill" · "I remember this skill" · "The skill is overkill" ·
"I'll just do this one thing first". In every one of these cases: check for and activate the relevant skill first.

To see the full methodology start by activating the **using-superpowers** skill.
