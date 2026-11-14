// Passports view: Forkit Core passports for AI tools found by the device scan
// (one per installation) and for agent setups found by the repository scan, plus
// every passport in the local registry with its checks:
//   record     the stored passport still matches its ID (Forkit Core's check)
//   install    tool passports: the installed program still matches the fingerprint
//   publisher  tool passports: the program is still signed by the same publisher
//   setup      repo passports: the agent files still match the fingerprint
// See tool_passports.py and repo_passports.py for what goes into a passport.

const RECORD_BADGES = {
  intact: { text: '✓ Record ID matches', tone: 'ok', title: 'Core recomputed the ID from the stored identity fields' },
};
const SETUP_BADGES = {
  unchanged: { text: '✓ Setup unchanged', tone: 'ok', title: 'The agent files still match the passport' },
  changed: { text: '⚠ Setup changed', tone: 'warn', title: 'The agent files were edited since this passport' },
  'files-removed': { text: '⚠ Agent files removed', tone: 'warn', title: 'The repository no longer has these agent files' },
  'repo-missing': { text: 'Repository not found', tone: 'muted', title: 'The repository moved or was deleted' },
  unreadable: { text: '⚠ Setup fingerprint unavailable', tone: 'warn', title: 'One or more setup files could not be read' },
};
const INSTALL_BADGES = {
  unchanged: () => ({ text: '✓ Installation fingerprint matches', tone: 'ok', title: 'The files listed under Fingerprint of still match the saved fingerprint' }),
  updated: passport => ({ text: `New version installed: v${passport.current_version}`, tone: 'info',
    title: 'The tool was updated since this passport' }),
  changed: () => ({ text: '⚠ Installation fingerprint differs', tone: 'warn',
    title: 'The measured files differ without a detected version change' }),
  unreadable: () => ({ text: '⚠ Fingerprint unavailable', tone: 'warn', title: 'One or more measured files could not be read' }),
  unknown: () => ({ text: 'Fingerprint not checked', tone: 'muted', title: 'The saved installation could not be checked on this device' }),
  missing: () => ({ text: 'Not installed', tone: 'muted', title: 'This installation is no longer on the device' }),
};
const PUBLISHER_BADGES = {
  verified: { text: '✓ Signing team matches', tone: 'ok', title: 'The macOS code signature verifies against the recorded team; resources are not checked' },
  changed: { text: '⚠ Signing identity differs', tone: 'warn', title: 'A different signing identity was found, or the program is unsigned' },
  invalid: { text: '⚠ Invalid code signature', tone: 'warn', title: 'macOS could not verify the current code signature' },
  user: { text: 'Vouched for by a person', tone: 'muted', title: 'The recorded name is a user claim; publisher signature verification was not performed or did not establish a publisher' },
  'not-checked': { text: 'Publisher not checked', tone: 'muted', title: 'No current publisher verification result is available' },
};
const NEEDS_ATTENTION = {
  setup: ['changed', 'files-removed', 'unreadable'], install: ['changed', 'unreadable'], publisher: ['changed', 'invalid'],
};

function passportBadges(passport) {
  const badges = [{ text: `v${passport.version}`, tone: 'info' }];
  badges.push(RECORD_BADGES[passport.record]
    ?? { text: '⚠ Record ID check failed', tone: 'warn', title: `Forkit Core check: ${passport.record}` });
  if (passport.kind === 'repo-agent' && SETUP_BADGES[passport.setup]) badges.push(SETUP_BADGES[passport.setup]);
  if (passport.kind === 'device-tool') {
    if (INSTALL_BADGES[passport.install]) badges.push(INSTALL_BADGES[passport.install](passport));
    if (PUBLISHER_BADGES[passport.publisher]) badges.push(PUBLISHER_BADGES[passport.publisher]);
  }
  if (passport.kind === 'provider-model') badges.push({ text: 'Provider model', tone: 'muted' });
  return badges;
}

function needsAttention(passport) {
  return passport.record !== 'intact'
    || Object.entries(NEEDS_ATTENTION).some(([check, states]) => states.includes(passport[check]));
}

function summarizePassports(passports) {
  return {
    tools: passports.filter(passport => passport.kind === 'device-tool').length,
    agents: passports.filter(passport => passport.kind === 'repo-agent').length,
    models: passports.filter(passport => passport.kind === 'model').length,
    attention: passports.filter(needsAttention).length,
  };
}

// ── passports view (browser only) ─────────────────────────────────────────────

