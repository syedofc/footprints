# Passports

Footprints creates Forkit Core agent passports for two things:
- **AI tools found by the device scan:** one passport per installation, e.g. "Claude Code, command-line tool, v2.1.287, published by Anthropic PBC".
- **Agent setups found by the repository scan:** e.g. "the Claude Code setup of `~/Projects/app`, v1.0.0, vouched for by Jane".

## Tool passports

### Create one

**Device** tab → **Scan this device**. On a tool's card, under **Passports**, choose **Create passport** next to an installation: an app, a command, or an editor or browser extension.

The page sends only the tool's identifier (bundle ID, command name or extension ID). The local service finds the program itself, so file paths never travel to the page.

### What a tool passport contains

| Field | Value |
|---|---|
| `name` | Tool and installation type, followed by a hash of its local identifier (including browser profile when applicable) |
| `version` | The **detected** version (app or extension metadata). Command-line tools have no version metadata, so you enter it, prefilled from the npm package when known |
| `creator` | For a program with a valid code signature, the **publisher from the signing certificate**, e.g. "Anthropic PBC", never typed. Otherwise the person who vouches for it, and the passport records that (`creator_source: user`). A program whose signature doesn't verify gets no passport |
| `artifact_hash` | Fingerprint of the installed program, see below |
| `model_id` | The provider model: Anthropic Claude, GitHub, Google Gemini, Cursor, OpenAI, Perplexity, xAI Grok, or "user-configured" |
| `parent_agent_id` | The nearest earlier registered version of the same installation |

### Fingerprints

| Installation | Fingerprinted | Strength |
|---|---|---|
| App | Main executable and `_CodeSignature/CodeResources`, or `Info.plist` if the seal is absent | The resource list is measured as a file. Resource contents are not individually checked |
| Command | The discovered program file | For an npm or pip shim, this measures the shim rather than its entire package |
| Editor extension | `package.json` | Extension code and publisher signature are not checked |
| Browser extension | `manifest.json` and `_metadata/verified_contents.json` when present | These files are hashed. The list's signature and extension code are not checked |

### Checks

| Check | Result |
|---|---|
| **Installation fingerprint** | Measured files match or differ. A detected version change is shown separately. Missing and unreadable evidence have their own statuses |
| **Publisher** | On macOS, Footprints checks the program's code signature and recorded signing team, without checking bundle resources. An invalid signature, a different signing identity, and an unavailable check are separate results. User vouching is a recorded claim |

Creating a passport for a changed program under an existing version number is refused, because that's exactly what a replaced program looks like.

## Repository agent passports

### Create one

1. **Repositories** tab → **Scan repositories**.
2. On a repository card, under **Agent setups**, choose **Create passport** next to a tool.
3. Enter a version (`1.0` or `1.0.0`) and who vouches for the setup, then **Create agent passport**.

Passports can only be created for repositories found by the latest scan.

### What a repository passport contains

| Field | Value |
|---|---|
| `name` | Repository folder name and agent tool, followed by a hash of the local checkout path and tool |
| `version`, `creator` | What you entered. No `v` prefix: Forkit Core's own check would accept `v1.0.0` and create a second identity for the same release |
| `artifact_hash` | **Fingerprint** of the tool's config files: SHA-256 over their names and contents. Personal `*.local.*` files are left out, so the same commit gives the same fingerprint everywhere |
| `model_id` | A **provider model** passport for the agent's model family: Anthropic Claude, GitHub Copilot, Google Gemini, Cursor, or "user-configured". These have no artifact hash, because the models run at the provider |
| `tools` | MCP server names configured in the setup's files |
| `parent_agent_id` | The nearest earlier registered version of the same checkout and tool |
| `metadata.context` | Repository path and origin, tool, and the names of the fingerprinted files |

**Never in a passport:** the text of the agent files, MCP commands or secrets, or the text of permission rules.

Files per tool:

| Tool | Files |
|---|---|
| Claude Code | `CLAUDE.md`, `.claude/settings.json`, `.claude/agents/*`, `.claude/commands/*`, `.mcp.json` |
| Cursor | `.cursorrules`, `.cursor/rules/*`, `.cursor/mcp.json` |
| GitHub Copilot | `.github/copilot-instructions.md`, `.github/instructions/*`, `.vscode/mcp.json` |
| Gemini CLI | `GEMINI.md`, `.gemini/settings.json` |
| AGENTS.md agents | `AGENTS.md` |
| Aider, Continue, Windsurf | `.aider.conf.yml`, `.continue/config.*`, `.windsurfrules` |

Forkit Core derives the passport ID from name, version, creator and fingerprint. Footprints distinguishes local checkouts even if their names and file contents match. Moving a checkout changes its local locator. Existing passport files keep their IDs and lineage. Re-registering the same version from the same local locator can reuse a matching legacy passport.

**No overwriting:**
- Creating the same passport again returns the existing one.
- Reusing a version for a changed setup is refused, and a new version is suggested (e.g. `1.1.0`).

## Checking passports

The **Passports** tab checks every passport in the local registry (`~/.forkit/footprints`):

| Check | Result |
|---|---|
| **Record ID** | Core recomputes the ID from the canonical identity fields. This check does not cover fields such as display labels, descriptions, or lineage pointers |
| **Setup** (agent passports) | ✓ unchanged: the files still match the fingerprint. ⚠ changed: they were edited, so create a new version. ⚠ agent files removed. Repository not found |

A passport is a record of what a setup was and who vouched for it. It's not a claim that the setup is safe.

## Local storage and sharing

Footprints stores passport JSON, the registry index, lineage, and registry change records in the local registry. Model records can contain a selected filename and its hash. Tool records keep installation identifiers, browser profiles where applicable, the measured file roles, fingerprints, signing team, and creator information. Repository records can contain local paths, sanitized remote origins, config filenames, server names, and fingerprints. The latest 50 repository scans can include names, paths, sanitized remotes, activity times, and agent configuration summaries. The browser remembers the last vouched-for name in local storage. Clear the browser's site data to remove that name.

Use the bundled synthetic sample and a separate registry for public examples. Footprints hides live results in print and does not expose Core's bulk export or sync routes. Screenshots and copies of local data made outside the app still need review before sharing.
