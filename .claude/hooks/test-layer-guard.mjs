#!/usr/bin/env node
// PreToolUse hook (Write|Edit). Enforces docs/code-conventions.md at the
// moment a test file is written, so the rule reaches every model regardless
// of what it read earlier.
//
//  - Denies CLI/pipeline wiring tests (CliRunner, run_refresh, dgi.cli,
//    dgi.pipeline) outside tests/test_cli_pipeline.py: wiring has no unit
//    spec, logic gets a UNIT test.
//  - Injects a pointer to the PIPELINE-layer contract when tests/test_cli_pipeline.py is touched.

import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(0, "utf8"));
const file = input?.tool_input?.file_path ?? "";
const text = input?.tool_input?.content ?? input?.tool_input?.new_string ?? "";

const PIPELINE_TEST = /(^|\/)tests\/test_cli_pipeline\.py$/;
const ANY_TEST = /(^|\/)tests\/[^/]+\.py$/;
const WIRING = /\bCliRunner\b|\brun_refresh\s*\(|from dgi\.(cli|pipeline) import|import dgi\.(cli|pipeline)\b|from dgi import (cli|pipeline)\b/;

const out = (hookSpecificOutput) =>
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        ...hookSpecificOutput,
      },
    }),
  );

if (PIPELINE_TEST.test(file)) {
  out({
    additionalContext:
      "Follow docs/code-conventions.md section 3, PIPELINE layer: happy path, idempotence, at most one clean-failure per command. " +
      "No data-level cases; those belong in the function's UNIT test.",
  });
} else if (ANY_TEST.test(file) && WIRING.test(text)) {
  out({
    permissionDecision: "deny",
    permissionDecisionReason:
      "docs/code-conventions.md: cli.py and pipeline.py are wiring and get no unit spec. " +
      "Test the function directly in a UNIT test beside its module's tests.",
  });
}