let passportDraft = null;
let selectedPassportId = null;
let passportLoad = 0;

function hasPublisherSignature(signer) {
  return typeof signer === 'string' && Boolean(signer.trim()) && !['unsigned', 'invalid', 'script'].includes(signer);
}

// From a repository card: a passport for one agent setup of the repository.
function startAgentPassport(draft) {
  openPassportForm({ type: 'repo', ...draft });
}

// From a device card: a passport for one installation of a scanned tool.
function startToolPassport(draft) {
  openPassportForm({ type: 'tool', ...draft });
}

function openPassportForm(draft) {
  passportDraft = draft;
  renderPassportForm();
  document.getElementById('agent-passport').showModal();
}

function readStored(key) {
  try { return localStorage.getItem(key) ?? ''; } catch { return ''; }
}

function writeStored(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable: just don't remember */ }
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(payload?.detail ?? `Footprints returned HTTP ${response.status}.`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function renderPassportForm() {
  const container = document.getElementById('agent-passport');
  const draft = passportDraft;
  const tool = draft.type === 'tool';
  // Only an installation whose own program carries a valid signature gets its publisher from it;
  // e.g. a Chrome extension has no signature even when the tool's other parts do.
  const signed = tool && hasPublisherSignature(draft.installation.signer);
  const detectedVersion = tool ? draft.installation.version : null;
  const status = el('p', { class: 'status', role: 'status' });
  const sourceName = tool ? draft.label : `${draft.repo.split(/[\\/]/).pop()} · ${draft.label}`;
  const agentName = el('input', { name: 'display_name', value: draft.displayName ?? sourceName,
    required: true, maxlength: '80', autofocus: true });
  const version = el('input', { name: 'version', value: detectedVersion ?? draft.suggestedVersion ?? draft.version ?? (tool ? '' : '1.0.0'),
    required: true, pattern: '[0-9]+\\.[0-9]+(\\.[0-9]+)?', title: 'A version such as 1.0 or 1.0.0',
    placeholder: 'e.g. 1.0.0', readonly: Boolean(detectedVersion) });
  const creator = el('input', { name: 'creator', value: readStored('footprints.creator'), required: !signed, maxlength: '100' });
  const submit = el('button', { type: 'submit', class: 'primary' }, 'Register passport');
  const cancel = el('button', { type: 'button', class: 'secondary', onclick: () => container.close() }, 'Cancel');
  const form = el('form', { class: 'agent-form' },
    el('h2', { id: 'registration-heading' }, 'Register passport'),
    el('p', { class: 'muted small' }, tool
      ? `${INSTALLATION_SECTIONS[draft.installation.kind]} · ${draft.label} · ${installationLabel(draft.installation)}`
      : `Repository · ${sourceName}`),
    !tool && draft.files?.length ? el('p', { class: 'small' }, 'Files: ', draft.files.join(', ')) : null,
    el('p', { class: 'muted small' }, 'Saves a fingerprint on this device.'),
    el('fieldset', {},
      el('label', {}, 'Agent name', agentName),
      el('label', {}, detectedVersion ? 'Version (detected)' : 'Version', version),
      signed
        ? el('p', { class: 'small' }, el('strong', {}, 'Publisher: '),
          'Footprints will check the signature of this installation during registration.')
        : el('label', {}, 'Vouched for by', creator)),
    el('div', { class: 'registration-actions' }, submit, cancel),
    status);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    submit.disabled = true;
    cancel.disabled = true;
    status.textContent = 'Registering…';
    const common = { version: version.value.trim(), creator: creator.value.trim(), display_name: agentName.value.trim() };
    try {
      const result = tool
        ? await postJson('/passports/tool', {
          finding: draft.finding, label: draft.label, tool_kind: draft.toolKind, ...draft.installation, ...common,
        })
        : await postJson('/passports/repo-agent', { repo: draft.repo, tool: draft.tool, ...common });
      if (!signed) writeStored('footprints.creator', common.creator);
      const name = result.passport.metadata?.context?.display_name || sourceName;
      const message = document.getElementById('registration-result');
      message.textContent = result.status === 'already-registered'
        ? `${name} is already registered.`
        : `${name} registered. ${result.check?.valid ? 'Record ID matches.' : 'Check the saved record.'}`;
      container.close();
      showView('passports');
      message.focus();
      await loadPassports(result.passport.id);
    } catch (error) {
      if (error.payload?.suggested_version) version.value = error.payload.suggested_version;
      if (error.payload?.publisher_vouch_required && signed) {
        renderPassportFormForVouch(draft, agentName.value, version.value, error.message);
        return;
      }
      status.textContent = `Passport not created. ${error.message}`;
    } finally {
      submit.disabled = false;
      cancel.disabled = false;
    }
  });
  container.replaceChildren(form);
}

