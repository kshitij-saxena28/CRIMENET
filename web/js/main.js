import { h, $, mount, api, session, toast, modal, field, clear, table } from './lib.js';
import { icon } from './icons.js';
import { renderLanding } from './landing.js';
import { HELP, GLOSSARY } from './help.js';
import { demoToggle } from './demo_toggle.js';
import { mfaPanel } from './mfa_ui.js';

// [id, label, loader, permission, section, icon, blurb]
const VIEWS = [
  // [id, title, loader, permission(s) - any of, section, icon, one-line description]
  ['command', 'Command Center', () => import('./v_command.js'), ['read'], 'Overview', 'command', 'The picture of the selected case: what is in it, what the AI flagged, and what needs a decision.'],
  ['ingest', 'Ingestion & Review', () => import('./v_ingest.js'), ['write', 'review'], 'Overview', 'ingest', 'Upload FIRs and tables, then check every extracted fact before it enters the case.'],
  ['cases', 'Cases & Notes', () => import('./v_cases.js'), ['read'], 'Overview', 'cases', 'Case workbenches, tasks and notes.'],
  ['suspects', 'Persons of Interest', () => import('./f_suspects.js'), ['analyze'], 'Investigate', 'suspects', 'Who to look at first, and exactly why. You verify or dismiss each lead.'],
  ['graph', 'Knowledge Graph', () => import('./v_graph.js'), ['read'], 'Investigate', 'graph', 'How people, phones, vehicles and accounts are connected.'],
  ['netai', 'Network AI', () => import('./f_netai.js'), ['analyze'], 'Investigate', 'netai', 'Suggested missing links and the people who hold the network together.'],
  ['timeline', 'Timeline & Map', () => import('./v_timeline.js'), ['analyze'], 'Investigate', 'timeline', 'When and where things happened.'],
  ['social', 'Social Media Intelligence', () => import('./f_social.js'), ['read'], 'Intelligence', 'social', 'Import lawfully obtained posts; flag risk language, coordinated accounts and links to the case.'],
  ['surveillance', 'Surveillance Reports', () => import('./f_surveillance.js'), ['read'], 'Intelligence', 'surveillance', 'Authorised operations, a tamper-evident field log, and exportable reports.'],
  ['copilot', 'AI Copilot', () => import('./v_copilot.js'), ['analyze'], 'Intelligence', 'copilot', 'Ask questions of the case. Answers cite their sources.'],
  ['signals', 'Signals & Evidence', () => import('./v_signals.js'), ['read'], 'Intelligence', 'signals', 'Alerts, cross-FIR links and the evidence vault.'],
  ['workflow', 'Approvals & Deadlines', () => import('./f_workflow.js'), ['read'], 'Oversight', 'workflow', 'Approvals, deadlines, watchlists and notifications.'],
  ['integrity', 'Data Integrity', () => import('./f_integrity.js'), ['integrity', 'audit', 'read'], 'Oversight', 'integrity', 'A signed, hash-chained ledger that proves records and evidence were not altered.'],
  ['governance', 'Oversight', () => import('./f_governance.js'), ['oversight'], 'Oversight', 'governance', 'Management dashboard, data protection, backups and court packs.'],
  ['reports', 'Reports & Audit', () => import('./v_reports.js'), ['report', 'audit'], 'Oversight', 'reports', 'Case reports and the tamper-evident audit log.'],
  ['requests', 'Access Requests', () => import('./v_requests.js'), ['signup_review', 'manage_users'], 'Administration', 'requests', 'Review who is asking for access.'],
  ['admin', 'Admin Portal', () => import('./v_admin.js'), ['manage_users'], 'Administration', 'admin', 'Users, roles, two-step sign-in policy, case access and demo data.'],
];
export const ctx = { user: null, cases: [], caseNumber: '', can: p => (ctx.user?.permissions || []).includes(p), canAny: ps => ps.some(p => ctx.can(p)), view: '', refresh: null };

const app = $('#app');
const REDUCED = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const lsGet = k => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };

// ------------------------------------------------------------------ theme
function setTheme(t) {
  const apply = () => { document.documentElement.dataset.theme = t; lsSet('dcn-theme', t); };
  if (document.startViewTransition && !REDUCED()) document.startViewTransition(apply); else apply();
}
const toggleTheme = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

