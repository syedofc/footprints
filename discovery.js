// Evidence format (see docs/evidence-format.md):
//   { kind, value, app?, parent?, count?, ...extra }
// kind    what was observed: process, app, editor-extension, browser-extension,
//         native-host, runtime, model-file
// value   the observed identifier (process name, bundle ID, extension ID, ...)
// app     for a process: bundle ID of the app it lives in (macOS)
// parent  for a process: bundle ID or name of the process that started it
// count   how many identical items were observed (default 1)
const sampleEvidence = [
  { kind: 'process', value: 'ollama.exe' },
  { kind: 'process', value: 'OLLAMA.EXE' },
  { kind: 'process', value: 'ollama-helper.exe' },
  { kind: 'runtime', value: 'ollama:api/tags' },
  { kind: 'model-file', value: 'demo-model.gguf' },
];

const SHELLS = ['zsh', 'bash', 'fish', 'sh', 'pwsh', 'powershell', 'cmd', 'tmux'];

// Expected code-signing publisher (Apple Team ID, from `codesign -dv`). Names and
// IDs can be copied by anyone; a valid signature from this team cannot.
const ANTHROPIC = { name: 'Anthropic PBC', teams: ['Q6L2SF6YDW'] };

const CLI_EXPLAIN = 'Command "{value}" installed';
const PACKAGE_EXPLAIN = 'Installed as {manager} package {value} (version {version})';
const RUNNING_EXPLAIN = '{count} session{s} running on {interpreter} (started by {parent})';

