import { h, mount, api } from './lib.js';
import { icon } from './icons.js';
import { mfaLogin } from './mfa_login.js';

const REDUCED = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const lsGet = k => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };
function flipTheme() {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  const apply = () => { document.documentElement.dataset.theme = t; lsSet('dcn-theme', t); };
  if (document.startViewTransition && !REDUCED()) document.startViewTransition(apply); else apply();
}

const TOOLS = [
  ['Read', 'FIR reading and translation', 'Upload a scanned or typed FIR in English, Hindi or Hinglish. The software reads it, translates it, and pulls out people, phones, vehicles, accounts, dates and legal sections, each with the words it came from.'],
  ['Check', 'Human verification', 'Nothing enters the case until an officer accepts it. Low-confidence facts are flagged first, so review time goes where it matters. No case yet? A pop-up creates it as you upload.'],
  ['Connect', 'Knowledge graph', 'See how people, phones, accounts and places connect across FIRs. Find the shortest link between two people and the groups hiding in the data.'],
  ['Rank', 'Persons of interest', 'A ranked shortlist of who to look at first, with the evidence for every rank. Officers confirm or dismiss each lead; victims are never ranked.'],
  ['Listen', 'Social media intelligence', 'Import lawfully obtained posts. Flag risk language in English, Hindi and Hinglish, spot accounts that post in step, and suggest links to people already in the case. Import only; nothing is scraped.'],
  ['Record', 'Surveillance reports', 'Log each authorised operation with its legal basis, keep a chained field log, and export a report for the supervisor or the court.'],
  ['Prove', 'Tamper-proof ledger', 'Every action is written to a signed, hash-chained ledger. One click checks that no record or evidence file was changed, and exports proof anyone can verify offline.'],
  ['Protect', 'Role-based two-step sign-in', 'Sensitive roles must confirm a code from an authenticator app. Recovery codes, lockout on repeated failures, and every sign-in recorded.'],
];
const FLOW = [['01', 'Upload', 'A FIR or a spreadsheet goes in.'], ['02', 'Verify', 'An officer checks each extracted fact.'], ['03', 'Connect', 'Facts become a graph across cases.'], ['04', 'Prioritise', 'Leads are ranked with reasons.'], ['05', 'Prove', 'Court-ready packs, sealed in the ledger.']];
const ROLES = [
  ['Administrator', 'Runs the system: users, roles, two-step sign-in policy, case access, backups, demo data. Creates accounts after supervisor approval.'],
  ['Supervisor', 'Everything an investigator does, and can change data: upload, edit, assign, approve. First to review access requests.'],
  ['Investigator', 'Investigates and verifies. Can run every analysis and confirm or reject facts, but cannot change case records.'],
  ['Auditor', 'Read-only, personal details masked. Sees the audit log and oversight dashboard.'],
  ['Demo', 'A practice account that only ever sees the built-in demo data.'],
];
const TICKER = ['FIR reading', 'Hindi · Marathi · English', 'Knowledge graph', 'Persons of interest', 'Social media intelligence', 'Surveillance reports', 'Signed ledger', 'Two-step sign-in', 'Offline first'];

