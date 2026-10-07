import { h, api, mount, field, table, tabs, notice, empty, chip, toast, modal, fmtTime, fmtNum } from './lib.js';
import { demoToggle } from './demo_toggle.js';
import { mfaPolicyCard, mfaResetButton } from './mfa_ui.js';

const ROLES = ['admin', 'supervisor', 'investigator', 'auditor', 'demo'];
const ROLE_HELP = {
  admin: 'controls everything', supervisor: 'investigates, changes data, approves sign-up requests', investigator: 'investigates and verifies, never changes data',
  auditor: 'read-only, masked, audit log', demo: 'works only on demo data',
};

export async function render(root, ctx) {
  const sections = {};
  // ---- users
  const userBox = h('div');
  async function loadUsers() {
    const users = await api('/admin/users');
    mount(userBox, table([
      { k: 'username', label: 'User' },
      { label: 'Role', render: u => h('select', { 'aria-label': 'Role for ' + u.username, title: ROLE_HELP[u.role] || '', onchange: async e => {
        try { await api(`/admin/users/${encodeURIComponent(u.username)}/role`, { method: 'PATCH', body: { role: e.target.value } }); toast('Role updated. The user must sign in again.', 'good'); loadUsers(); }
        catch (ex) { toast(ex.message, 'bad'); loadUsers(); } } }, ROLES.map(r => h('option', { value: r, selected: r === u.role }, r))) },
      { label: 'Active', render: u => chip(u.active ? 'active' : 'inactive', u.active ? 'good' : 'bad') },
      { label: 'Two-step', render: u => chip(u.mfa_enabled ? 'on' : 'off', u.mfa_enabled ? 'good' : '') },
      { label: 'Created', render: u => fmtTime(u.created_at) },
      { label: '', render: u => h('div', { class: 'row', style: 'margin:0;gap:.3rem' },
        h('button', { class: 'sm', onclick: () => resetDialog(u.username) }, 'Set password'),
        u.mfa_enabled ? mfaResetButton(u, loadUsers) : null,
        u.active
          ? h('button', { class: 'sm danger', onclick: async () => { try { await api('/admin/users/' + encodeURIComponent(u.username), { method: 'DELETE' }); toast('User deactivated', 'good'); loadUsers(); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Deactivate')
          : h('button', { class: 'sm', onclick: async () => { try { await api(`/admin/users/${encodeURIComponent(u.username)}/activate`, { method: 'POST' }); toast('User activated', 'good'); loadUsers(); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Activate')) },
    ], users));
  }
  function resetDialog(name) {
    const pw = h('input', { type: 'password', autocomplete: 'new-password' }), err = h('div', { class: 'err' });
    const m = modal(`Set password for ${name}`, h('form', { class: 'grid', onsubmit: async e => {
      e.preventDefault();
      try { await api(`/admin/users/${encodeURIComponent(name)}/reset-password`, { method: 'POST', body: { new_password: pw.value } }); toast('Password set. The user was signed out.', 'good'); m.close(); } catch (ex) { err.textContent = ex.message; } } },
      field('New password (demo mode: at least 6 characters; otherwise at least 10 with letters and digits)', pw), err, h('button', { class: 'primary', type: 'submit' }, 'Set password')));
  }
  const nu = h('input', { autocomplete: 'off' }), np = h('input', { type: 'password', autocomplete: 'new-password' });
  const nr = h('select', null, ['investigator', 'supervisor', 'auditor', 'demo', 'admin'].map(r => h('option', { value: r }, r)));
  sections.users = h('div', null,
    h('div', { class: 'card' }, h('h3', null, 'Create user'), h('div', { class: 'row' }, field('Username', nu), field('Password', np), field('Role', nr), h('button', { class: 'primary', onclick: async () => {
      try { await api('/admin/users', { method: 'POST', body: { username: nu.value.trim(), password: np.value, role: nr.value } }); toast('User created', 'good'); nu.value = np.value = ''; loadUsers(); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Create')),
      h('p', { class: 'muted small' }, 'Roles: ' + ROLES.map(r => `${r} (${ROLE_HELP[r]})`).join('; ') + '. People can also ask for an account under Account requests.')),
    h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'Users'), userBox));
  await loadUsers();

  // ---- two-step sign-in policy (which roles must use an authenticator app)
  sections.mfa = mfaPolicyCard(ctx, () => loadUsers().catch(() => {}));

  // ---- account requests (supervisor-approved requests are turned into accounts here)
  const reqBox = h('div');
  async function loadRequests() {
    const mod = await import('./v_requests.js');
    await mod.render(reqBox, ctx);
  }
  sections.requests = reqBox;

  // ---- case access
  const cs = await api('/cases');
  const caseSel = h('select', null, cs.map(c => h('option', { value: c.case_number, selected: c.case_number === ctx.caseNumber }, `${c.case_number} · ${c.title}${c.is_demo ? ' (demo)' : ''}`.slice(0, 70))));
  const caseOut = h('div');
  async function loadCase() {
    if (!caseSel.value) return mount(caseOut, empty('No cases.'));
    const c = caseSel.value, m = await api(`/admin/cases/${encodeURIComponent(c)}/members`);
    const addU = h('input', { placeholder: 'username' });
    mount(caseOut, h('div', { class: 'row center' }, h('span', null, 'Visibility: '), chip(m.visibility, m.visibility === 'restricted' ? 'warn' : 'info'),
      h('button', { class: 'sm', onclick: async () => { try { await api(`/admin/cases/${encodeURIComponent(c)}/visibility`, { method: 'PATCH', body: { visibility: m.visibility === 'restricted' ? 'shared' : 'restricted' } }); loadCase(); ctx.refresh(); } catch (ex) { toast(ex.message, 'bad'); } } }, m.visibility === 'restricted' ? 'Make shared' : 'Make restricted'),
      h('button', { class: 'sm danger', onclick: async () => { try { await api(`/admin/cases/${encodeURIComponent(c)}/archive`, { method: 'POST' }); toast('Case archived', 'good'); await ctx.refresh(); loadCase(); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Archive case')),
      h('p', { class: 'muted small' }, `Created by ${m.created_by || 'unknown'}. Restricted cases are visible only to the creator, members and admins. Demo cases are never visible to real users, whatever the membership.`),
      h('h4', null, 'Members'), m.members.length ? table([{ label: 'User', render: u => u }, { label: '', render: u => h('button', { class: 'sm danger', onclick: async () => { try { await api(`/admin/cases/${encodeURIComponent(c)}/members/${encodeURIComponent(u)}`, { method: 'DELETE' }); loadCase(); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Remove') }], m.members) : empty('No extra members.'),
      h('div', { class: 'row', style: 'margin-top:.6rem' }, addU, h('button', { onclick: async () => { try { await api(`/admin/cases/${encodeURIComponent(c)}/members`, { method: 'POST', body: { username: addU.value.trim() } }); loadCase(); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Add member')));
  }
  caseSel.addEventListener('change', loadCase);
  sections.cases = h('div', { class: 'card' }, h('h3', null, 'Case access'), h('div', { class: 'row' }, field('Case', caseSel)), caseOut);
  await loadCase();

  // ---- demo data
  const demoBox = h('div');
  async function loadDemo() {
    const s = await api('/demo/status');
    mount(demoBox,
      notice(s.enabled ? `Demo data is ON: ${s.case_count} demo cases (${Object.entries(s.counts).filter(([, v]) => v).map(([k, v]) => `${fmtNum(v)} ${k}`).join(', ')}). Recommended case: ${s.recommended_case}.`
        : 'Demo data is OFF. The demo account sees empty screens.', s.enabled ? 'good' : ''),
      h('div', { class: 'row center', style: 'margin-top:.6rem' }, demoToggle(ctx, async () => { await ctx.refresh(); loadDemo(); })),
      s.available ? null : notice('This installation is not in demo mode, so demo data cannot be loaded (it can still be removed).', 'warn'));
  }
  sections.demo = h('div', { class: 'card' }, h('h3', null, 'Demo data'),
    h('p', { class: 'muted small' }, 'Switch on: loads a fully synthetic dataset (no real people) that only the demo account and admins can see. Switch off: removes only that demonstration data. Real cases, users and the audit log are never touched.'), demoBox);
  await loadDemo().catch(ex => mount(demoBox, notice(ex.message, 'warn')));

  const body = h('div');
  const pick = id => { mount(body, sections[id]); if (id === 'requests') loadRequests().catch(ex => mount(reqBox, notice(ex.message, 'bad'))); };
  mount(root, tabs([['users', 'Users'], ['mfa', 'Two-step sign-in'], ['requests', 'Account requests'], ['cases', 'Case access'], ['demo', 'Demo data']], 'users', pick), body);
  pick('users');
}