// A finding matches when ANY of its signals matches one evidence item.
// A signal matches when every field it names matches (arrays mean "any of").
// `explain` says why the signal counts, for people. Placeholders: {count},
// {s}/{es} (plural endings), and any evidence field such as {version},
// {editor}, {browser}, {parent} or {value}.
// `attachOnly` signals add context (MCP servers, permission rules) to a finding
// that other signals found, but never create or count as a finding themselves:
// configuration files can outlive the tool. `groupBy` keeps one line per value
// of that evidence field instead of merging them.
const catalog = [
  {
    id: 'ollama-tool', label: 'Ollama', kind: 'AI tool',
    signals: [
      { kind: 'process', value: 'ollama', explain: 'Running ({count} process{es})' },
      { kind: 'cli', value: 'ollama', explain: CLI_EXPLAIN },
    ],
  },
  {
    id: 'ollama-runtime', label: 'Ollama API', kind: 'Runtime',
    signals: [{ kind: 'runtime', value: 'ollama:api/tags', explain: 'API answered at {value}' }],
  },
  {
    id: 'demo-model', label: 'Demo model', kind: 'Model file',
    signals: [{ kind: 'model-file', value: 'demo-model.gguf', explain: 'Model file {value}' }],
  },
  {
    id: 'claude-desktop', label: 'Claude desktop app', kind: 'AI app',
    publisher: ANTHROPIC,
    signals: [
      { kind: 'app', value: 'com.anthropic.claudefordesktop', explain: 'Installed, version {version}' },
      { kind: 'process', value: 'claude', app: 'com.anthropic.claudefordesktop', explain: 'Running now' },
      { kind: 'mcp-server', client: 'claude-desktop', attachOnly: true, explain: '{count} MCP server{s}: {value}' },
    ],
  },
  {
    // The process name "claude" alone is ambiguous: the desktop app's main
    // process has the same name. Only where it lives or who started it tells.
    id: 'claude-code', label: 'Claude Code', kind: 'Coding agent',
    publisher: ANTHROPIC,
    signals: [
      { kind: 'editor-extension', value: 'anthropic.claude-code', explain: 'Extension {version} in {editor}' },
      { kind: 'process', value: 'claude', app: 'com.anthropic.claude-code',
        explain: '{count} session{s} inside Claude desktop' },
      { kind: 'process', value: 'claude', parent: ['com.microsoft.vscode', 'com.todesktop.230313mzl4w4u92'],
        explain: '{count} session{s} started by {parent}' },
      { kind: 'process', value: 'claude', parent: SHELLS,
        explain: '{count} session{s} started from a terminal ({parent})' },
      { kind: 'cli', value: 'claude', explain: CLI_EXPLAIN },
      { kind: 'package', value: '@anthropic-ai/claude-code', explain: PACKAGE_EXPLAIN },
      { kind: 'interpreted-process', value: ['@anthropic-ai/claude-code', 'claude'], explain: RUNNING_EXPLAIN },
      { kind: 'mcp-server', client: 'claude-code', attachOnly: true, groupBy: 'scope',
        explain: '{count} MCP server{s} ({scope}): {value}' },
      { kind: 'agent-rules', value: 'claude-code', attachOnly: true, groupBy: 'scope',
        explain: 'Permission rules ({scope}): mode {mode}, {allow} allow, {ask} ask, {deny} deny' },
    ],
  },
  {
    id: 'claude-in-chrome', label: 'Claude in Chrome', kind: 'Browser extension',
    publisher: ANTHROPIC,
    signals: [
      { kind: 'browser-extension', value: 'fcoeoabgfenejglbffodgkkbkcdhcgfn',
        explain: 'Installed in {count} {browser} profile{s}' },
      { kind: 'native-host', value: 'com.anthropic.claude_browser_extension',
        explain: 'Native messaging host registered for {browser}' },
      { kind: 'process', value: 'chrome-native-host', app: 'com.anthropic.claudefordesktop',
        explain: '{count} live bridge{s} from {parent} to Claude desktop' },
    ],
  },
  {
    id: 'github-copilot', label: 'GitHub Copilot', kind: 'Editor extension',
    signals: [{ kind: 'editor-extension', value: ['github.copilot', 'github.copilot-chat'],
      explain: 'Extension {value} {version} in {editor}' }],
  },
  {
    id: 'continue', label: 'Continue', kind: 'Editor extension',
    signals: [{ kind: 'editor-extension', value: 'continue.continue', explain: 'Extension {version} in {editor}' }],
  },
  // "Running now" matches only the app's main executable (CFBundleExecutable),
  // not helpers or updaters that live in the same bundle.
  {
    id: 'cursor', label: 'Cursor', kind: 'AI editor',
    publisher: { name: 'Hilary Stout', teams: ['VDXQ22DGB9'] },
    signals: [
      { kind: 'app', value: 'com.todesktop.230313mzl4w4u92', explain: 'Installed, version {version}' },
      { kind: 'process', value: 'cursor', app: 'com.todesktop.230313mzl4w4u92', explain: 'Running now' },
      { kind: 'mcp-server', client: 'cursor', attachOnly: true, explain: '{count} MCP server{s}: {value}' },
    ],
  },
  {
    // Display name "Grok Bot", but the bundle ID does not mention Grok: the app
    // is signed by Anysphere Incorporated (team DCNK4UB866) as com.anysphere.sand.
    id: 'grok-bot', label: 'Grok Bot', kind: 'AI app',
    publisher: { name: 'Anysphere Incorporated', teams: ['DCNK4UB866'] },
    signals: [
      { kind: 'app', value: 'com.anysphere.sand', explain: 'Installed, version {version}' },
      { kind: 'process', value: 'grok bot', app: 'com.anysphere.sand', explain: 'Running now' },
    ],
  },
  {
    // Verified on a real device: Homebrew cask, signed by GitHub.
    id: 'copilot-cli', label: 'GitHub Copilot CLI', kind: 'Coding agent',
    publisher: { name: 'GitHub', teams: ['VEKTX9H2N7'] },
    signals: [
      { kind: 'cli', value: 'copilot', explain: CLI_EXPLAIN },
      { kind: 'process', value: 'copilot', parent: SHELLS, explain: '{count} session{s} started from a terminal ({parent})' },
    ],
  },
  // Well-known command and package names whose publishers have not been verified
  // on a device yet: they can be "signed" or "unverified", never "confirmed".
  // Most of these run on Node or Python: the scan attributes a running node/python
  // process to them only when its command line points at their package or command.
  {
    id: 'codex-cli', label: 'OpenAI Codex CLI', kind: 'Coding agent',
    signals: [
      { kind: 'cli', value: 'codex', explain: CLI_EXPLAIN },
      { kind: 'package', value: '@openai/codex', explain: PACKAGE_EXPLAIN },
      { kind: 'interpreted-process', value: ['@openai/codex', 'codex'], explain: RUNNING_EXPLAIN },
    ],
  },
  {
    id: 'gemini-cli', label: 'Gemini CLI', kind: 'Coding agent',
    signals: [
      { kind: 'cli', value: 'gemini', explain: CLI_EXPLAIN },
      { kind: 'package', value: '@google/gemini-cli', explain: PACKAGE_EXPLAIN },
      { kind: 'interpreted-process', value: ['@google/gemini-cli', 'gemini'], explain: RUNNING_EXPLAIN },
    ],
  },
  {
    id: 'aider', label: 'Aider', kind: 'Coding agent',
    signals: [
      { kind: 'cli', value: 'aider', explain: CLI_EXPLAIN },
      { kind: 'package', value: 'aider-chat', explain: PACKAGE_EXPLAIN },
      { kind: 'interpreted-process', value: ['aider-chat', 'aider'], explain: RUNNING_EXPLAIN },
    ],
  },
  {
    id: 'opencode', label: 'OpenCode', kind: 'Coding agent',
    signals: [
      { kind: 'cli', value: 'opencode', explain: CLI_EXPLAIN },
      { kind: 'package', value: 'opencode-ai', explain: PACKAGE_EXPLAIN },
      { kind: 'interpreted-process', value: ['opencode-ai', 'opencode'], explain: RUNNING_EXPLAIN },
    ],
  },
  {
    id: 'cursor-agent-cli', label: 'Cursor Agent CLI', kind: 'Coding agent',
    signals: [{ kind: 'cli', value: 'cursor-agent', explain: CLI_EXPLAIN }],
  },
  {
    id: 'qwen-code', label: 'Qwen Code', kind: 'Coding agent',
    signals: [
      { kind: 'cli', value: 'qwen', explain: CLI_EXPLAIN },
      { kind: 'package', value: '@qwen-code/qwen-code', explain: PACKAGE_EXPLAIN },
      { kind: 'interpreted-process', value: ['@qwen-code/qwen-code', 'qwen'], explain: RUNNING_EXPLAIN },
    ],
  },
  {
    id: 'perplexity', label: 'Perplexity', kind: 'AI app',
    publisher: { name: 'Perplexity AI Inc.', teams: ['7S8W4W365S'] },
    signals: [
      { kind: 'app', value: 'ai.perplexity.macv3', explain: 'Installed, version {version}' },
      { kind: 'process', value: 'perplexity', app: 'ai.perplexity.macv3', explain: 'Running now' },
    ],
  },
];

