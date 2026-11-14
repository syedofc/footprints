// Repository tracker view: git repositories on this device and the AI agent files
// in them. The local Footprints service (python serve.py) finds and describes
// them (see repos.py for exactly what is read); this page shows the result.

// One line per kind of AI configuration found in a repository.
function aiSummary(ai) {
  const lines = [];
  if (ai?.files?.length) lines.push(`Agent files: ${ai.files.join(', ')}`);
  for (const [folder, count] of Object.entries(ai?.folders ?? {})) {
    lines.push(`${count} file${count === 1 ? '' : 's'} in ${folder}`);
  }
  if (ai?.mcp_servers?.length) {
    lines.push(`MCP servers: ${ai.mcp_servers.map(({ server, config }) => `${server} (${config})`).join(', ')}`);
  }
  for (const rules of ai?.claude_rules ?? []) {
    lines.push(`Claude Code rules (${rules.config}): mode ${rules.mode}, `
      + `${rules.allow} allow, ${rules.ask} ask, ${rules.deny} deny`);
  }
  return lines;
}

// Short tags for a repository card: [{ text, title }].
function aiTags(ai) {
  const tags = (ai?.files ?? []).map(file => ({ text: file, title: 'Agent file present (contents not read)' }));
  for (const [folder, count] of Object.entries(ai?.folders ?? {})) {
    tags.push({ text: `${folder} ×${count}`, title: `${count} file${count === 1 ? '' : 's'} in ${folder}` });
  }
  for (const { server, config } of ai?.mcp_servers ?? []) {
    tags.push({ text: `MCP: ${server}`, title: `MCP server configured in ${config}` });
  }
  for (const rules of ai?.claude_rules ?? []) {
    tags.push({ text: `Rules: ${rules.allow} allow · ${rules.ask} ask · ${rules.deny} deny`,
      title: `Claude Code permission rules in ${rules.config}, mode ${rules.mode}` });
  }
  return tags;
}

const hasAi = repo => aiTags(repo.ai).length > 0;

// {path: 'new' | 'changed'} from the comparison with the previous scan.
function changeMarks(changes) {
  const marks = {};
  for (const path of changes?.new ?? []) marks[path] = 'new';
  for (const { path } of changes?.changed ?? []) marks[path] = 'changed';
  return marks;
}

const CHANGED_FIELD_NAMES = {
  origin: 'origin', last_active: 'activity', ai: 'AI configuration', agents: 'agent file contents',
};

function changedFields(changes, path) {
  const entry = (changes?.changed ?? []).find(change => change.path === path);
  return entry ? entry.fields.map(field => CHANGED_FIELD_NAMES[field] ?? field) : [];
}

function summarizeRepos(scan) {
  const marks = changeMarks(scan.changes);
  return {
    total: scan.repos.length,
    withAi: scan.repos.filter(hasAi).length,
    cloned: scan.repos.filter(repo => repo.how === 'cloned').length,
    createdHere: scan.repos.filter(repo => repo.how === 'created here').length,
    changed: Object.keys(marks).length,
    notFound: scan.changes?.not_found?.length ?? 0,
    firstScan: !scan.changes,
  };
}

const REPO_FILTERS = {
  all: () => true,
  ai: repo => hasAi(repo),
  changed: (repo, marks) => Boolean(marks[repo.path]),
};

function filterRepos(scan, filter = 'all', query = '') {
  const marks = changeMarks(scan.changes);
  const needle = query.trim().toLowerCase();
  return scan.repos
    .filter(repo => (REPO_FILTERS[filter] ?? REPO_FILTERS.all)(repo, marks))
    .filter(repo => !needle || [repo.path, repo.origin ?? ''].join(' ').toLowerCase().includes(needle));
}

function summaryText(scan) {
  const count = `${scan.repos.length} repositor${scan.repos.length === 1 ? 'y' : 'ies'}`;
  if (!scan.changes) return `${count}. First scan: saved as the baseline for the next one.`;
  const { new: added, not_found: missing, changed } = scan.changes;
  return `${count}. Since the scan of ${formatDate(scan.previous_scan_at, true)}: `
    + `${added.length} new, ${changed.length} changed, ${missing.length} not found.`;
}

// ── repository view (browser only) ────────────────────────────────────────────

const repoName = path => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