// ------------------------------------------------------------------ sign-in / sign-out
function curtainThen(fn) {
  if (REDUCED()) return fn();
  const c = h('div', { class: 'curtain' }); document.body.append(c); c.classList.add('go');
  setTimeout(fn, 430); setTimeout(() => c.remove(), 1000);
}
async function doLogin(username, password) {
  const r = await api('/auth/login', { method: 'POST', body: { username, password } });
  if (r.mfa_required || r.mfa_setup_required) return { mfa: r };   // the sign-in card shows the second step
  await completeLogin(r);
}
async function completeLogin(r) {
  session.token = r.access_token;
  ctx.user = await api('/me');
  await loadCases();
  curtainThen(renderShell);
}
function signOut(msg) {
  session.token = ''; ctx.user = null; ctx.caseNumber = ''; ctx.view = ''; stopBell();
  document.querySelectorAll('.pop,.overlay').forEach(e => e.remove());
  renderLanding(app, { login: doLogin, finish: completeLogin, message: msg || '' });
  window.scrollTo(0, 0);
}
session.onExpire = () => signOut('Your session expired. Please sign in again.');

async function boot() {
  if (!session.token) return signOut();
  try { ctx.user = await api('/me'); } catch { return signOut(); }
  await loadCases();
  renderShell();
}
async function loadCases() {
  try { ctx.cases = await api('/cases'); } catch { ctx.cases = []; }
  let saved = ''; try { saved = sessionStorage.getItem('dcn-case:' + ctx.user.username) || ''; } catch { /* ignore */ }
  if (!ctx.cases.some(c => c.case_number === ctx.caseNumber)) ctx.caseNumber = '';
  if (!ctx.caseNumber && ctx.cases.some(c => c.case_number === saved)) ctx.caseNumber = saved;
  if (!ctx.caseNumber && ctx.user.role !== 'admin' && ctx.cases.length) ctx.caseNumber = ctx.cases[0].case_number;
}

// ------------------------------------------------------------------ shell: sidebar + workspace
let bellTimer = 0;
const stopBell = () => { clearInterval(bellTimer); bellTimer = 0; };
const visible = () => VIEWS.filter(v => ctx.canAny(v[3]));
let navIdx = 0, navMax = 0, popBound = false;

