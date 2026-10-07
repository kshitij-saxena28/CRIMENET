// Account requests: supervisors approve/reject first, admins then create the account (or reject).
// There is no self sign-up. ctx.user.role / ctx.can('signup_review') decide what is shown.
import { h, api, mount, table, notice, empty, chip, toast, modal, fmtTime, field } from './lib.js';

const KIND = { pending_supervisor: 'warn', supervisor_approved: 'info', supervisor_rejected: 'bad', created: 'good', rejected: 'bad' };
const LABEL = { pending_supervisor: 'Waiting for supervisor', supervisor_approved: 'Approved by supervisor', supervisor_rejected: 'Rejected by supervisor', created: 'Account created', rejected: 'Rejected' };
const ROLES = ['investigator', 'supervisor', 'auditor', 'demo', 'admin'];

const statusChip = s => chip(LABEL[s] || s, KIND[s] || '');

function person(r) {
  return h('div', null, h('strong', null, r.full_name), h('div', { class: 'muted small' }, `${r.username} · ${r.designation}${r.badge_no ? ' · badge ' + r.badge_no : ''}`), h('div', { class: 'muted small' }, r.email));
}

export async function render(root, ctx) {
  const role = ctx.user && ctx.user.role;
  const isAdmin = role === 'admin';
  const canReview = role === 'supervisor' || (ctx.can && ctx.can('signup_review'));
  const box = h('div');
  mount(root, h('div', { class: 'card' }, h('h3', null, 'Account requests'),
    h('p', { class: 'muted small' }, 'Nobody can create an account for themselves. A supervisor approves a request first, then an administrator creates the account.')), box);

  async function act(fn, okText) {
    try { await fn(); toast(okText, 'good'); await load(); } catch (ex) { toast(ex.message, 'bad'); }
  }

  function decideDialog(r, approve) {
    const note = h('textarea', { rows: 3, maxlength: 1000, placeholder: approve ? 'Optional note for the administrator' : 'Why is this request rejected?' });
    const m = modal(`${approve ? 'Approve' : 'Reject'} request from ${r.full_name}`, h('div', { class: 'grid' }, field('Note', note),
      h('button', { class: approve ? 'primary' : 'danger', onclick: () => act(async () => { await api(`/signup-requests/${r.id}/supervisor-decision`, { method: 'POST', body: { approve, note: note.value.trim() } }); m.close(); }, approve ? 'Request approved' : 'Request rejected') }, approve ? 'Approve' : 'Reject')));
  }
  function rejectDialog(r) {
    const reason = h('textarea', { rows: 3, maxlength: 1000, placeholder: 'Reason (required)' });
    const m = modal(`Reject request from ${r.full_name}`, h('div', { class: 'grid' }, field('Reason', reason),
      h('button', { class: 'danger', onclick: () => { if (reason.value.trim().length < 3) return toast('Please give a reason', 'bad'); act(async () => { await api(`/signup-requests/${r.id}/reject`, { method: 'POST', body: { reason: reason.value.trim() } }); m.close(); }, 'Request rejected'); } }, 'Reject request')));
  }

  function supervisorQueue(rows) {
    const queue = rows.filter(r => r.can_decide);
    return h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, `Waiting for your decision (${queue.length})`),
      queue.length ? queue.map(r => h('div', { class: 'card', style: 'margin:.6rem 0' },
        h('div', { class: 'row center', style: 'justify-content:space-between;margin:0' }, person(r), chip('wants: ' + r.requested_role, 'info')),
        h('p', null, r.reason), h('div', { class: 'muted small' }, 'Requested ' + fmtTime(r.created_at)),
        h('div', { class: 'row', style: 'margin-top:.5rem' }, h('button', { class: 'primary', onclick: () => decideDialog(r, true) }, 'Approve'), h('button', { class: 'danger', onclick: () => decideDialog(r, false) }, 'Reject'))))
        : empty('No requests are waiting for you.'));
  }

  function adminRow(r) {
    const pick = h('select', { 'aria-label': 'Role for ' + r.username, disabled: !r.can_create }, ROLES.map(x => h('option', { value: x, selected: x === r.requested_role }, x)));
    return h('div', { class: 'row', style: 'margin:0;gap:.3rem' }, r.can_create ? pick : null,
      h('button', { class: 'sm primary', disabled: !r.can_create, title: r.can_create ? '' : 'A supervisor must approve first', onclick: () => act(() => api(`/signup-requests/${r.id}/create-user`, { method: 'POST', body: { role: pick.value } }), `Account ${r.username} created`) }, 'Create user'),
      r.can_reject ? h('button', { class: 'sm danger', onclick: () => rejectDialog(r) }, 'Reject') : null);
  }

  function history(rows) {
    const cols = [
      { label: 'Applicant', render: person },
      { label: 'Role wanted', render: r => r.requested_role + (r.final_role && r.final_role !== r.requested_role ? ` → ${r.final_role}` : '') },
      { label: 'Reason', render: r => r.reason },
      { label: 'Status', render: r => statusChip(r.status) },
      { label: 'Supervisor', render: r => r.supervisor_by ? `${r.supervisor_by}${r.supervisor_note ? ': ' + r.supervisor_note : ''}` : '' },
      { label: 'Administrator', render: r => r.admin_by ? `${r.admin_by}${r.admin_note ? ': ' + r.admin_note : ''}` : '' },
      { label: 'Requested', render: r => fmtTime(r.created_at) },
    ];
    if (isAdmin) cols.push({ label: '', render: adminRow });
    return table(cols, rows, { emptyText: 'No requests yet.' });
  }

  async function load() {
    let data;
    try { data = await api('/signup-requests'); } catch (ex) { return mount(box, notice(ex.message, 'bad')); }
    const rows = data.requests || [];
    const counts = h('div', { class: 'row center' }, Object.entries(LABEL).map(([k, v]) => h('span', null, chip(`${v}: ${(data.counts || {})[k] || 0}`, KIND[k]))));
    mount(box,
      isAdmin ? h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'All requests'), counts, history(rows))
        : h('div', null, canReview ? supervisorQueue(rows) : null, h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'History'), history(rows.filter(r => !r.can_decide)))));
  }
  await load();
}