// Readable names for identifiers that appear in explanations.
const DISPLAY_NAMES = {
  'com.microsoft.vscode': 'VS Code',
  'com.todesktop.230313mzl4w4u92': 'Cursor',
  'com.anthropic.claudefordesktop': 'Claude desktop',
  'com.google.chrome': 'Chrome',
  vscode: 'VS Code',
  'vscode-insiders': 'VS Code Insiders',
  cursor: 'Cursor',
  vscodium: 'VSCodium',
  windsurf: 'Windsurf',
  chrome: 'Chrome',
  edge: 'Edge',
  brave: 'Brave',
  arc: 'Arc',
  chromium: 'Chromium',
};
const EXPLAIN_FIELDS = ['value', 'version', 'editor', 'browser', 'profile', 'parent', 'app',
  'client', 'scope', 'transport', 'mode', 'allow', 'ask', 'deny', 'manager', 'interpreter'];

// What a permission lets a tool reach, in plain words. `high` marks broad reach.
// Entitlements are what an app DECLARES it may ask for; `granted:` is what the
// user allowed in macOS Privacy & Security.
const PERMISSIONS = {
  'sandbox:off': ['Not sandboxed: can read and change your files', 'high'],
  'hosts:all-sites': ['Can read and change every website', 'high'],
  'extension:debugger': ['Can take control of browser tabs (debugger)', 'high'],
  'content-scripts:all-sites': ['Runs its scripts on every page', 'high'],
  'extension:cookies': ['Can read and change cookies', 'high'],
  'extension:history': ['Can read browsing history', 'high'],
  'extension:nativemessaging': ['Talks to a program on this computer', 'medium'],
  'extension:identity': ['Can use your browser sign-in', 'medium'],
  'extension:downloads': ['Can download and manage files', 'medium'],
  'extension:tabs': ['Can see your open tabs', 'medium'],
  'extension:scripting': ['Can inject scripts into pages', 'medium'],
  'extension:webnavigation': ['Sees which pages you visit', 'medium'],
  'extension:clipboardread': ['Can read the clipboard', 'medium'],
  'hosts:some-sites': ['Can read and change some websites', 'medium'],
  'content-scripts:some-sites': ['Runs its scripts on some sites', 'medium'],
  'entitlement:automation.apple-events': ['May ask to control other apps', 'medium'],
  'entitlement:device.camera': ['May ask for the camera', 'low'],
  'entitlement:device.audio-input': ['May ask for the microphone', 'low'],
  'entitlement:personal-information.location': ['May ask for your location', 'low'],
  'entitlement:personal-information.addressbook': ['May ask for your contacts', 'low'],
  'entitlement:personal-information.calendars': ['May ask for your calendars', 'low'],
  'entitlement:personal-information.photos-library': ['May ask for your photos', 'low'],
  'entitlement:device.usb': ['May use USB devices', 'low'],
  'entitlement:device.bluetooth': ['May use Bluetooth', 'low'],
  'entitlement:virtualization': ['May run virtual machines', 'low'],
};
const GRANTED = {
  'full-disk-access': ['Allowed: Full Disk Access', 'high'],
  accessibility: ['Allowed: Accessibility (can control your computer)', 'high'],
  'screen-recording': ['Allowed: Screen Recording', 'high'],
  'input-monitoring': ['Allowed: Input Monitoring (sees keystrokes)', 'high'],
};
const LEVEL_ORDER = { high: 0, medium: 1, low: 2 };