function renderShell() {
  const allowed = visible();
  const navKids = []; let lastSec = '';
  for (const v of allowed) {
    if (v[4] !== lastSec) { lastSec = v[4]; navKids.push(h('span', { class: 'nav-sec' }, lastSec)); }
    navKids.push(h('button', { 'data-view': v[0], title: v[6], onclick: () => go(v[0]) }, icon(v[5]), h('span', null, v[1])));
  }
  const nav = h('nav', { class: 'nav', 'aria-label': 'Sections' }, navKids);

  const caseSel = h('select', { id: 'case-select', 'aria-label': 'Active case', onchange: e => setCase(e.target.value) });
  const fillCases = () => {
    clear(caseSel);
    if (ctx.user.role === 'admin') caseSel.append(h('option', { value: '' }, 'All cases'));
    else if (!ctx.cases.length) caseSel.append(h('option', { value: '' }, 'No case available'));
    ctx.cases.forEach(c => caseSel.append(h('option', { value: c.case_number, selected: c.case_number === ctx.caseNumber }, `${c.case_number} · ${c.title}`.slice(0, 64))));
    caseSel.value = ctx.caseNumber;
  };
  fillCases();
  ctx.refresh = async () => { await loadCases(); fillCases(); if (ctx.view) go(ctx.view, true); };
  function setCase(v) { ctx.caseNumber = v; try { sessionStorage.setItem('dcn-case:' + ctx.user.username, v); } catch { /* ignore */ } caseSel.value = v; go(ctx.view, true); }
  ctx.setCase = setCase;
  // Used by the "create case" pop-up: pick up a case that was just made without re-drawing the page under the upload.
  ctx.adoptCase = async cn => { await loadCases(); ctx.caseNumber = cn; try { sessionStorage.setItem('dcn-case:' + ctx.user.username, cn); } catch { /* ignore */ } fillCases(); };

  const bellBadge = h('span', { class: 'badge hidden' }, '0');
  const bell = h('button', { class: 'iconbtn', title: 'Notifications', 'aria-label': 'Notifications', onclick: () => go('workflow') }, icon('bell'), bellBadge);
  const themeBtn = h('button', { class: 'iconbtn', title: 'Switch light / dark', 'aria-label': 'Switch light or dark theme', onclick: toggleTheme }, icon('moon'));
  const guideBtn = h('button', { class: 'iconbtn', title: 'Guide and glossary', 'aria-label': 'Guide and glossary', onclick: guideDialog }, h('b', null, '?'));
  const search = h('button', { class: 'searchbtn', onclick: () => openPalette() }, icon('search'), h('span', null, 'Search or jump to…'), h('kbd', { style: 'margin-left:auto' }, '⌘K'));
  const backBtn = h('button', { id: 'nav-back', title: 'Back to the previous page (Alt + ←)', 'aria-label': 'Back', onclick: () => ctx.back() }, icon('back'));
  const fwdBtn = h('button', { id: 'nav-fwd', title: 'Forward (Alt + →)', 'aria-label': 'Forward', onclick: () => history.forward() }, icon('back'));
  fwdBtn.firstChild.style.transform = 'scaleX(-1)';
  ctx._navState = () => { backBtn.disabled = navIdx <= 0; fwdBtn.disabled = navIdx >= navMax; };
  const menuBtn = h('button', { class: 'iconbtn menubtn', 'aria-label': 'Open menu', onclick: () => shell.classList.toggle('open') }, icon('menu'));
  const crumbSec = h('small', null, ''), crumbTitle = h('b', null, '');
  ctx._crumb = (sec, title) => { crumbSec.textContent = sec; crumbTitle.textContent = title; };
  const brand = h('button', { class: 'brand', title: 'Home', onclick: () => go(allowed[0]?.[0] || '') }, h('span', { class: 'logo' }, icon('logo')), h('span', { class: 'brand-text' }, h('b', null, 'DARK CRIMENET'), h('span', null, 'INVESTIGATION INTELLIGENCE')));
  const user = h('button', { class: 'usercard', title: 'Account menu', 'aria-label': 'Account menu', onclick: e => accountMenu(e.currentTarget) },
    h('span', { class: 'avatar' }, ctx.user.username.slice(0, 2)), h('span', { class: 'who' }, h('b', null, ctx.user.username), h('span', null, ctx.user.role)));
  const demoNode = demoToggle(ctx, async () => { await ctx.refresh(); });

  const main = h('main', { id: 'main', tabindex: '-1' });
  const side = h('aside', { class: 'side' }, brand, nav, h('div', { class: 'side-foot' }, user));
  const topbar = h('header', { class: 'topbar' }, menuBtn, h('div', { class: 'navbtns' }, backBtn, fwdBtn), h('div', { class: 'crumb' }, crumbSec, crumbTitle), h('span', { class: 'spacer' }), search, h('div', { class: 'casepick' }, caseSel), demoNode, bell, guideBtn, themeBtn);
  const scrim = h('div', { class: 'scrim', onclick: () => shell.classList.remove('open') });
  const shell = h('div', { class: 'shell' }, side, h('div', { class: 'work' }, topbar, h('div', { class: 'main-col' }, main)));
  shell.addEventListener('click', e => { const nb = e.target.closest('.nav button'); if (nb) { nb.blur(); shell.classList.remove('open'); scrim.remove(); } });
  new MutationObserver(() => { if (shell.classList.contains('open')) shell.append(scrim); else scrim.remove(); }).observe(shell, { attributes: true, attributeFilter: ['class'] });
  mount(app, shell);
  ctx.demoNode = demoNode;

  const poll = async () => { try { const r = await api('/workflow/notifications/unread-count'); bellBadge.textContent = r.unread > 99 ? '99+' : r.unread; bellBadge.classList.toggle('hidden', !r.unread); } catch { /* feature may be off */ } };
  poll(); stopBell(); bellTimer = setInterval(poll, 60000);
  syncThemeIcon(themeBtn);
  new MutationObserver(() => syncThemeIcon(themeBtn)).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  // Back / forward: every page change is a real history entry, so the browser buttons and Alt+arrows work too.
  navIdx = 0; navMax = 0;
  if (!popBound) {
    popBound = true;
    window.addEventListener('popstate', e => {
      if (!ctx.user) return;
      navIdx = (e.state && e.state.i) || 0;
      const id = (location.hash || '').slice(1);
      if (visible().some(v => v[0] === id)) go(id, false, true); else ctx._navState && ctx._navState();
    });
  }
  ctx.back = () => { if (navIdx > 0) history.back(); else { const first = visible()[0]; if (first && ctx.view !== first[0]) go(first[0]); } };
  const hash = (location.hash || '').slice(1);
  ctx.view = '';
  go(allowed.some(v => v[0] === hash) ? hash : allowed[0]?.[0] || '');
}
function syncThemeIcon(btn) { const dark = document.documentElement.dataset.theme === 'dark'; clear(btn); btn.append(icon(dark ? 'sun' : 'moon')); }

