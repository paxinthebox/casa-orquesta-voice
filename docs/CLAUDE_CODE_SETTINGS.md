# Claude Code Settings for this Repo

Copy this JSON to `.claude/settings.json` after cloning. The `.claude/` directory is gitignored to keep per-developer overrides local. This file is the canonical baseline.

```json
{
  "_comment": "Casa·Orquesta · Voice — Claude Code project settings",

  "rules": {
    "always_load": ["CLAUDE.md", "docs/HANDOFF.md"],
    "never_touch": [
      "../casa-orquesta-mvp/**",
      ".env",
      "*.pem",
      "*.key"
    ],
    "ask_before_modifying": [
      "services/orchestrator/tests/test_agents.py",
      ".github/workflows/**",
      "infra/**",
      "CLAUDE.md"
    ]
  },

  "verification": {
    "command": "./scripts/verify.sh",
    "must_pass_before_commit": true
  },

  "preferences": {
    "spanish_locale": "es-MX",
    "python_version": "3.11",
    "node_version": "20",
    "package_manager_python": "pip",
    "package_manager_node": "npm",
    "test_framework_python": "pytest",
    "test_framework_node": "jest",
    "linter_python": "ruff",
    "typechecker_python": "mypy",
    "linter_typescript": "eslint",
    "code_style": "match the existing surrounding code; no broad reformats"
  },

  "task_queue": "docs/TASK_PROMPTS.md",

  "escalation": {
    "stuck_file": "STUCK.md",
    "stop_and_ask_if": [
      "a test would need to change to make code pass",
      "a public API contract would change",
      "a new paid dependency would be added",
      "an LFPDPPP/NOM-151/CFDI-relevant change is needed",
      "runtime cost would increase by >$20/month",
      "you would write more than 300 lines without committing"
    ]
  }
}
```

## How to use

```bash
mkdir -p .claude
# Paste the JSON above into .claude/settings.json
```

The `.claude/` directory is in `.gitignore`, so this stays local to your machine.

## What each section does

- **`rules.always_load`** — files Claude Code loads at the start of every session.
- **`rules.never_touch`** — globs Claude Code will refuse to read/write without explicit override.
- **`rules.ask_before_modifying`** — globs Claude Code must ask about before changing.
- **`verification.command`** — what `./scripts/verify.sh` does; Claude Code runs this before declaring work done.
- **`preferences`** — house style and tools.
- **`task_queue`** — where Claude Code looks for the next task.
- **`escalation`** — when to stop and write `STUCK.md` instead of pushing through.
