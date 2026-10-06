# Token strategy

Each workflow stage ends in a written artifact. The next stage starts in a fresh session (`/clear`) that reads only that artifact, not the chat history.

## Per stage

| Stage | Model / effort | Context rule |
|---|---|---|
| Brainstorm | Opus, high | Reference only the spec and `../investment/GOLD_SCHEMA.md`. Output is a spec file. |
| Plan | Opus, medium | Read only the spec, not the code. Output is small, independent tasks. |
| Implement | Sonnet, low/medium | `/clear` per task or small batch. `@` the task's files. Read only that task's plan section. |
| Review / debug | Sonnet | Give the diff or failing output only. Use `/rewind`, not `/compact`, after a bad turn. |

Set the model once at session start. Switching mid-session breaks the prompt cache.

## Standing rules

1. **Keep always-loaded context small.** CLAUDE.md points to the spec, it doesn't `@`-import it. Never `@` the plan file. Put new workflow-specific rules in a skill or a `docs/` file, not CLAUDE.md.
2. **Split big plans by task.** If a plan section exceeds ~150 lines, split it into `plans/NN-task.md` so a session loads only its own.
3. **Keep tool output short.** `pytest -q` is already set. Use a single file or `-k` while iterating and the full suite once at the end. Never print the cache or API payloads whole. Use DuckDB queries with `LIMIT`. Pipe logs through `head`, `tail` or `grep`.
4. **Delegate noisy searches to a subagent** (e.g. "find every caller of X") to get the conclusion without the file dumps.
5. **Hand off by file, not `/compact`.** After ~30 turns or an unrelated detour, write decisions and the next step to the plan or spec, `/clear`, then continue from that file.
6. **Constrain output.** For mechanical edits: "diff only, no explanation, don't touch surrounding code."

## Checkpoints

- Run `/context` at the start of a new kind of task. If CLAUDE.md plus imports exceeds ~3k tokens, trim it.
- A session past ~30 turns on one task is the signal to hand off by file and `/clear`.

## Not worth doing here

MCP pruning (none configured), `repomix` (small repo), Haiku fan-out.