// Turn permission names into sorted plain-language lines; unlabeled technical
// names are only counted.
function describePermissions(permissions) {
  const lines = [];
  let other = 0;
  for (const permission of permissions ?? []) {
    if (permission.startsWith('granted:')) {
      const name = permission.slice('granted:'.length);
      const [text, level] = GRANTED[name] ?? [`Allowed: ${name.replace(/-/g, ' ')}`, 'medium'];
      lines.push({ permission, text, level });
    } else if (PERMISSIONS[permission]) {
      const [text, level] = PERMISSIONS[permission];
      lines.push({ permission, text, level });
    } else {
      other += 1;
    }
  }
  lines.sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level] || a.text.localeCompare(b.text));
  return { lines, other };
}

function normalize(text) {
  return typeof text === 'string' ? text.trim().toLowerCase() : null;
}

// Process names arrive as "ollama", "ollama.exe" or a full path on any OS.
function normalizeValue(kind, value) {
  let normalized = normalize(value);
  if (kind === 'process' && normalized) {
    normalized = normalized.split(/[\\/]/).pop();
    if (normalized.endsWith('.exe')) normalized = normalized.slice(0, -4);
  }
  return normalized;
}

function fieldMatches(expected, actual) {
  if (expected === undefined) return true;
  const options = Array.isArray(expected) ? expected : [expected];
  return actual !== null && options.some(option => normalize(option) === actual);
}