let renderToken = 0;
async function go(id, force = false, fromPop = false) {
  const entry = visible().find(v => v[0] === id);
  const main = $('#main'); if (!main || !entry) return;
  if (id === ctx.view && !force) return;
  const first = !ctx.view; ctx.view = id;
  if (first) history.replaceState({ i: 0 }, '', '#' + id);
  else if (!fromPop && !force) { navIdx++; navMax = navIdx; history.pushState({ i: navIdx }, '', '#' + id); }
  ctx._navState && ctx._navState(); ctx._crumb && ctx._crumb(entry[4], entry[1]);
  document.title = `${entry[1]} · DARK CRIMENET`;
  document.querySelectorAll('.nav button').forEach(b => b.classList.toggle('active', b.dataset.view === id));
  document.querySelector('.nav button.active')?.scrollIntoView({ block: 'nearest' });
  const mine = ++renderToken;
  const swap = async () => {
    mount(main, header(entry), h('div', { class: 'grid g3' }, [0, 1, 2].map(() => h('div', { class: 'skel', style: 'height:110px' })), h('div', { class: 'skel', style: 'height:280px;grid-column:1/-1' })));
    main.scrollTop = 0;
  };
  if (document.startViewTransition && !REDUCED() && !first) { try { await document.startViewTransition(swap).updateCallbackDone; } catch { await swap(); } } else await swap();
  try {
    const mod = await entry[2]();
    if (mine !== renderToken) return;
    const body = h('div', { class: 'page-in' });
    mount(main, header(entry), body);
    const needsCase = !['admin', 'requests', 'cases', 'governance', 'reports', 'workflow', 'integrity'].includes(id) && !(id === 'ingest' && ctx.can('write'));
    if (!ctx.caseNumber && ctx.user.role !== 'admin' && needsCase) {
      body.append(h('div', { class: 'notice warn' }, ctx.user.role === 'demo'
        ? 'No demo case is loaded. Switch "Demo data" on in the top bar to load the practice cases.'
        : 'You have no accessible case yet. Ask an administrator to add you to a case.'));
      return;
    }
    await mod.render(body, ctx);
    if (mine === renderToken) stagger(body);
  } catch (ex) {
    if (mine === renderToken) mount(main, header(entry), h('div', { class: 'notice bad' }, ex.message || 'Something went wrong.'));
  }
}
ctx.go = go;
function header(entry) {
  const words = entry[1].split(' '), last = words.pop();
  const hp = HELP[entry[0]];
  return h('div', { class: 'page-head' }, h('span', { class: 'eyebrow' }, entry[4]), h('h1', null, words.length ? words.join(' ') + ' ' : '', h('em', null, last)), h('p', null, entry[6]), hp ? helpPanel(hp, entry[0]) : null);
}
function helpPanel(hp, id) {
  const seen = lsGet('dcn-help-' + id) === '1';
  const d = h('details', { class: 'help', open: !seen });
  if (!seen) lsSet('dcn-help-' + id, '1');
  d.addEventListener('toggle', () => { if (!d.open) lsSet('dcn-help-' + id, '1'); });
  d.append(h('summary', null, 'How this page works'),
    h('div', { class: 'hb' },
      h('div', null, h('h5', null, 'What it is for'), h('p', null, hp.what)),
      h('div', null, h('h5', null, 'How to use it'), h('ol', null, hp.steps.map(x => h('li', null, x)))),
      hp.read ? h('div', null, h('h5', null, 'How to read the results'), h('p', null, hp.read)) : null,
      hp.caution ? h('div', { class: 'caution' }, h('b', null, 'Careful: '), hp.caution) : null));
  return d;
}
function stagger(root) {
  if (REDUCED()) return;
  let i = 0;
  root.querySelectorAll('.card, .kpi, .hero-card, .tw, .notice, .canvas, .start-steps').forEach(el => { if (i > 16 || el.closest('.modal')) return; el.classList.add('rise'); el.style.setProperty('--i', i++); });
}
function guideDialog() {
  const role = ctx.user.role;
  const mine = visible().map(v => h('li', null, h('b', null, v[1]), ' — ', v[6]));
  modal('Guide', h('div', { class: 'grid' },
    h('p', null, `You are signed in as ${role}. Every page has a "How this page works" panel under its title. These are the pages you can use:`),
    h('ul', { style: 'margin:0;padding-left:1.1rem;display:grid;gap:.3rem' }, mine),
    h('h4', null, 'Words used in this software'),
    h('dl', { class: 'kv', style: 'grid-template-columns:170px 1fr' }, GLOSSARY.flatMap(([t, d]) => [h('dt', null, t), h('dd', null, d)]))), { wide: true });
}

