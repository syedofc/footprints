const assert = require('node:assert/strict');
const test = require('node:test');
const {
  discover, describePermissions, scanRequest, summarizeFindings, filterFindings, installationLabel, sampleEvidence,
} = require('./discovery.js');

test('finds distinct tool, runtime, and model results in the sample', () => {
  assert.deepEqual(discover(sampleEvidence).map(result => result.id), [
    'ollama-tool', 'ollama-runtime', 'demo-model',
  ]);
});

test('deduplicates equivalent process names', () => {
  const result = discover([
    { kind: 'process', value: 'ollama.exe' },
    { kind: 'process', value: '  OLLAMA.EXE  ' },
  ]);
  assert.equal(result.length, 1);
  assert.equal(result[0].evidence, 'ollama.exe');
});

test('ignores near matches, the wrong evidence type, and malformed entries', () => {
  assert.deepEqual(discover([
    { kind: 'process', value: 'ollama-helper.exe' },
    { kind: 'runtime', value: 'ollama.exe' },
    { kind: 'process', value: 42 },
    null,
  ]), []);
});

const ids = evidence => discover(evidence).map(result => result.id);

test('matches process names the same way on Windows, macOS and Linux', () => {
  for (const value of ['ollama', 'ollama.exe', 'C:\\Program Files\\Ollama\\ollama.exe', '/usr/local/bin/ollama']) {
    assert.deepEqual(ids([{ kind: 'process', value }]), ['ollama-tool'], value);
  }
  assert.deepEqual(ids([{ kind: 'Process', value: 'ollama' }]), ['ollama-tool']);
  assert.deepEqual(ids([{ kind: 'process', value: '/opt/ollama-exporter/ollama-exporter' }]), []);
});

test('counts every matching signal but reports each finding once', () => {
  const [result] = discover([
    { kind: 'process', value: 'ollama' },
    { kind: 'process', value: 'ollama.exe' },
  ]);
  assert.equal(result.matches, 2);
  assert.equal(result.evidence, 'ollama');
});

test('tells the Claude desktop app apart from Claude Code sessions', () => {
  // The desktop app's main process is also named "claude".
  assert.deepEqual(ids([{ kind: 'process', value: 'Claude', app: 'com.anthropic.claudefordesktop', parent: 'launchd' }]),
    ['claude-desktop']);
  assert.deepEqual(ids([{ kind: 'app', value: 'com.anthropic.claudefordesktop' }]), ['claude-desktop']);

  // Sessions started by the desktop app, VS Code, Cursor or a terminal are Claude Code.
  for (const session of [
    { app: 'com.anthropic.claude-code', parent: 'com.anthropic.claudefordesktop' },
    { parent: 'com.microsoft.VSCode' },
    { parent: 'com.todesktop.230313mzl4w4u92' },
    { parent: 'zsh' },
    { parent: '/bin/bash' },
  ]) {
    assert.deepEqual(ids([{ kind: 'process', value: 'claude', ...session }]), ['claude-code'], JSON.stringify(session));
  }
  assert.deepEqual(ids([{ kind: 'editor-extension', value: 'anthropic.claude-code', editor: 'vscode' }]), ['claude-code']);
});

test('does not guess when a "claude" process has no telling context', () => {
  assert.deepEqual(ids([{ kind: 'process', value: 'claude' }]), []);
  // Windows desktop app: same name, started by Explorer, no bundle ID.
  assert.deepEqual(ids([{ kind: 'process', value: 'claude.exe', parent: 'explorer.exe' }]), []);
  assert.deepEqual(ids([{ kind: 'process', value: 'claude-helper', parent: 'zsh' }]), []);
});