function signalMatches(signal, item) {
  return signal.kind === item.kind
    && fieldMatches(signal.value, item.value)
    && fieldMatches(signal.app, item.app)
    && fieldMatches(signal.parent, item.parent)
    && fieldMatches(signal.client, item.client);
}

function displayName(text) {
  return DISPLAY_NAMES[normalize(text)] ?? text;
}

function explain(signal, group) {
  const template = signal.explain ?? (group.count > 1 ? '{value} (×{count})' : '{value}');
  const text = template.replace(/\{(\w+)\}/g, (_, key) => {
    if (key === 'count') return String(group.count);
    if (key === 's') return group.count === 1 ? '' : 's';
    if (key === 'es') return group.count === 1 ? '' : 'es';
    const values = [...(group.fields[key] ?? [])];
    return values.length ? values.join(', ') : 'unknown';
  });
  return group.problem ? `${text} · ⚠ ${group.problem}` : text;
}

// Installed programs that can get a tool passport, one per installation.
const INSTALL_KINDS = new Set(['app', 'cli', 'editor-extension', 'browser-extension']);

// A finding counts as running when a live process matched it.
const RUNNING_KINDS = new Set(['process', 'interpreted-process']);

// Only programs and apps carry a code signature the scan can check.
const SIGNED_KINDS = new Set(['process', 'app', 'cli']);

// Compare one matched item's signer (from the scan) with the expected publisher.
function checkSigner(entry, kind, raw) {
  if (!SIGNED_KINDS.has(kind)) return null;
  const signer = typeof raw.signer === 'string' ? raw.signer.trim() : '';
  // Scripts (npm and pip command shims) cannot carry a signature: nothing to verify.
  if (!signer || signer === 'script') return { status: 'not-checked' };
  if (signer === 'unsigned' || signer === 'invalid') return { status: 'mismatch', signer };
  const teams = entry.publisher?.teams;
  if (!teams) return { status: 'signed', signer };
  return teams.includes(signer) ? { status: 'confirmed', signer } : { status: 'mismatch', signer };
}

function signerProblem(entry, signer) {
  if (signer === 'unsigned') return 'unsigned program, publisher unknown';
  if (signer === 'invalid') return 'signature does not verify';
  return `signed by team ${signer}, not ${entry.publisher?.name ?? 'the expected publisher'}`;
}

// One verdict per finding. Any mismatch wins: a copied name next to the real
// tool must not hide behind the real tool's signature.
function verifyPublisher(entry, checks) {
  const publisher = entry.publisher;
  if (checks.mismatch) {
    return { status: 'unverified', text: publisher
      ? `Unverified: ${checks.mismatch} item${checks.mismatch === 1 ? ' is' : 's are'} not signed by ${publisher.name}`
      : `Unverified: ${checks.mismatch} item${checks.mismatch === 1 ? ' comes' : 's come'} from a program without a valid signature` };
  }
  if (checks.confirmed) {
    return { status: 'confirmed', text: `Signed by ${publisher.name} (${publisher.teams.join(', ')})` };
  }
  if (checks.signed.size) {
    return { status: 'signed', text: `Signed by team ${[...checks.signed].join(', ')}; publisher not in the catalog yet` };
  }
  return { status: 'not-checked', text: 'Publisher not checked' };
}

// Group by signal (and its groupBy field) so "5 sessions" and "2 profiles"
// become one line each. Items that fail the signer check get their own line.
function addToGroup(finding, entry, signal, raw, count, problem = null) {
  const by = signal.groupBy ? String(raw[signal.groupBy] ?? '') : '';
  const key = `${entry.signals.indexOf(signal)}|${by}|${problem ?? ''}`;
  let group = finding.groups.get(key);
  if (!group) {
    group = { signal, count: 0, fields: {}, problem };
    finding.groups.set(key, group);
  }
  group.count += count;
  for (const field of EXPLAIN_FIELDS) {
    if (typeof raw[field] !== 'string' || !raw[field].trim()) continue;
    (group.fields[field] ??= new Set()).add(displayName(raw[field].trim()));
  }
}