function renderPassportFormForVouch(draft, displayName, version, message) {
  passportDraft = { ...draft, displayName, suggestedVersion: version,
    installation: { ...draft.installation, signer: undefined } };
  renderPassportForm();
  const dialog = document.getElementById('agent-passport');
  dialog.querySelector('[role="status"]').textContent = message;
  dialog.querySelector('input[name="creator"]').focus();
}

function showPassportDetail(passport, focus = true) {
  selectedPassportId = passport?.id ?? null;
  const detail = document.getElementById('passport-detail');
  document.getElementById('passport-overview').hidden = Boolean(passport);
  document.getElementById('model-passport').hidden = Boolean(passport);
  detail.hidden = !passport;
  if (!passport) {
    detail.replaceChildren();
    if (focus) document.getElementById('passport-heading').focus();
    return;
  }
  detail.replaceChildren(el('button', { type: 'button', class: 'secondary',
    onclick: () => showPassportDetail(null) }, 'Back to passports'), passportCard(passport, true));
  if (focus) detail.querySelector('h3').focus();
}

function setupEvidence(passport) {
  const added = passport.files_added ?? [];
  const removed = passport.files_removed ?? [];
  const observations = passport.scan_history ?? [];
  const labels = { matches: 'Matched the registered setup', different: 'Setup differed',
    'not-found': 'Setup not found in scan', unreadable: 'Setup fingerprint unavailable' };
  return el('section', {},
    el('h4', {}, 'Setup evidence'),
    added.length ? el('p', {}, 'Added since registration: ', added.join(', ')) : null,
    removed.length ? el('p', {}, 'Removed since registration: ', removed.join(', ')) : null,
    passport.setup === 'changed' && !added.length && !removed.length
      ? el('p', {}, 'The same files are present, but their fingerprint has changed.') : null,
    observations.length ? el('ul', { class: 'scan-history' }, observations.map(scan =>
      el('li', {}, formatDate(scan.at, true), ' · ', labels[scan.state])))
      : el('p', { class: 'muted' }, 'No repository scan history for this setup yet.'));
}