function authPanel(login, finish, message) {
  let mode = 'in';
  const msg = h('div', { class: 'au-msg', role: 'status' }, message || '');
  const setMsg = (t, good) => { msg.textContent = t || ''; msg.className = 'au-msg' + (good ? ' good' : t ? ' bad' : ''); };
  const u = h('input', { id: 'username', autocomplete: 'username', placeholder: 'Officer ID', required: true });
  const p = h('input', { id: 'password', type: 'password', autocomplete: 'current-password', placeholder: 'Password', required: true });
  const btn = h('button', { class: 'primary', type: 'submit' }, 'Sign in');
  const signin = h('form', { class: 'au-form', onsubmit: async e => {
    e.preventDefault(); setMsg(''); btn.disabled = true;
    try {
      const r = await login(u.value.trim(), p.value);
      if (r && r.mfa) showMfa(r.mfa);
    } catch (ex) { setMsg(ex.message || 'Sign-in failed'); }
    btn.disabled = false;
  } }, h('label', null, 'Officer ID', u), h('label', null, 'Password', p), btn, h('p', { class: 'au-note' }, 'Authorised use only. Every action is recorded.'));

  const f = { username: h('input', { autocomplete: 'off', required: true, minlength: 3 }), full_name: h('input', { required: true }), email: h('input', { type: 'email', required: true }),
    designation: h('input', { placeholder: 'e.g. Sub-Inspector, Cyber Cell', required: true }), badge_no: h('input', { placeholder: 'optional' }),
    requested_role: h('select', null, [['investigator', 'Investigator (verify and analyse)'], ['supervisor', 'Supervisor (can change data)'], ['auditor', 'Auditor (audit log only)']].map(([v, l]) => h('option', { value: v }, l))),
    reason: h('textarea', { rows: 2, required: true, placeholder: 'Why do you need access?' }), password: h('input', { type: 'password', autocomplete: 'new-password', required: true, minlength: 6, placeholder: 'At least 6 characters' }) };
  const rbtn = h('button', { class: 'primary', type: 'submit' }, 'Send request');
  const request = h('form', { class: 'au-form hidden', onsubmit: async e => {
    e.preventDefault(); setMsg(''); rbtn.disabled = true;
    try {
      const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim()]));
      body.password = f.password.value;
      await api('/auth/signup-request', { method: 'POST', body });
      request.reset(); setMsg('Request received. A supervisor reviews it first, then an administrator creates your account. Sign in once both are done.', true);
    } catch (ex) { setMsg(ex.message || 'Could not send the request'); }
    rbtn.disabled = false;
  } }, h('div', { class: 'au-two' }, h('label', null, 'Choose an Officer ID', f.username), h('label', null, 'Full name', f.full_name)),
    h('div', { class: 'au-two' }, h('label', null, 'Official email', f.email), h('label', null, 'Designation', f.designation)),
    h('div', { class: 'au-two' }, h('label', null, 'Badge no.', f.badge_no), h('label', null, 'Access you need', f.requested_role)),
    h('label', null, 'Reason', f.reason), h('label', null, 'Choose a password', f.password), rbtn,
    h('p', { class: 'au-note' }, 'There is no instant sign-up. Your request goes to a supervisor, then to an administrator.'));

  const tabIn = h('button', { type: 'button', class: 'active', onclick: () => sw('in') }, 'Sign in');
  const tabReq = h('button', { type: 'button', onclick: () => sw('req') }, 'Request access');
  function sw(m) { mode = m; setMsg(''); tabIn.classList.toggle('active', m === 'in'); tabReq.classList.toggle('active', m === 'req'); signin.classList.toggle('hidden', m !== 'in'); request.classList.toggle('hidden', m !== 'req'); }
  const tabs = h('div', { class: 'au-tabs', role: 'tablist' }, tabIn, tabReq);
  const step = h('div', { class: 'au-step hidden' });
  const card = h('div', { class: 'au-card', id: 'auth' }, tabs, signin, request, step, msg);
  function showMfa(first) {
    setMsg(''); p.value = '';
    tabs.classList.add('hidden'); signin.classList.add('hidden'); request.classList.add('hidden'); step.classList.remove('hidden');
    mount(step, mfaLogin(first, { finish, back: () => { step.classList.add('hidden'); tabs.classList.remove('hidden'); sw('in'); setTimeout(() => u.focus(), 20); } }));
  }
  card.open = m => sw(m);
  return card;
}