// The command and package names the catalog knows. The scan looks up only
// these, so other tools installed on the device stay private.
function scanRequest(findingsCatalog = catalog) {
  const names = kind => [...new Set(findingsCatalog
    .flatMap(entry => entry.signals)
    .filter(signal => signal.kind === kind)
    .flatMap(signal => (Array.isArray(signal.value) ? signal.value : [signal.value]))
    .filter(value => typeof value === 'string'))].sort();
  return { cli: names('cli'), packages: names('package') };
}

function discover(evidence, findingsCatalog = catalog) {
  const findings = new Map();
  const attachments = [];

  for (const raw of Array.isArray(evidence) ? evidence : []) {
    if (typeof raw?.kind !== 'string' || typeof raw?.value !== 'string') continue;

    const kind = normalize(raw.kind);
    const item = {
      kind,
      value: normalizeValue(kind, raw.value),
      app: normalize(raw.app),
      parent: normalizeValue('process', raw.parent),
      client: normalize(raw.client),
    };
    if (!item.value) continue;
    const count = Number.isInteger(raw.count) && raw.count > 0 ? raw.count : 1;

    for (const entry of findingsCatalog) {
      const signal = entry.signals.find(candidate => signalMatches(candidate, item));
      if (!signal) continue;
      if (signal.attachOnly) {
        attachments.push({ entry, signal, raw, count });
        continue;
      }

      let finding = findings.get(entry.id);
      if (!finding) {
        finding = {
          id: entry.id, label: entry.label, kind: entry.kind,
          evidence: raw.value, matches: 0, running: false, groups: new Map(), permissions: new Set(), entry,
          installations: new Map(), packageVersion: null,
          checks: { confirmed: 0, mismatch: 0, signed: new Set() },
        };
        findings.set(entry.id, finding);
      }
      finding.matches += count;
      finding.running ||= RUNNING_KINDS.has(kind);
      if (INSTALL_KINDS.has(kind)) {
        const key = JSON.stringify([kind, item.value, raw.editor, raw.browser, raw.profile]);
        if (!finding.installations.has(key)) {
          finding.installations.set(key, Object.fromEntries(Object.entries({
            kind, value: item.value, version: raw.version, signer: raw.signer,
            editor: raw.editor, browser: raw.browser, profile: raw.profile,
          }).filter(([, field]) => typeof field === 'string')));
        }
      }
      if (kind === 'package' && typeof raw.version === 'string') finding.packageVersion ??= raw.version;
      const check = checkSigner(entry, kind, raw);
      if (check?.status === 'confirmed') finding.checks.confirmed += count;
      if (check?.status === 'mismatch') finding.checks.mismatch += count;
      if (check?.status === 'signed') finding.checks.signed.add(check.signer);
      addToGroup(finding, entry, signal, raw, count,
        check?.status === 'mismatch' ? signerProblem(entry, check.signer) : null);
      for (const permission of Array.isArray(raw.permissions) ? raw.permissions : []) {
        if (typeof permission === 'string') finding.permissions.add(permission);
      }
    }
  }

  for (const { entry, signal, raw, count } of attachments) {
    const finding = findings.get(entry.id);
    if (finding) addToGroup(finding, entry, signal, raw, count);
  }

  return [...findings.values()].map(({ groups, permissions, entry, checks, installations, ...finding }) => ({
    ...finding,
    installations: [...installations.values()],
    reasons: [...groups.values()].map(group => explain(group.signal, group)),
    permissions: [...permissions].sort(),
    verification: verifyPublisher(entry, checks),
  }));
}

// ── summary and filters for the device view (pure, tested) ────────────────────

const hasBroadAccess = finding => describePermissions(finding.permissions).lines.some(line => line.level === 'high');

const DEVICE_FILTERS = {
  all: () => true,
  running: finding => finding.running,
  unverified: finding => finding.verification.status === 'unverified',
  broad: hasBroadAccess,
};

