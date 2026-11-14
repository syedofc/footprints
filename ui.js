// Shared page helpers for the Footprints views: tabs, a tiny element builder,
// summary tiles, filter chips and time formatting. Loaded before the views.

function relativeTime(iso, now = Date.now()) {
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return 'unknown';
  const seconds = Math.round((now - time) / 1000);
  if (seconds < 0) return formatDate(iso);
  const steps = [[60, 'second'], [60, 'minute'], [24, 'hour'], [7, 'day'], [4.35, 'week'], [12, 'month']];
  let value = seconds;
  for (const [size, unit] of steps) {
    if (value < size) {
      if (unit === 'second') return 'just now';
      const rounded = Math.floor(value);
      return `${rounded} ${unit}${rounded === 1 ? '' : 's'} ago`;
    }
    value /= size;
  }
  const years = Math.floor(value);
  return `${years} year${years === 1 ? '' : 's'} ago`;
}

function formatDate(iso, withTime = false) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'unknown';
  return withTime
    ? date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : date.toLocaleDateString(undefined, { dateStyle: 'medium' });
}

// el('span', { class: 'badge', title: '…' }, 'text', otherNode)
function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (name === 'class') node.className = value;
    else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

function badge(text, tone = 'muted', title) {
  return el('span', { class: `badge badge-${tone}`, title }, text);
}

const INSTALLATION_SECTIONS = {
  cli: 'Terminal', 'editor-extension': 'Editor', 'browser-extension': 'Browser', app: 'Desktop',
};

function groupedCards(items, sectionFor, renderCard) {
  const groups = new Map();
  for (const item of items) {
    const section = sectionFor(item);
    if (!groups.has(section)) groups.set(section, []);
    groups.get(section).push(renderCard(item));
  }
  return [...groups].map(([section, cards]) => el('li', { class: 'card-group' },
    el('h3', {}, section), el('ul', { class: 'cards' }, cards)));
}

// tiles: [{ label, value, tone?, hint? }]
function renderTiles(container, tiles) {
  container.replaceChildren(...tiles.map(({ label, value, tone, hint }) =>
    el('div', { class: `tile${tone ? ` tile-${tone}` : ''}`, title: hint },
      el('span', { class: 'tile-value' }, String(value)),
      el('span', { class: 'tile-label' }, label))));
}

// options: [{ id, label, count }]; calls onChange(id) when another chip is chosen.
function renderChips(container, options, active, onChange) {
  container.replaceChildren(...options.map(({ id, label, count }) =>
    el('button', {
      type: 'button', class: 'chip', 'aria-pressed': String(id === active),
      onclick: () => onChange(id),
    }, label, el('span', { class: 'chip-count' }, String(count)))));
}

function setupTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab, focus = false) => {
    for (const other of tabs) {
      const selected = other === tab;
      other.setAttribute('aria-selected', String(selected));
      other.tabIndex = selected ? 0 : -1;
      document.getElementById(other.getAttribute('aria-controls')).hidden = !selected;
    }
    if (focus) tab.focus();
    history.replaceState(null, '', `#${tab.dataset.view}`);
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (step) select(tabs[(index + step + tabs.length) % tabs.length], true);
    });
  });
  selectView = view => {
    const tab = tabs.find(candidate => candidate.dataset.view === view);
    if (tab) select(tab);
  };
  select(tabs.find(tab => `#${tab.dataset.view}` === location.hash) ?? tabs[0]);
}

let selectView = null;

// Switch to a view ("device", "repositories", "passports") from another view.
function showView(view) {
  selectView?.(view);
}

const isLocalService = () => typeof location !== 'undefined'
  && location.hostname === '127.0.0.1' && location.pathname === '/footprints/';

if (typeof document !== 'undefined') {
  const connection = document.getElementById('connection');
  document.body.dataset.footprintsMode = isLocalService() ? 'local' : 'sample';
  if (isLocalService()) {
    connection.textContent = 'On this device';
    connection.className = 'badge badge-ok';
    document.getElementById('repos-privacy').hidden = false;
  }
  setupTabs();
}

if (typeof module !== 'undefined') {
  module.exports = { relativeTime, formatDate };
}