function passportCard(passport, detail = false) {
  const repoAgent = passport.kind === 'repo-agent';
  const deviceTool = passport.kind === 'device-tool';
  const title = passport.display_name || (repoAgent ? `${passport.tool_label} · ${passport.repo.split(/[\\/]/).pop()}`
    : deviceTool ? passport.label : passport.name);
  const subtitle = repoAgent ? passport.repo
    : deviceTool ? `${passport.surface.replace(/-/g, ' ')} · ${passport.installation}` : passport.description ?? passport.type;
  const newSetupVersion = repoAgent && passport.setup === 'changed' && passport.suggested_version;
  const newToolVersion = deviceTool && passport.install === 'updated' && passport.request;
  const badges = el('div', { class: 'badges' }, passportBadges(passport).map(item => badge(item.text, item.tone, item.title)));
  const className = `card${needsAttention(passport) ? ' card-warn' : ''}`;
  if ((repoAgent || deviceTool) && !detail) {
    return el('li', { class: className },
      el('div', { class: 'card-head' },
        el('div', {}, el('h4', {}, title), el('p', { class: 'muted small mono' }, subtitle)), badges),
      el('button', { type: 'button', class: 'secondary', 'aria-label': `View passport for ${title}`,
        onclick: () => showPassportDetail(passport) }, 'View passport'));
  }
  return el(detail ? 'section' : 'li', { class: className },
    el('div', { class: 'card-head' },
      el('div', {}, el(detail ? 'h3' : 'h4', detail ? { tabindex: '-1' } : {}, title),
        el('p', { class: 'muted small mono' }, subtitle)), badges),
    detail && deviceTool ? el('p', {}, 'This passport checks whether the installed program still matches its fingerprint.') : null,
    el('dl', { class: 'meta-grid' },
      el('dt', {}, deviceTool && passport.creator_source === 'code-signature' ? 'Publisher' : 'Vouched for by'),
      el('dd', {}, [passport.creator, passport.organization].filter(Boolean).join(', ') || 'unknown',
        deviceTool && passport.creator_source === 'code-signature' ? el('span', { class: 'muted' }, ' (from code signature)') : null),
      el('dt', {}, 'Created'), el('dd', {}, formatDate(passport.created_at, true)),
      el('dt', {}, 'Passport ID'), el('dd', { class: 'mono', title: passport.id }, detail ? passport.id : `${passport.id.slice(0, 16)}…`),
      (repoAgent || deviceTool) && passport.parent ? [el('dt', {}, 'Lineage'),
        el('dd', { class: 'mono', title: passport.parent }, `follows ${passport.parent.slice(0, 12)}…`)] : null,
      repoAgent && passport.tools.length ? [el('dt', {}, 'MCP servers'), el('dd', {}, passport.tools.join(', '))] : null,
      deviceTool ? [el('dt', {}, 'Fingerprint of'), el('dd', {}, passport.fingerprinted.join(', '))] : null),
    passport.kind === 'model' && passport.artifact_hash ? el('details', {},
      el('summary', {}, 'Model file details'),
      el('dl', { class: 'meta-grid' },
        passport.artifact_files?.length ? [el('dt', {}, 'Files'), el('dd', {}, passport.artifact_files.join(', '))] : null,
        el('dt', {}, 'SHA-256'), el('dd', { class: 'mono' }, passport.artifact_hash))) : null,
    repoAgent ? el('div', { class: 'tags' }, passport.files.map(file => el('span', { class: 'tag' }, file))) : null,
    repoAgent && detail ? setupEvidence(passport) : null,
    newSetupVersion ? el('button', {
      type: 'button', class: 'secondary',
      onclick: () => startAgentPassport({
        repo: passport.repo, tool: passport.tool, label: passport.tool_label, files: passport.files,
        version: passport.suggested_version, displayName: passport.display_name,
      }),
    }, `Create v${passport.suggested_version} for the changed setup`) : null,
    newToolVersion ? el('button', {
      type: 'button', class: 'secondary',
      onclick: () => {
        const { finding, label, tool_kind: toolKind, ...installation } = passport.request;
        startToolPassport({
          finding, label, toolKind, displayName: passport.display_name,
          installation: { ...installation, version: passport.current_version,
            signer: passport.publisher === 'verified' ? 'verified' : undefined },
          verification: passport.publisher === 'verified' ? { status: 'confirmed', text: `Signed by ${passport.creator}` } : null,
        });
      },
    }, `Create passport for v${passport.current_version}`) : null);
}

async function loadPassports(openId = null) {
  const load = ++passportLoad;
  const status = document.getElementById('passports-status');
  status.textContent = 'Checking passports…';
  try {
    const { passports } = await postJson('/passports/list', {});
    if (load !== passportLoad) return;
    const totals = summarizePassports(passports);
    renderTiles(document.getElementById('passport-tiles'), [
      { label: 'Tool passports', value: totals.tools },
      { label: 'Repository setup passports', value: totals.agents },
      { label: 'Model passports', value: totals.models },
      { label: 'Need attention', value: totals.attention, tone: totals.attention ? 'warn' : 'ok',
        hint: 'A record ID check failed, measured files differ or are unreadable, or a signing check failed' },
    ]);
    const section = passport => passport.kind === 'device-tool' ? INSTALLATION_SECTIONS[passport.installation_kind] ?? 'Other tools'
      : passport.kind === 'repo-agent' ? 'Repositories' : 'Models';
    document.getElementById('passport-list').replaceChildren(...(passports.length ? groupedCards(passports, section, passportCard)
      : [el('li', { class: 'empty' }, 'No passports yet.')]));
    if (openId || selectedPassportId) {
      const selected = passports.find(passport => passport.id === (openId || selectedPassportId));
      showPassportDetail(selected, Boolean(openId));
    }
    status.textContent = `Checked ${passports.length} passport${passports.length === 1 ? '' : 's'} `
      + `at ${formatDate(new Date().toISOString(), true)}.`;
  } catch (error) {
    if (load !== passportLoad) return;
    status.textContent = `Could not load passports. ${error.message}`;
  }
}

if (typeof document !== 'undefined' && isLocalService()) {
  document.getElementById('passports-local').hidden = false;
  document.getElementById('refresh-passports').addEventListener('click', () => loadPassports());
  document.getElementById('agent-passport').addEventListener('cancel', event => {
    if (event.currentTarget.querySelector('button[type="submit"]')?.disabled) event.preventDefault();
  });
  loadPassports();
}

if (typeof module !== 'undefined') {
  module.exports = { passportBadges, summarizePassports, needsAttention, hasPublisherSignature };
}