function summarizeFindings(findings) {
  return {
    found: findings.length,
    running: findings.filter(DEVICE_FILTERS.running).length,
    verified: findings.filter(finding => finding.verification.status === 'confirmed').length,
    unverified: findings.filter(DEVICE_FILTERS.unverified).length,
    broad: findings.filter(hasBroadAccess).length,
  };
}

// Unverified first, then running, then by name.
function filterFindings(findings, filter = 'all', query = '') {
  const needle = query.trim().toLowerCase();
  const rank = finding => (finding.verification.status === 'unverified' ? 0 : finding.running ? 1 : 2);
  return findings
    .filter(DEVICE_FILTERS[filter] ?? DEVICE_FILTERS.all)
    .filter(finding => !needle || [finding.label, finding.kind, ...finding.reasons].join(' ').toLowerCase().includes(needle))
    .sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}

// ── device view (browser only) ────────────────────────────────────────────────

const NOT_COLLECTED_NAMES = {
  'granted-permissions': 'macOS privacy grants',
  signer: 'Code signatures',
  app: 'Installed apps',
  'browser-extension': 'Browser extensions',
  'native-host': 'Native messaging hosts',
};

function installationLabel(installation) {
  const name = text => DISPLAY_NAMES[text] ?? text;
  return {
    app: () => 'App',
    cli: () => `Command "${installation.value}"`,
    'editor-extension': () => `${name(installation.editor)} extension`,
    'browser-extension': () => `${name(installation.browser)} extension (${installation.profile})`,
  }[installation.kind]();
}

function findingSection(finding) {
  const kind = Object.keys(INSTALLATION_SECTIONS).find(candidate =>
    finding.installations.some(installation => installation.kind === candidate));
  return INSTALLATION_SECTIONS[kind] ?? {
    'Coding agent': 'Terminal', 'AI editor': 'Editor', 'AI app': 'Desktop',
    'Editor extension': 'Editor', 'Browser extension': 'Browser',
  }[finding.kind] ?? 'Other tools';
}

function findingCard(finding) {
  const { lines, other } = describePermissions(finding.permissions);
  const broad = lines.filter(line => line.level === 'high').length;
  const { status, text } = finding.verification;
  const verifiedBadge = {
    confirmed: () => badge('✓ Expected publisher signature found', 'ok', text),
    unverified: () => badge('⚠ Publisher not confirmed', 'warn', text),
    signed: () => badge('Signed evidence found', 'info', text),
  }[status]?.() ?? badge('Publisher not checked', 'muted', text);
  return el('li', { class: `card${status === 'unverified' ? ' card-warn' : ''}` },
    el('div', { class: 'card-head' },
      el('div', {}, el('h4', {}, finding.label), el('p', { class: 'muted small' }, finding.kind)),
      el('div', { class: 'badges' },
        finding.running ? badge('● Running', 'info') : badge('Installed', 'muted'), verifiedBadge)),
    status === 'not-checked' ? null : el('p', { class: `small ${status === 'unverified' ? 'warn-text' : 'muted'}` }, text),
    el('ul', { class: 'facts' }, finding.reasons.map(reason => el('li', {}, reason))),
    finding.installations.length && isLocalService() ? el('div', { class: 'setups' },
      el('p', { class: 'setups-title' }, 'Passports'),
      finding.installations.map(installation => el('div', { class: 'setup' },
        el('div', {}, el('strong', {}, `${INSTALLATION_SECTIONS[installation.kind]} · ${installationLabel(installation)}`),
          installation.version ? el('p', { class: 'muted small' }, `version ${installation.version}`) : null),
        el('button', {
          type: 'button', class: 'secondary',
          onclick: () => startToolPassport({
            finding: finding.id, label: finding.label, toolKind: finding.kind, installation,
            verification: finding.verification, suggestedVersion: installation.version ?? finding.packageVersion,
          }),
        }, 'Create passport')))) : null,
    lines.length || other ? el('details', {},
      el('summary', {}, broad ? `Can reach · ⚠ ${broad} broad` : 'Can reach'),
      el('ul', {},
        lines.map(line => el('li', { class: line.level === 'high' ? 'warn-text' : undefined }, line.text)),
        other ? el('li', { class: 'muted' }, `${other} other technical permission${other === 1 ? '' : 's'}`) : null))
      : null);
}