export function renderLanding(app, { login, finish, message } = {}) {
  const auth = authPanel(login, finish, message);
  const go = id => document.getElementById(id)?.scrollIntoView({ behavior: REDUCED() ? 'auto' : 'smooth', block: 'start' });
  const themeBtn = h('button', { class: 'lp-theme', title: 'Switch light / dark', 'aria-label': 'Switch light or dark theme', onclick: flipTheme }, icon('moon'));
  const nav = h('header', { class: 'lp-nav' },
    h('div', { class: 'lp-brand' }, h('span', { class: 'logo' }, icon('logo')), h('span', null, 'DARK CRIMENET')),
    h('nav', null, h('button', { onclick: () => go('tools') }, 'What it does'), h('button', { onclick: () => go('flow') }, 'How a case flows'), h('button', { onclick: () => go('roles') }, 'Who can do what')),
    h('div', { class: 'lp-nav-r' }, themeBtn, h('button', { class: 'primary', onclick: () => { auth.open('in'); document.getElementById('username')?.focus(); go('top'); } }, 'Sign in')));

  const words = ['Every', 'FIR,', 'connected.'];
  const hero = h('section', { class: 'lp-hero', id: 'top' },
    h('div', { class: 'lp-hero-l' },
      h('span', { class: 'lp-eyebrow' }, 'Investigation intelligence · offline first'),
      h('h1', null, words.map((w, i) => h('span', { class: 'lp-w', style: `--i:${i}` }, h('span', null, i === 2 ? h('em', null, w) : w)))),
      h('p', { class: 'lp-lede' }, 'Read complaints in English and Hindi, check every fact, see how cases connect, and know who to look at first. The software suggests; officers decide. Every step is sealed in a signed ledger that shows if anything is altered.'),
      h('div', { class: 'lp-cta' }, h('button', { class: 'primary big', onclick: () => { auth.open('in'); document.getElementById('username')?.focus(); } }, 'Sign in ', icon('arrow')), h('button', { class: 'big', onclick: () => { auth.open('req'); } }, 'Request access')),
      h('ul', { class: 'lp-trust' }, ['Runs on your own machine', 'Two-step sign-in for sensitive roles', 'Tamper-evident signed ledger'].map(t => h('li', null, icon('check'), t)))),
    h('div', { class: 'lp-hero-r' }, auth));

  const ticker = h('div', { class: 'lp-ticker', 'aria-hidden': 'true' }, h('div', { class: 'lp-track' }, [...TICKER, ...TICKER, ...TICKER].map(t => h('span', null, t, h('i', null, '✦')))));

  const tools = h('section', { class: 'lp-sec', id: 'tools' }, h('div', { class: 'lp-head', 'data-reveal': '' }, h('span', { class: 'lp-eyebrow' }, 'What it does'), h('h2', null, 'Eight jobs, one ', h('em', null, 'secure case file'))),
    h('ol', { class: 'lp-list' }, TOOLS.map(([k, t, d], i) => h('li', { 'data-reveal': '', style: `--d:${i % 4}` }, h('span', { class: 'n' }, String(i + 1).padStart(2, '0')), h('span', { class: 'k' }, k), h('div', null, h('h3', null, t), h('p', null, d))))));

  const flow = h('section', { class: 'lp-sec alt', id: 'flow' }, h('div', { class: 'lp-head', 'data-reveal': '' }, h('span', { class: 'lp-eyebrow' }, 'How a case flows'), h('h2', null, 'From a piece of paper to a ', h('em', null, 'sealed, court-ready pack'))),
    h('div', { class: 'lp-flow' }, FLOW.map(([n, t, d], i) => h('div', { class: 'lp-step', 'data-reveal': '', style: `--d:${i}` }, h('b', null, n), h('h3', null, t), h('p', null, d)))));

  const roles = h('section', { class: 'lp-sec', id: 'roles' }, h('div', { class: 'lp-head', 'data-reveal': '' }, h('span', { class: 'lp-eyebrow' }, 'Who can do what'), h('h2', null, 'Access by ', h('em', null, 'role'), ', by approval')),
    h('div', { class: 'lp-roles' }, ROLES.map(([t, d], i) => h('div', { class: 'lp-role', 'data-reveal': '', style: `--d:${i % 3}` }, h('h3', null, t), h('p', null, d)))),
    h('p', { class: 'lp-fine', 'data-reveal': '' }, 'New users cannot sign themselves up. They send a request; a supervisor approves it; an administrator then creates the account.'));

  const foot = h('footer', { class: 'lp-foot' }, h('span', null, 'DARK CRIMENET · Investigation Intelligence'), h('span', null, 'AI results are investigative signals, not findings of guilt. Demonstration data is synthetic.'));
  const page = h('div', { class: 'lp' }, nav, hero, ticker, tools, flow, roles, foot);
  mount(app, page);

  if ('IntersectionObserver' in window && !REDUCED()) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { threshold: .12 });
    page.querySelectorAll('[data-reveal]').forEach(el => io.observe(el));
  } else page.querySelectorAll('[data-reveal]').forEach(el => el.classList.add('in'));
  const syncIcon = () => { themeBtn.replaceChildren(icon(document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon')); };
  syncIcon(); new MutationObserver(syncIcon).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  setTimeout(() => document.getElementById('username')?.focus({ preventScroll: true }), 700);
}