function repoCard(repo, mark, fields) {
  const tags = aiTags(repo.ai);
  return el('li', { class: 'card' },
    el('div', { class: 'card-head' },
      el('div', {},
        el('h3', {}, repoName(repo.path)),
        el('p', { class: 'muted small mono' }, repo.path)),
      el('div', { class: 'badges' },
        mark === 'new' ? badge('New', 'info') : null,
        mark === 'changed' ? badge('Changed', 'warn', `Changed: ${fields.join(', ')}`) : null,
        repo.last_active ? badge(`Active ${relativeTime(repo.last_active)}`, 'muted', formatDate(repo.last_active, true)) : null)),
    el('dl', { class: 'meta-grid' },
      el('dt', {}, 'Origin'), el('dd', { class: 'mono' }, repo.origin ?? 'No remote'),
      el('dt', {}, repo.how === 'cloned' ? 'Cloned' : 'Created'),
      el('dd', {}, repo.cloned_at ? formatDate(repo.cloned_at) + (repo.how === 'cloned' ? '' : ' (created here)') : repo.how ?? 'unknown'),
      repo.last_action ? [el('dt', {}, 'Last action'), el('dd', {}, repo.last_action)] : null,
      fields.length ? [el('dt', {}, 'Changed'), el('dd', { class: 'warn-text' }, fields.join(', '))] : null),
    tags.length
      ? el('div', { class: 'tags', 'aria-label': 'AI configuration' }, tags.map(tag => el('span', { class: 'tag', title: tag.title }, tag.text)))
      : el('p', { class: 'muted small' }, 'No AI agent files'),
    (repo.agents ?? []).length ? el('div', { class: 'setups' },
      el('p', { class: 'setups-title' }, 'Agent setups'),
      repo.agents.map(setup => el('div', { class: 'setup' },
        el('div', {},
          el('strong', {}, setup.label),
          el('p', { class: 'muted small' }, setup.files.join(', '),
            ' · ', setup.fingerprint
              ? el('span', { class: 'mono', title: `Fingerprint of these files: ${setup.fingerprint}` }, setup.fingerprint.slice(0, 12))
              : 'Fingerprint unavailable')),
        el('button', {
          type: 'button', class: 'secondary',
          onclick: () => startAgentPassport({ repo: repo.path, tool: setup.tool, label: setup.label, files: setup.files }),
        }, 'Create passport')))) : null);
}

if (typeof document !== 'undefined' && isLocalService()) {
  const $ = id => document.getElementById(id);
  const view = { scan: null, filter: 'all', query: '' };
  $('repos-offline').hidden = true;
  $('repos-search').hidden = true;
  $('repos-list').replaceChildren(el('li', { class: 'empty' },
    'No scan yet. Press "Scan repositories" to list the git repositories on this device.'));
  $('scan-repos').hidden = false;

  const render = () => {
    const { scan } = view;
    $('repos-search').hidden = false;
    const totals = summarizeRepos(scan);
    renderTiles($('repos-tiles'), [
      { label: 'Repositories', value: totals.total },
      { label: 'With AI configuration', value: totals.withAi, tone: totals.withAi ? 'ok' : undefined },
      { label: 'Cloned / created here', value: `${totals.cloned} / ${totals.createdHere}` },
      totals.firstScan
        ? { label: 'Changes: first scan, baseline saved', value: '–' }
        : { label: `Changed since ${formatDate(scan.previous_scan_at, true)}`, value: totals.changed,
          tone: totals.changed ? 'warn' : undefined },
      { label: 'Not found again', value: totals.notFound, tone: totals.notFound ? 'warn' : undefined },
    ]);
    renderChips($('repos-filters'), [
      { id: 'all', label: 'All', count: totals.total },
      { id: 'ai', label: 'With AI configuration', count: totals.withAi },
      { id: 'changed', label: 'New or changed', count: totals.changed },
    ], view.filter, filter => { view.filter = filter; render(); });
    const marks = changeMarks(scan.changes);
    const shown = filterRepos(scan, view.filter, view.query);
    $('repos-list').replaceChildren(...(shown.length
      ? shown.map(repo => repoCard(repo, marks[repo.path], changedFields(scan.changes, repo.path)))
      : [el('li', { class: 'empty' }, scan.repos.length ? 'No repositories match this filter.' : 'No repositories found.')]));
    const missing = scan.changes?.not_found ?? [];
    $('repos-missing').replaceChildren(missing.length ? el('details', {},
      el('summary', {}, `${missing.length} repositor${missing.length === 1 ? 'y' : 'ies'} not found again`),
      el('p', { class: 'muted small' }, 'Moved, deleted, or outside the searched folders since the previous scan.'),
      el('ul', {}, missing.map(path => el('li', { class: 'mono' }, path)))) : '');
  };

  $('repos-search').addEventListener('input', event => { view.query = event.target.value; if (view.scan) render(); });
  $('scan-repos').addEventListener('click', async () => {
    const button = $('scan-repos');
    button.disabled = true;
    $('repos-status').textContent = 'Looking for git repositories…';
    try {
      const response = await fetch('/repos/scan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        signal: AbortSignal.timeout(60_000),
      });
      if (!response.ok) throw new Error(`Footprints returned HTTP ${response.status}.`);
      view.scan = await response.json();
      $('repos-status').textContent = `Scanned ${formatDate(view.scan.scanned_at, true)} · ${summaryText(view.scan)}`
        + ` · ${view.scan.scans_in_history} scan${view.scan.scans_in_history === 1 ? '' : 's'} kept on this device`;
      render();
    } catch (error) {
      $('repos-status').textContent = `Repository scan failed. ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

if (typeof module !== 'undefined') {
  // Node tests: the shared formatter lives in ui.js.
  // eslint-disable-next-line no-global-assign
  globalThis.formatDate ??= require('./ui.js').formatDate;
  module.exports = { aiSummary, aiTags, changeMarks, changedFields, summarizeRepos, filterRepos, summaryText };
}