if (typeof document !== 'undefined') {
  const $ = id => document.getElementById(id);
  // With the local service running, nothing is shown until the first real scan:
  // the simulated sample is only for the page opened on its own.
  const view = { findings: [], filter: 'all', query: '', waiting: isLocalService() };

  const render = () => {
    $('device-search').hidden = view.waiting;
    if (view.waiting) {
      $('device-tiles').replaceChildren();
      $('device-filters').replaceChildren();
      $('findings').replaceChildren(el('li', { class: 'empty' },
        'No scan yet. Press "Scan this device" to see which AI tools are installed and running here.'));
      return;
    }
    const totals = summarizeFindings(view.findings);
    renderTiles($('device-tiles'), [
      { label: 'AI tools found', value: totals.found },
      { label: 'Running now', value: totals.running },
      { label: 'Verified publisher', value: totals.verified, tone: totals.verified ? 'ok' : undefined },
      { label: 'Unverified', value: totals.unverified, tone: totals.unverified ? 'warn' : undefined,
        hint: 'Unsigned, invalid signature, or not signed by the expected publisher' },
      { label: 'Broad access', value: totals.broad, hint: 'Not sandboxed, or can read and change every website' },
    ]);
    renderChips($('device-filters'), [
      { id: 'all', label: 'All', count: totals.found },
      { id: 'running', label: 'Running', count: totals.running },
      { id: 'unverified', label: 'Unverified', count: totals.unverified },
      { id: 'broad', label: 'Broad access', count: totals.broad },
    ], view.filter, filter => { view.filter = filter; render(); });
    const shown = filterFindings(view.findings, view.filter, view.query);
    $('findings').replaceChildren(...(shown.length ? groupedCards(shown, findingSection, findingCard)
      : [el('li', { class: 'empty' }, view.findings.length ? 'No tools match this filter.' : 'No AI tools found.')]));
  };

  $('device-search').addEventListener('input', event => { view.query = event.target.value; render(); });
  if (!view.waiting) view.findings = discover(sampleEvidence);
  render();

  if (isLocalService()) {
    const button = $('scan-device');
    button.hidden = false;
    $('discovery-source').hidden = true;
    button.addEventListener('click', async () => {
      button.disabled = true;
      $('scan-status').textContent = 'Reading identifiers and checking code signatures on this device…';
      const started = performance.now();
      try {
        const response = await fetch('/discovery/scan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(scanRequest()),
          signal: AbortSignal.timeout(60_000),
        });
        if (!response.ok) throw new Error(`Footprints returned HTTP ${response.status}.`);
        const scan = await response.json();
        const seconds = ((performance.now() - started) / 1000).toFixed(1);
        view.findings = discover(scan.evidence);
        view.waiting = false;
        $('discovery-source').hidden = true;
        $('scan-status').textContent = `Scanned ${formatDate(new Date().toISOString(), true)} · `
          + `${scan.evidence.length} identifiers in ${seconds} s · nothing left this device`;
        $('device-notes').replaceChildren(...Object.entries(scan.not_collected || {}).map(([what, why]) =>
          el('p', { class: 'note' }, el('strong', {}, `${NOT_COLLECTED_NAMES[what] ?? what} not checked: `), why)));
        render();
      } catch (error) {
        $('scan-status').textContent = `Device scan failed. ${error.message}`;
      } finally {
        button.disabled = false;
      }
    });
  }
}

if (typeof module !== 'undefined') {
  module.exports = {
    discover, describePermissions, scanRequest, summarizeFindings, filterFindings, installationLabel, sampleEvidence,
    catalog,
  };
}
