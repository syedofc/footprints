# Repository tracker

**Scan repositories** lists the git repositories on this device and the AI agent configuration in them. Each scan is saved locally, so the next scan shows what changed.

Repository names, paths, remote hosts and configuration names can identify a private project. Use the simulated discovery sample for public screenshots. The local view hides these results when printed. Keep raw history and passport JSON private.

## Where it looks

- **Folders agents and editors know:** Claude Code's projects (`~/.claude.json`) and the "recently opened" folders of VS Code, VS Code Insiders and Cursor. A project folder inside a repository counts as that repository.
- **Common development folders,** up to 4 levels deep: `~/Projects`, `~/code`, `~/src`, `~/dev`, `~/Developer`, `~/repos`, `~/git`, `~/GitHub`, `~/Documents/GitHub`, `~/workspace`, `~/work`, `~/Sites`.
  - Hidden folders, `node_modules`, virtual environments, build output and `~/Library` are skipped.
  - The search stops after 20,000 folders.
  - Repositories inside repositories aren't listed separately.

## What it records per repository

| Field | Source | Example |
|---|---|---|
| `path` | Location, relative to the home folder | `~/Projects/app` |
| `origin` | Remote `origin` from `.git/config`, as host/owner/repo. Credentials, query and fragment are removed; `local` for a path remote | `git.example.com/team/app` |
| `cloned_at`, `how` | Timestamp and action of the **first** reflog entry (`.git/logs/HEAD`) | `cloned` or `created here` |
| `last_active`, `last_action` | The **last** reflog entry | `2026-11-10T15:47:13+00:00`, `commit` |
| `ai.files` | Which agent files exist (never their text) | `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.windsurfrules`, `.github/copilot-instructions.md`, `.aider.conf.yml`, Continue and Gemini settings |
| `ai.folders` | Number of files in agent folders | `.claude/agents`, `.claude/commands`, `.cursor/rules`, `.github/instructions` |
| `ai.mcp_servers` | Server **names** in `.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json` | `github (.mcp.json)` |
| `agents` | Per agent tool: its config files and a SHA-256 **fingerprint** over their names and contents. Personal `*.local.*` files are left out | `Claude Code: CLAUDE.md, .mcp.json · cf58f851024a` |
| `ai.claude_rules` | Permission mode and **number** of allow/ask/deny rules in `.claude/settings.json` and `.claude/settings.local.json` | `mode default, 1 allow, 0 ask, 2 deny` |

**What stays out of the scan result:**
- Source files and commit contents.
- Author names, emails and reflog message bodies. The scan keeps timestamps and an action label.
- Agent file contents. The scan reads them locally to calculate fingerprints.
- MCP commands, arguments, environment values and permission rule text. The scan parses local configuration and keeps names, modes and counts.

## History

Scans are saved to `repos-history.json` in the Footprints registry folder (default `~/.forkit/footprints`, set with `--registry-root`). The last 50 scans are kept, and each save is atomic.

Running `python repos.py` prints a repository count and creates no scan history.

From the second scan on, the result lists repositories that are:
- **new:** found now but not last time;
- **not found:** found last time but not now (moved, deleted, or outside the searched folders);
- **changed:** origin, last activity, AI configuration, or the contents of agent files (fingerprint) changed.

Agent setups can be given a Forkit Core passport; see [passports.md](passports.md).

## Limits

- A repository is reported once, even if it's reachable under two names. Matching is by folder identity, e.g. `~/Projects` and `~/projects` on macOS.
- Without a reflog (e.g. `git clone --no-reflog` setups or copied folders), the clone time is unknown; `last_active` then falls back to the `.git` folder's modification time.
- Repositories in other folders are found only if Claude Code or an editor has opened them.
- Explicit network share paths are skipped. Keep scan locations on local disks if your system maps network storage to a drive or mount point.