// ------------------------------------------------------------------ account menu, dialog, palette, search
function accountMenu(anchor) {
  document.querySelectorAll('.pop').forEach(e => e.remove());
  const r = anchor.getBoundingClientRect();
  const item = (ic, t, fn) => h('button', { class: 'it', onclick: () => { pop.remove(); fn(); } }, icon(ic), t);
  const pop = h('div', { class: 'pop', style: `left:${Math.max(12, r.left)}px;bottom:${innerHeight - r.top + 8}px;transform-origin:bottom left` },
    h('div', { class: 'hd' }, h('b', null, ctx.user.username), h('span', null, ctx.user.role)),
    item('user', 'Account & password', accountDialog), item('sun', 'Toggle theme', toggleTheme), item('logout', 'Sign out', () => signOut()));
  document.body.append(pop);
  const off = e => { if (e.type === 'keydown' ? e.key === 'Escape' : !pop.contains(e.target)) { pop.remove(); document.removeEventListener('pointerdown', off); document.removeEventListener('keydown', off); } };
  setTimeout(() => { document.addEventListener('pointerdown', off); document.addEventListener('keydown', off); }, 0);
}

async function doSearch(q) {
  q = q.trim(); if (q.length < 2) return toast('Type at least 2 characters to search', 'bad');
  try {
    const r = await api('/search', { params: { q, case_number: ctx.caseNumber } });
    modal(`Search: ${q}`, h('div', null,
      h('h4', null, `Entities (${r.entities.length})`), table([{ k: 'external_id', label: 'ID' }, { k: 'name', label: 'Name' }, { k: 'type', label: 'Type' }], r.entities, { emptyText: 'No matching entities.' }),
      h('h4', { style: 'margin-top:1rem' }, `Relationships (${r.relationships.length})`), table([{ k: 'source', label: 'From' }, { k: 'relation', label: 'Relation' }, { k: 'target', label: 'To' }], r.relationships, { emptyText: 'No matching relationships.' }),
      r.documents.length ? [h('h4', { style: 'margin-top:1rem' }, `Documents (${r.documents.length})`), table([{ k: 'document_id', label: 'ID' }, { k: 'filename', label: 'File' }, { k: 'status', label: 'Status' }], r.documents)] : null), { wide: true });
  } catch (ex) { toast(ex.message, 'bad'); }
}