test('finds Claude in Chrome by extension ID, native host or bridge process', () => {
  for (const item of [
    { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', browser: 'chrome', profile: 'Default' },
    { kind: 'native-host', value: 'com.anthropic.claude_browser_extension' },
    { kind: 'process', value: 'chrome-native-host', app: 'com.anthropic.claudefordesktop', parent: 'com.google.Chrome' },
  ]) {
    assert.deepEqual(ids([item]), ['claude-in-chrome'], item.kind);
  }
  // Another extension ID, or another app's chrome-native-host, is not Claude.
  assert.deepEqual(ids([{ kind: 'browser-extension', value: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }]), []);
  assert.deepEqual(ids([{ kind: 'process', value: 'chrome-native-host', app: 'com.example.passwords' }]), []);
});

test('finds AI editor extensions in any VS Code-family editor, but not similar IDs', () => {
  assert.deepEqual(ids([{ kind: 'editor-extension', value: 'GitHub.copilot-chat', editor: 'cursor' }]), ['github-copilot']);
  assert.deepEqual(ids([{ kind: 'editor-extension', value: 'continue.continue' }]), ['continue']);
  assert.deepEqual(ids([{ kind: 'editor-extension', value: 'github.copilot-theme' }]), []);
});

// Evidence as the device scan reports it: identical items are folded into `count`.
const scannedDevice = [
  { kind: 'process', value: 'Claude', app: 'com.anthropic.claudefordesktop', parent: 'launchd', count: 1 },
  { kind: 'process', value: 'claude', app: 'com.anthropic.claude-code', parent: 'com.anthropic.claudefordesktop', count: 5 },
  { kind: 'process', value: 'chrome-native-host', app: 'com.anthropic.claudefordesktop', parent: 'com.google.Chrome', count: 2 },
  { kind: 'process', value: 'claude', parent: 'com.microsoft.VSCode', count: 1 },
  { kind: 'app', value: 'com.anthropic.claudefordesktop', version: '4.16336.0' },
  { kind: 'editor-extension', value: 'anthropic.claude-code', editor: 'vscode', version: '2.1.319' },
  { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', browser: 'chrome', profile: 'Default', version: '1.0.106' },
  { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', browser: 'chrome', profile: 'Profile 9', version: '1.0.106' },
  { kind: 'native-host', value: 'com.anthropic.claude_browser_extension', browser: 'chrome' },
];

test('adds up the count of each scanned item', () => {
  const byId = Object.fromEntries(discover(scannedDevice).map(result => [result.id, result.matches]));
  assert.deepEqual(byId, { 'claude-desktop': 2, 'claude-code': 7, 'claude-in-chrome': 5 });
  assert.equal(discover([{ kind: 'process', value: 'ollama', count: 0 }])[0].matches, 1);
  assert.equal(discover([{ kind: 'process', value: 'ollama', count: '3' }])[0].matches, 1);
});

test('explains each result with one line per kind of evidence', () => {
  const reasons = Object.fromEntries(discover(scannedDevice).map(result => [result.id, result.reasons]));
  assert.deepEqual(reasons, {
    'claude-desktop': ['Running now', 'Installed, version 4.16336.0'],
    'claude-code': ['5 sessions inside Claude desktop', '1 session started by VS Code', 'Extension 2.1.319 in VS Code'],
    'claude-in-chrome': [
      '2 live bridges from Chrome to Claude desktop',
      'Installed in 2 Chrome profiles',
      'Native messaging host registered for Chrome',
    ],
  });
});

test('explains the simulated sample and falls back to the raw value', () => {
  assert.deepEqual(discover(sampleEvidence).map(result => result.reasons), [
    ['Running (2 processes)'],
    ['API answered at ollama:api/tags'],
    ['Model file demo-model.gguf'],
  ]);
  const custom = [{ id: 'x', label: 'X', kind: 'Test', signals: [{ kind: 'app', value: 'com.example.x' }] }];
  assert.deepEqual(discover([{ kind: 'app', value: 'com.example.x', count: 3 }], custom)[0].reasons,
    ['com.example.x (×3)']);
  assert.deepEqual(discover([{ kind: 'app', value: 'com.todesktop.230313mzl4w4u92' }])[0].reasons,
    ['Installed, version unknown']);
});

test('reports an AI app as running only when its main program runs', () => {
  const installed = { kind: 'app', value: 'ai.perplexity.macv3', version: '26.42.0' };
  const main = { kind: 'process', value: 'Perplexity', app: 'ai.perplexity.macv3', parent: 'launchd', count: 1 };
  const updater = { kind: 'process', value: 'Autoupdate', app: 'ai.perplexity.macv3', parent: 'launchd', count: 1 };

  assert.deepEqual(discover([installed, updater])[0].reasons, ['Installed, version 26.42.0']);
  assert.deepEqual(discover([main, updater, installed])[0].reasons, ['Running now', 'Installed, version 26.42.0']);

  const cursor = discover([
    { kind: 'process', value: 'Cursor', app: 'com.todesktop.230313mzl4w4u92', parent: 'launchd' },
    { kind: 'process', value: 'Cursor Helper (Renderer)', app: 'com.todesktop.230313mzl4w4u92', parent: 'Cursor' },
  ]);
  assert.deepEqual(cursor.map(result => [result.id, result.reasons, result.matches]), [['cursor', ['Running now'], 1]]);
  // A process merely named "Perplexity" outside the app's bundle is not the app.
  assert.deepEqual(ids([{ kind: 'process', value: 'perplexity', parent: 'zsh' }]), []);
});

test('finds Grok Bot by its bundle ID, which does not contain its name', () => {
  assert.deepEqual(discover([{ kind: 'app', value: 'com.anysphere.sand', version: '0.78.3' }])
    .map(result => [result.id, result.reasons]), [['grok-bot', ['Installed, version 0.78.3']]]);
  assert.deepEqual(discover([{ kind: 'process', value: 'Grok Bot', app: 'com.anysphere.sand', parent: 'launchd' }])[0].reasons,
    ['Running now']);
  assert.deepEqual(ids([{ kind: 'process', value: 'Grok Bot Helper (Renderer)', app: 'com.anysphere.sand' }]), []);
  assert.deepEqual(ids([{ kind: 'app', value: 'com.anysphere.sandbox' }]), []);
});

test('collects the permissions of every matching item into the finding', () => {
  const [chrome] = discover([
    { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', profile: 'Default',
      permissions: ['extension:debugger', 'hosts:all-sites', 'extension:storage'] },
    { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', profile: 'Profile 9',
      permissions: ['extension:debugger', 'extension:tabs'] },
  ]);
  assert.deepEqual(chrome.permissions, ['extension:debugger', 'extension:storage', 'extension:tabs', 'hosts:all-sites']);
  assert.deepEqual(discover([{ kind: 'app', value: 'ai.perplexity.macv3' }])[0].permissions, []);
});

test('describes permissions in plain words, broadest reach first', () => {
  const { lines, other } = describePermissions([
    'entitlement:device.camera', 'extension:tabs', 'sandbox:off', 'entitlement:cs.allow-jit',
    'granted:screen-recording', 'granted:camera', 'sandbox:on',
  ]);
  assert.deepEqual(lines.map(line => [line.text, line.level]), [
    ['Allowed: Screen Recording', 'high'],
    ['Not sandboxed: can read and change your files', 'high'],
    ['Allowed: camera', 'medium'],
    ['Can see your open tabs', 'medium'],
    ['May ask for the camera', 'low'],
  ]);
  assert.equal(other, 2); // cs.allow-jit and sandbox:on have no plain-language line
  assert.deepEqual(describePermissions(undefined), { lines: [], other: 0 });
});

test('MCP servers and permission rules add context but never create a finding', () => {
  const config = [
    { kind: 'mcp-server', value: 'docs', client: 'claude-desktop', scope: 'user', transport: 'stdio' },
    { kind: 'mcp-server', value: 'github', client: 'claude-desktop', scope: 'user', transport: 'http' },
    { kind: 'agent-rules', value: 'claude-code', scope: 'user', mode: 'default', allow: '0', ask: '0', deny: '0' },
  ];
  assert.deepEqual(discover(config), []);

  const [desktop] = discover([...config, { kind: 'app', value: 'com.anthropic.claudefordesktop', version: '4.1' }]);
  assert.equal(desktop.matches, 1);
  assert.deepEqual(desktop.reasons, ['Installed, version 4.1', '2 MCP servers: docs, github']);

  // An MCP server of another client is not attached to Claude desktop.
  const [other] = discover([{ kind: 'app', value: 'com.anthropic.claudefordesktop', version: '4.1' },
    { kind: 'mcp-server', value: 'db', client: 'cursor', scope: 'user' }]);
  assert.deepEqual(other.reasons, ['Installed, version 4.1']);
});

test('keeps one permission-rule line per scope for Claude Code', () => {
  const [claudeCode] = discover([
    { kind: 'editor-extension', value: 'anthropic.claude-code', editor: 'vscode', version: '2.1.319' },
    { kind: 'agent-rules', value: 'claude-code', scope: 'user', mode: 'acceptEdits', allow: '3', ask: '0', deny: '1' },
    { kind: 'agent-rules', value: 'claude-code', scope: 'managed', mode: 'default', allow: '0', ask: '2', deny: '5' },
    { kind: 'mcp-server', value: 'notes', client: 'claude-code', scope: 'project', transport: 'stdio' },
  ]);
  assert.deepEqual(claudeCode.reasons, [
    'Extension 2.1.319 in VS Code',
    'Permission rules (user): mode acceptEdits, 3 allow, 0 ask, 1 deny',
    'Permission rules (managed): mode default, 0 allow, 2 ask, 5 deny',
    '1 MCP server (project): notes',
  ]);
});

test('confirms a tool when its program is signed by the expected publisher', () => {
  const [desktop] = discover([
    { kind: 'app', value: 'com.anthropic.claudefordesktop', version: '4.1', signer: 'Q6L2SF6YDW' },
    { kind: 'process', value: 'Claude', app: 'com.anthropic.claudefordesktop', signer: 'Q6L2SF6YDW' },
  ]);
  assert.deepEqual(desktop.verification, { status: 'confirmed', text: 'Signed by Anthropic PBC (Q6L2SF6YDW)' });
  assert.deepEqual(desktop.reasons, ['Installed, version 4.1', 'Running now']);
});

test('flags a copied bundle ID that is unsigned or signed by someone else', () => {
  const [unsigned] = discover([{ kind: 'app', value: 'com.anthropic.claudefordesktop', version: '9.9', signer: 'unsigned' }]);
  assert.equal(unsigned.verification.status, 'unverified');
  assert.equal(unsigned.verification.text, 'Unverified: 1 item is not signed by Anthropic PBC');
  assert.deepEqual(unsigned.reasons, ['Installed, version 9.9 · ⚠ unsigned program, publisher unknown']);

  const [otherTeam] = discover([{ kind: 'app', value: 'com.anthropic.claudefordesktop', signer: 'ABCDE12345' }]);
  assert.deepEqual(otherTeam.reasons, ['Installed, version unknown · ⚠ signed by team ABCDE12345, not Anthropic PBC']);

  const [broken] = discover([{ kind: 'process', value: 'Claude', app: 'com.anthropic.claudefordesktop', signer: 'invalid' }]);
  assert.deepEqual(broken.reasons, ['Running now · ⚠ signature does not verify']);
});

test('reproduces the audit: an unsigned program named "ollama" is not trusted', () => {
  // A copy of another program saved as /tmp/ollama has no valid signature.
  const [fake] = discover([{ kind: 'process', value: '/tmp/ollama', parent: 'zsh', signer: 'unsigned' }]);
  assert.equal(fake.id, 'ollama-tool');
  assert.equal(fake.verification.status, 'unverified');
  assert.deepEqual(fake.reasons, ['Running (1 process) · ⚠ unsigned program, publisher unknown']);

  // A signed Ollama whose team is not in the catalog yet is reported as such, not as confirmed.
  const [signed] = discover([{ kind: 'process', value: 'ollama', signer: 'TEAM123456' }]);
  assert.deepEqual(signed.verification, {
    status: 'signed', text: 'Signed by team TEAM123456; publisher not in the catalog yet' });
});

test('a fake next to the real tool still makes the result unverified', () => {
  const [claudeCode] = discover([
    { kind: 'process', value: 'claude', app: 'com.anthropic.claude-code', parent: 'com.anthropic.claudefordesktop',
      signer: 'Q6L2SF6YDW', count: 5 },
    { kind: 'process', value: 'claude', parent: 'zsh', signer: 'unsigned', count: 1 },
  ]);
  assert.equal(claudeCode.verification.status, 'unverified');
  assert.deepEqual(claudeCode.reasons, [
    '5 sessions inside Claude desktop',
    '1 session started from a terminal (zsh) · ⚠ unsigned program, publisher unknown',
  ]);
});

test('evidence without a signature to check is reported as not checked', () => {
  assert.deepEqual(discover([{ kind: 'editor-extension', value: 'continue.continue' }])[0].verification,
    { status: 'not-checked', text: 'Publisher not checked' });
  // Linux and Windows scans carry no signer at all.
  assert.equal(discover([{ kind: 'process', value: 'ollama' }])[0].verification.status, 'not-checked');
});

test('asks the scan only for command and package names the catalog knows', () => {
  const request = scanRequest();
  for (const name of ['claude', 'copilot', 'codex', 'gemini', 'aider', 'ollama', 'opencode', 'cursor-agent', 'qwen']) {
    assert.ok(request.cli.includes(name), name);
  }
  assert.ok(request.packages.includes('@anthropic-ai/claude-code'));
  assert.ok(request.packages.includes('aider-chat'));
  assert.deepEqual(request.cli, [...new Set(request.cli)].sort());
  const custom = [{ id: 'x', label: 'X', kind: 'T', signals: [{ kind: 'cli', value: ['b', 'a'] }, { kind: 'app', value: 'c' }] }];
  assert.deepEqual(scanRequest(custom), { cli: ['a', 'b'], packages: [] });
});

test('finds installed CLI tools and confirms their publisher', () => {
  const results = discover([
    { kind: 'cli', value: 'claude', signer: 'Q6L2SF6YDW', count: 1 },
    { kind: 'package', value: '@anthropic-ai/claude-code', manager: 'npm', version: '2.1.287' },
    { kind: 'cli', value: 'copilot', signer: 'VEKTX9H2N7', count: 1 },
  ]);
  const byId = Object.fromEntries(results.map(result => [result.id, result]));
  assert.deepEqual(byId['claude-code'].reasons,
    ['Command "claude" installed', 'Installed as npm package @anthropic-ai/claude-code (version 2.1.287)']);
  assert.equal(byId['claude-code'].verification.status, 'confirmed');
  assert.deepEqual(byId['copilot-cli'].verification, { status: 'confirmed', text: 'Signed by GitHub (VEKTX9H2N7)' });
});

test('flags a fake CLI and never confirms tools without a known publisher', () => {
  const [fake] = discover([{ kind: 'cli', value: 'copilot', signer: 'unsigned' }]);
  assert.equal(fake.verification.status, 'unverified');
  assert.deepEqual(fake.reasons, ['Command "copilot" installed · ⚠ unsigned program, publisher unknown']);

  const [codex] = discover([{ kind: 'cli', value: 'codex', signer: 'ABCDE12345' }]);
  assert.equal(codex.verification.status, 'signed');

  const [aider] = discover([{ kind: 'package', value: 'aider-chat', manager: 'pipx' }]);
  assert.equal(aider.id, 'aider');
  assert.deepEqual(aider.reasons, ['Installed as pipx package aider-chat (version unknown)']);
  assert.equal(aider.verification.status, 'not-checked');
});

test('reports node and python sessions of CLI tools as running', () => {
  const results = discover([
    { kind: 'interpreted-process', value: '@google/gemini-cli', interpreter: 'node', parent: 'zsh', count: 2 },
    { kind: 'interpreted-process', value: 'aider-chat', interpreter: 'python', parent: 'com.microsoft.VSCode', count: 1 },
    { kind: 'cli', value: 'gemini', signer: 'script' },
  ]);
  const byId = Object.fromEntries(results.map(result => [result.id, result]));
  assert.deepEqual(byId['gemini-cli'].reasons,
    ['2 sessions running on node (started by zsh)', 'Command "gemini" installed']);
  assert.deepEqual(byId.aider.reasons, ['1 session running on python (started by VS Code)']);
  // A script shim has no signature to check: not flagged as unsigned.
  assert.equal(byId['gemini-cli'].verification.status, 'not-checked');
  assert.deepEqual(ids([{ kind: 'interpreted-process', value: 'gemini', interpreter: 'node' }]), ['gemini-cli']);
  assert.deepEqual(ids([{ kind: 'interpreted-process', value: 'some-other-tool', interpreter: 'node' }]), []);
});

test('marks a tool as running only when a live process matched it', () => {
  const byId = Object.fromEntries(discover([
    { kind: 'app', value: 'ai.perplexity.macv3' },
    { kind: 'app', value: 'com.todesktop.230313mzl4w4u92' },
    { kind: 'process', value: 'Cursor', app: 'com.todesktop.230313mzl4w4u92' },
    { kind: 'interpreted-process', value: 'aider-chat', interpreter: 'python' },
  ]).map(result => [result.id, result.running]));
  assert.deepEqual(byId, { perplexity: false, cursor: true, aider: true });
});

const deviceView = () => discover([
  { kind: 'app', value: 'com.anthropic.claudefordesktop', signer: 'Q6L2SF6YDW', permissions: ['sandbox:off'] },
  { kind: 'process', value: 'Claude', app: 'com.anthropic.claudefordesktop', signer: 'Q6L2SF6YDW' },
  { kind: 'cli', value: 'copilot', signer: 'unsigned' },
  { kind: 'editor-extension', value: 'continue.continue' },
  { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', permissions: ['hosts:all-sites'] },
]);

test('summarizes a device scan for the tiles', () => {
  assert.deepEqual(summarizeFindings(deviceView()), { found: 4, running: 1, verified: 1, unverified: 1, broad: 2 });
  assert.deepEqual(summarizeFindings([]), { found: 0, running: 0, verified: 0, unverified: 0, broad: 0 });
});

test('filters and orders device results: unverified first, then running', () => {
  const findings = deviceView();
  assert.deepEqual(filterFindings(findings).map(f => f.id),
    ['copilot-cli', 'claude-desktop', 'claude-in-chrome', 'continue']);
  assert.deepEqual(filterFindings(findings, 'running').map(f => f.id), ['claude-desktop']);
  assert.deepEqual(filterFindings(findings, 'broad').map(f => f.id), ['claude-desktop', 'claude-in-chrome']);
  assert.deepEqual(filterFindings(findings, 'all', 'EXTENSION').map(f => f.id), ['claude-in-chrome', 'continue']);
  assert.deepEqual(filterFindings(findings, 'unknown-filter').length, 4);
});

test('lists each installation of a tool that can get a passport', () => {
  const [claudeCode] = discover([
    { kind: 'cli', value: 'claude', signer: 'Q6L2SF6YDW' },
    { kind: 'cli', value: 'claude', signer: 'Q6L2SF6YDW' },                      // same installation twice
    { kind: 'package', value: '@anthropic-ai/claude-code', manager: 'npm', version: '2.1.287' },
    { kind: 'editor-extension', value: 'anthropic.claude-code', editor: 'vscode', version: '2.1.322' },
    { kind: 'process', value: 'claude', parent: 'zsh' },                          // running, not an installation
  ]);
  assert.deepEqual(claudeCode.installations, [
    { kind: 'cli', value: 'claude', signer: 'Q6L2SF6YDW' },
    { kind: 'editor-extension', value: 'anthropic.claude-code', version: '2.1.322', editor: 'vscode' },
  ]);
  assert.equal(claudeCode.packageVersion, '2.1.287');
  const [chrome] = discover([
    { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', browser: 'chrome', profile: 'Default', version: '1.0.106' },
    { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn', browser: 'chrome', profile: 'Profile 9', version: '1.0.106' },
  ]);
  assert.deepEqual(chrome.installations.map(installation => installation.profile), ['Default', 'Profile 9']);
  assert.deepEqual(discover([{ kind: 'process', value: 'Cursor', app: 'com.todesktop.230313mzl4w4u92' }])[0].installations, []);
});

test('names installations for people', () => {
  assert.equal(installationLabel({ kind: 'app', value: 'x' }), 'App');
  assert.equal(installationLabel({ kind: 'cli', value: 'claude' }), 'Command "claude"');
  assert.equal(installationLabel({ kind: 'editor-extension', value: 'x', editor: 'vscode' }), 'VS Code extension');
  assert.equal(installationLabel({ kind: 'browser-extension', value: 'x', browser: 'chrome', profile: 'Default' }),
    'Chrome extension (Default)');
});

test('CLI and package names must match exactly', () => {
  assert.deepEqual(ids([{ kind: 'cli', value: 'claude-helper' }]), []);
  assert.deepEqual(ids([{ kind: 'package', value: 'claude-code' }]), []); // unscoped look-alike
  assert.deepEqual(ids([{ kind: 'package', value: '@openai/codex-sdk' }]), []);
});

test('finds AI apps by bundle ID, not by display name', () => {
  assert.deepEqual(ids([{ kind: 'app', value: 'com.todesktop.230313mzl4w4u92' }]), ['cursor']);
  assert.deepEqual(ids([{ kind: 'app', value: 'ai.perplexity.macv3' }]), ['perplexity']);
  assert.deepEqual(ids([{ kind: 'app', value: 'Cursor' }]), []);
});