function openPalette() {
  if (document.querySelector('.palette')) return;
  const input = h('input', { placeholder: 'Jump to a page, switch case, or search entities…', 'aria-label': 'Command palette', autocomplete: 'off' });
  const list = h('ul', { role: 'listbox' });
  const acts = [];
  visible().forEach(v => acts.push({ g: 'Go to', t: v[1], ic: v[5], hint: v[4], run: () => go(v[0]) }));
  ctx.cases.forEach(c => acts.push({ g: 'Switch case', t: `${c.case_number} · ${c.title}`, ic: 'cases', hint: c.status, run: () => ctx.setCase(c.case_number) }));
  if (ctx.user.role === 'admin') acts.push({ g: 'Switch case', t: 'All cases', ic: 'cases', hint: '', run: () => ctx.setCase('') });
  acts.push({ g: 'Actions', t: 'Go back', ic: 'back', hint: 'Alt ←', run: () => ctx.back() }, { g: 'Actions', t: 'Switch light / dark theme', ic: 'sun', hint: '', run: toggleTheme }, { g: 'Actions', t: 'Account & password', ic: 'user', hint: '', run: accountDialog }, { g: 'Actions', t: 'Sign out', ic: 'logout', hint: '', run: () => signOut() });
  let shown = [], sel = 0;
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    shown = acts.filter(a => !q || a.t.toLowerCase().includes(q) || a.g.toLowerCase().includes(q)).slice(0, 40);
    if (q.length >= 2 && ctx.can('basic_search')) shown.unshift({ g: 'Search', t: `Search entities for “${input.value.trim()}”`, ic: 'search', hint: 'Enter', run: () => doSearch(input.value) });
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    clear(list); let g = '';
    shown.forEach((a, i) => { if (a.g !== g) { g = a.g; list.append(h('li', { class: 'grp' }, g)); } list.append(h('li', { class: i === sel ? 'sel' : '', role: 'option', onclick: () => choose(i), onmousemove: () => { sel = i; paint(); } }, icon(a.ic), a.t, h('small', null, a.hint))); });
    if (!shown.length) list.append(h('li', { class: 'grp' }, 'No matches'));
  };
  const paint = () => { [...list.querySelectorAll('li:not(.grp)')].forEach((li, i) => li.classList.toggle('sel', i === sel)); list.querySelector('.sel')?.scrollIntoView({ block: 'nearest' }); };
  const close = () => { ov.remove(); document.removeEventListener('keydown', onKey, true); };
  const choose = i => { const a = shown[i]; close(); if (a) a.run(); };
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); paint(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(sel); }
  };
  const ov = h('div', { class: 'overlay', onclick: e => { if (e.target === ov) close(); } }, h('div', { class: 'palette', role: 'dialog', 'aria-label': 'Command palette' }, input, list, h('div', { class: 'foot' }, h('span', null, '↑↓ navigate'), h('span', null, '↵ select'), h('button', { class: 'sm', style: 'margin-left:auto', onclick: () => close() }, 'Close (Esc)'))));
  document.body.append(ov); document.addEventListener('keydown', onKey, true);
  input.addEventListener('input', () => { sel = 0; draw(); }); draw(); setTimeout(() => input.focus(), 30);
}
document.addEventListener('keydown', e => {
  if (!ctx.user) return;
  if (e.key === 'Escape') { const sh = document.querySelector('.shell.open'); if (sh) { sh.classList.remove('open'); return; } }
  if (e.altKey && e.key === 'ArrowLeft' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')) { e.preventDefault(); ctx.back(); return; }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  else if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')) { e.preventDefault(); openPalette(); }
});

function accountDialog() {
  const msg = h('div', { class: 'err', role: 'alert' });
  const cur = h('input', { type: 'password', autocomplete: 'current-password' });
  const np = h('input', { type: 'password', autocomplete: 'new-password' });
  const np2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const nu = h('input', { autocomplete: 'off', placeholder: ctx.user.username });
  const cur2 = h('input', { type: 'password', autocomplete: 'current-password' });
  const m = modal('Account', h('div', { class: 'grid' },
    h('form', { class: 'grid', onsubmit: async e => {
      e.preventDefault(); msg.textContent = '';
      if (np.value !== np2.value) { msg.textContent = 'New passwords do not match'; return; }
      try {
        const r = await api('/auth/change-password', { method: 'POST', body: { current_password: cur.value, new_password: np.value } });
        if (r.access_token) session.token = r.access_token;
        toast('Password changed. Other sessions were signed out.', 'good'); m.close();
      } catch (ex) { msg.textContent = ex.message; }
    } }, h('h4', null, 'Change password'), field('Current password', cur), field('New password (min 6 characters)', np), field('Repeat new password', np2), h('button', { class: 'primary', type: 'submit' }, 'Change password')),
    h('form', { class: 'grid', onsubmit: async e => {
      e.preventDefault(); msg.textContent = '';
      if (!nu.value.trim()) return;
      try { await api('/auth/change-username', { method: 'POST', body: { current_password: cur2.value, new_username: nu.value.trim() } }); m.close(); signOut('Username changed. Sign in with the new name.'); }
      catch (ex) { msg.textContent = ex.message; }
    } }, h('h4', null, 'Change username'), field('New username', nu), field('Current password', cur2), h('button', { type: 'submit' }, 'Change username')),
    h('div', { class: 'grid' }, h('h4', null, 'Two-step sign-in'), mfaPanel(ctx)),
    msg));
}

window.addEventListener('unhandledrejection', e => { if (e.reason && e.reason.status === 401) return; toast(e.reason?.message || 'Unexpected error', 'bad'); });
boot();
