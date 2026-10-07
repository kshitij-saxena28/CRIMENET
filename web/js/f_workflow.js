// Workflow: approvals (maker-checker), deadlines / SLA, watchlists, notification centre.
import { h, api, mount, field, table, tabs, notice, empty, chip, toast, modal, download, fmtTime } from './lib.js';

const KIND_LABEL = { document_verification: 'Document verification', report_release: 'Report release', evidence_export: 'Evidence export', case_closure: 'Case closure', legal_hold_release: 'Legal-hold release', custom: 'Custom' };
const STATUS_KIND = { pending: 'warn', approved: 'good', rejected: 'bad', withdrawn: '' };
const SAMPLE_WATCHLIST = { name: 'Sample: vehicles of interest', items: [
  { kind: 'vehicle', value: 'DL32ME5177', note: 'example value' },
  { kind: 'vehicle', value: 'DL 55 HK 5801', note: 'example value - spacing is normalised' },
  { kind: 'phone', value: '+91 90000 00001', note: 'example value' }] };

const CSS = `
.wf-note{border-left:3px solid var(--warn);background:var(--panel2);padding:.5rem .8rem;border-radius:6px;font-size:.8rem;color:var(--muted);margin-bottom:.8rem}
.wf-item{display:flex;gap:.8rem;align-items:flex-start;padding:.7rem .2rem;border-bottom:1px solid var(--line)}
.wf-item:last-child{border-bottom:0}.wf-item .grow{flex:1;min-width:0}.wf-item.unread{border-left:3px solid var(--accent);padding-left:.6rem}
.wf-count{display:inline-block;min-width:1.2rem;padding:0 .35rem;margin-left:.3rem;border-radius:999px;background:var(--bad);color:#fff;font-size:.7rem;text-align:center}
.wf-cd{display:inline-block;min-width:5.2rem;text-align:center}
.wf-sec{margin-bottom:1rem}
`;

const errToast = ex => toast(ex.message || String(ex), 'bad');
const daysChip = d => {
  if (d.state === 'done') return chip('done', 'good');
  if (d.state === 'waived') return chip('waived');
  const n = d.days_left;
  const txt = n < 0 ? `${-n} d overdue` : n === 0 ? 'due today' : `${n} d left`;
  return h('span', { class: 'chip wf-cd ' + (d.state === 'overdue' ? 'bad' : d.state === 'at_risk' ? 'warn' : 'good') }, txt);
};
const row = (...k) => h('div', { class: 'row' }, ...k);

export async function render(root, ctx) {
  const holder = h('div');
  const body = h('div');
  const style = h('style', null, CSS);
  const badge = h('span');
  const canReview = ctx.can('review'), canApprove = ctx.can('approve'), canAnalyze = ctx.can('analyze'), canAssign = ctx.can('assign');
  const cn = ctx.caseNumber;

  async function refreshBadge() {
    try { const r = await api('/workflow/notifications/unread-count'); mount(badge, h('span', { class: r.unread ? 'wf-count' : 'wf-count', style: r.unread ? '' : 'background:var(--panel2);color:var(--muted)', 'aria-label': r.unread + ' unread' }, r.unread)); return r.unread; } catch { return 0; }
  }

  // ------------------------------------------------------------ approvals
  async function approvals() {
    const box = h('div');
    async function load() {
      let all, mine;
      try {
        all = await api('/workflow/approvals', { params: { status: 'pending', case_number: cn } });
        mine = await api('/workflow/approvals', { params: { mine: true, case_number: cn } });
      } catch (ex) { return mount(box, notice(ex.message, 'bad')); }
      const forMe = all.approvals.filter(a => a.requested_by !== ctx.user.username && a.requested_by !== ctx.user.sub);
      const cols = [{ k: 'id', label: '#' }, { label: 'Type', render: a => KIND_LABEL[a.kind] || a.kind }, { k: 'case_number', label: 'Case' }, { label: 'Request', render: a => h('div', null, h('strong', null, a.title), a.ref_id ? h('div', { class: 'muted small' }, 'ref: ' + a.ref_id) : null, a.note ? h('div', { class: 'muted small' }, a.note) : null) }, { k: 'requested_by', label: 'By' }, { label: 'Requested', render: a => fmtTime(a.created_at) }];
      const decideCols = [...cols, { label: '', render: a => h('div', { class: 'row', style: 'margin:0;gap:.3rem' },
        h('button', { class: 'sm primary', onclick: () => decideDialog(a, 'approve') }, 'Approve'),
        h('button', { class: 'sm danger', onclick: () => decideDialog(a, 'reject') }, 'Reject')) }];
      const mineCols = [...cols.filter(c => c.k !== 'requested_by'), { label: 'Status', render: a => h('div', null, chip(a.status, STATUS_KIND[a.status]), a.decided_by && a.status !== 'withdrawn' ? h('div', { class: 'muted small' }, `${a.decided_by}${a.decision_note ? ': ' + a.decision_note : ''}`) : null) },
        { label: '', render: a => a.status === 'pending' ? h('button', { class: 'sm', onclick: async () => { try { await api(`/workflow/approvals/${a.id}/withdraw`, { method: 'POST' }); toast('Request withdrawn'); load(); refreshBadge(); } catch (ex) { errToast(ex); } } }, 'Withdraw') : '' }];
      mount(box,
        canApprove ? h('div', { class: 'card wf-sec' }, h('h3', null, 'Waiting for your decision', forMe.length ? h('span', { class: 'wf-count' }, forMe.length) : null),
          h('p', { class: 'muted small' }, 'Maker-checker: you can never approve a request you raised yourself. A note is required when rejecting.'),
          table(decideCols, forMe, { emptyText: 'Nothing is waiting for you.' })) : null,
        h('div', { class: 'card wf-sec' }, h('h3', null, 'My requests'), table(mineCols, mine.approvals, { emptyText: 'You have not raised any approval requests.' })),
        cn ? null : notice('Select a case to raise a new request.'));
    }
    function decideDialog(a, decision) {
      const note = h('textarea', { rows: 3, maxlength: 2000, placeholder: decision === 'reject' ? 'Reason (required)' : 'Optional note' });
      const m = modal((decision === 'approve' ? 'Approve: ' : 'Reject: ') + a.title, h('div', { class: 'grid' },
        h('p', { class: 'muted small' }, `${KIND_LABEL[a.kind] || a.kind} on ${a.case_number}, requested by ${a.requested_by}. Your decision is written to the audit log.`),
        field('Decision note', note),
        h('button', { class: decision === 'approve' ? 'primary' : 'danger', onclick: async () => {
          if (decision === 'reject' && note.value.trim().length < 3) return toast('A note is required to reject', 'bad');
          try { await api(`/workflow/approvals/${a.id}/decide`, { method: 'POST', body: { decision, note: note.value } }); toast(decision === 'approve' ? 'Approved' : 'Rejected', 'good'); m.close(); load(); refreshBadge(); } catch (ex) { errToast(ex); }
        } }, decision === 'approve' ? 'Confirm approval' : 'Confirm rejection')));
    }
    function requestDialog() {
      const kind = h('select', null, Object.entries(KIND_LABEL).map(([k, l]) => h('option', { value: k }, l)));
      const title = h('input', { maxlength: 255, placeholder: 'e.g. Release investigation report v2' });
      const ref = h('input', { maxlength: 200, placeholder: 'document / report / evidence id (optional)' });
      const note = h('textarea', { rows: 3, maxlength: 2000 });
      const m = modal('Request approval for ' + cn, h('div', { class: 'grid' }, field('Type', kind), field('Title', title), field('Reference', ref), field('Note to approver', note),
        h('button', { class: 'primary', onclick: async () => {
          if (title.value.trim().length < 3) return toast('Give the request a title', 'bad');
          try { await api('/workflow/approvals', { method: 'POST', body: { case_number: cn, kind: kind.value, ref_id: ref.value.trim(), title: title.value.trim(), note: note.value } }); toast('Request sent to approvers', 'good'); m.close(); load(); } catch (ex) { errToast(ex); }
        } }, 'Send request')));
    }
    await load();
    return h('div', null,
      h('div', { class: 'row center' }, h('p', { class: 'muted', style: 'flex:1;margin:0' }, 'Sensitive actions (report release, evidence export, case closure...) need a second person to approve.'),
        canReview && cn ? h('button', { class: 'primary', onclick: requestDialog }, 'Request approval') : null), box);
  }

  // ------------------------------------------------------------ deadlines
  async function deadlines() {
    const wrap = h('div');
    async function load() {
      let up, per = null, tpl;
      try {
        up = await api('/workflow/deadlines/upcoming', { params: { days: 90 } });
        tpl = await api('/workflow/deadline-templates');
        if (cn) per = await api('/workflow/deadlines', { params: { case_number: cn } });
      } catch (ex) { return mount(wrap, notice(ex.message, 'bad')); }
      const s = up.summary;
      const kp = (l, v, kind) => h('div', { class: 'card kpi' }, h('div', { class: 'l' }, l), h('div', { class: 'v', style: kind ? `color:var(--${kind})` : '' }, v ?? 0));
      const upTable = table([{ label: 'Due', render: d => daysChip(d) }, { k: 'due_date', label: 'Date' }, { k: 'case_number', label: 'Case' }, { label: 'Deadline', render: d => d.label }],
        up.deadlines, { emptyText: 'No open deadlines in the next 90 days. Add one from a template on the case below.' });
      const perBox = cn ? h('div', { class: 'card wf-sec' }, h('h3', null, 'Deadlines for ' + cn),
        table([{ label: 'Status', render: d => daysChip(d) }, { k: 'due_date', label: 'Due' }, { k: 'label', label: 'Deadline' }, { k: 'start_date', label: 'From' },
          { label: 'Note', render: d => h('span', { class: 'small muted', style: 'white-space:pre-wrap' }, d.note) },
          { label: '', render: d => canAssign && d.status === 'open' ? h('div', { class: 'row', style: 'margin:0;gap:.3rem' },
            h('button', { class: 'sm primary', onclick: () => patch(d, { status: 'done' }) }, 'Done'),
            h('button', { class: 'sm', onclick: () => noteDialog(d, 'reschedule') }, 'Reschedule'),
            h('button', { class: 'sm', onclick: () => noteDialog(d, 'waive') }, 'Waive')) : '' }], per.deadlines, { emptyText: 'No deadlines set for this case yet.' }),
        h('div', { class: 'row', style: 'margin-top:.6rem' }, h('button', { class: 'sm', onclick: () => ics(cn) }, 'Download .ics (this case)'), h('button', { class: 'sm', onclick: () => ics('') }, 'Download .ics (all my cases)'))) : notice('Select a case to view and add its deadlines.');
      mount(wrap, h('div', { class: 'wf-note' }, tpl.disclaimer),
        h('div', { class: 'grid g3 wf-sec' }, kp('Overdue', s.overdue, 'bad'), kp('At risk (7 days or less)', s.at_risk, 'warn'), kp('On track', s.on_track, 'good')),
        h('div', { class: 'card wf-sec' }, h('h3', null, 'Upcoming across my cases'), upTable),
        perBox, cn && canAssign ? addForm(tpl.templates) : null, tpl.can_configure ? templateAdmin(tpl.templates) : null);
    }
    async function patch(d, body) { try { await api('/workflow/deadlines/' + d.id, { method: 'PATCH', body }); toast('Deadline updated', 'good'); load(); } catch (ex) { errToast(ex); } }
    function noteDialog(d, mode) {
      const date = h('input', { type: 'date' }); const note = h('textarea', { rows: 3, maxlength: 1000, placeholder: 'Why? (required, kept in the audit trail)' });
      const m = modal(mode === 'reschedule' ? 'Reschedule: ' + d.label : 'Waive: ' + d.label, h('div', { class: 'grid' }, mode === 'reschedule' ? field('New due date', date) : null, field('Reason', note),
        h('button', { class: 'primary', onclick: async () => {
          if (note.value.trim().length < 3) return toast('A reason is required', 'bad');
          if (mode === 'reschedule' && !date.value) return toast('Pick the new date', 'bad');
          await patch(d, mode === 'reschedule' ? { due_date: date.value, note: note.value } : { status: 'waived', note: note.value }); m.close();
        } }, 'Save')));
    }
    async function ics(caseNo) {
      try { const res = await api('/workflow/deadlines/ics', { params: { case_number: caseNo }, raw: true }); download(await res.blob(), `deadlines_${caseNo || 'all_cases'}.ics`); } catch (ex) { errToast(ex); }
    }
    function addForm(templates) {
      const sel = h('select', null, h('option', { value: '' }, 'Custom deadline'), templates.filter(t => t.active).map(t => h('option', { value: t.key }, `${t.label} (${t.days} d)`)));
      const start = h('input', { type: 'date' }), due = h('input', { type: 'date' }), label = h('input', { maxlength: 255, placeholder: 'Label (custom deadlines)' }), note = h('input', { maxlength: 1000 });
      const hint = h('div', { class: 'muted small' });
      const upd = () => { const t = templates.find(x => x.key === sel.value); hint.textContent = t ? `Counted from: ${t.anchor}. ${t.note || ''}` : 'Give a label and an explicit due date.'; };
      sel.addEventListener('change', upd); upd();
      return h('div', { class: 'card wf-sec' }, h('h3', null, 'Add a deadline'),
        row(field('Template', sel), field('Start date', start), h('button', { class: 'sm', title: 'Use the FIR date of the earliest verified FIR', onclick: async () => { try { const s = await api('/workflow/deadlines/suggest', { params: { case_number: cn } }); if (s.suggested_start_date) { start.value = s.suggested_start_date; toast(s.reason); } else toast(s.reason, 'warn'); } catch (ex) { errToast(ex); } } }, 'Suggest from FIR'), field('Or explicit due date', due)),
        row(field('Label', label), field('Note', note), h('button', { class: 'primary', onclick: async () => {
          const body = { case_number: cn, template_key: sel.value || null, start_date: start.value || null, due_date: due.value || null, label: label.value || null, note: note.value };
          try { await api('/workflow/deadlines', { method: 'POST', body }); toast('Deadline added', 'good'); load(); } catch (ex) { errToast(ex); }
        } }, 'Add deadline')), hint);
    }
    function templateAdmin(templates) {
      return h('details', { class: 'card wf-sec' }, h('summary', null, 'Configure unit templates (admin)'),
        h('p', { class: 'muted small' }, 'Day counts are reminders set by the unit. Changes are audited. Confirm statutory limits with the legal officer.'),
        table([{ k: 'label', label: 'Template' }, { k: 'anchor', label: 'Counted from' }, { label: 'Days', render: t => { const i = h('input', { type: 'number', min: 1, max: 3650, value: t.days, style: 'width:5rem' }); return h('div', { class: 'row', style: 'margin:0;gap:.3rem' }, i, h('button', { class: 'sm', onclick: async () => { try { await api('/workflow/deadline-templates/' + encodeURIComponent(t.key), { method: 'PUT', body: { days: Number(i.value) } }); toast('Template saved', 'good'); } catch (ex) { errToast(ex); } } }, 'Save')); } }], templates));
    }
    await load();
    return wrap;
  }

  // ------------------------------------------------------------ watchlists
  async function watchlists() {
    const box = h('div');
    async function load() {
      let r;
      try { r = await api('/workflow/watchlists'); } catch (ex) { return mount(box, notice(ex.message, 'bad')); }
      mount(box, r.watchlists.length ? r.watchlists.map(w => card(w, r.kinds)) : empty('No watchlists yet. Create one, or load the sample list.'));
    }
    function card(w, kinds) {
      const mineOrAdmin = w.owner === ctx.user.username || w.owner === ctx.user.sub || ctx.can('manage_users');
      const kind = h('select', { 'aria-label': 'Kind' }, kinds.map(k => h('option', { value: k }, k))), val = h('input', { placeholder: 'value', maxlength: 255 }), note = h('input', { placeholder: 'note (optional)', maxlength: 500 });
      return h('div', { class: 'card wf-sec' },
        h('div', { class: 'row center', style: 'margin:0' }, h('h3', { style: 'margin:0;flex:1' }, w.name, ' ', chip(w.scope === 'unit' ? 'unit-wide' : 'private', w.scope === 'unit' ? 'info' : ''), ' ', chip(w.case_number || 'all my cases'), w.active ? null : chip('paused', 'warn')),
          h('span', { class: 'muted small' }, 'owner: ' + w.owner),
          mineOrAdmin ? h('button', { class: 'sm', onclick: async () => { try { await api('/workflow/watchlists/' + w.id, { method: 'PATCH', body: { active: !w.active } }); load(); } catch (ex) { errToast(ex); } } }, w.active ? 'Pause' : 'Resume') : null,
          mineOrAdmin ? h('button', { class: 'sm danger', onclick: async () => { if (!confirm('Delete this watchlist?')) return; try { await api('/workflow/watchlists/' + w.id, { method: 'DELETE' }); load(); } catch (ex) { errToast(ex); } } }, 'Delete') : null),
        table([{ k: 'kind', label: 'Kind' }, { k: 'value', label: 'Watched value' }, { k: 'note', label: 'Note' }, { label: '', render: i => mineOrAdmin ? h('button', { class: 'sm', onclick: async () => { try { await api(`/workflow/watchlists/${w.id}/items/${i.id}`, { method: 'DELETE' }); load(); } catch (ex) { errToast(ex); } } }, 'Remove') : '' }], w.items, { emptyText: 'No items yet.' }),
        mineOrAdmin ? row(field('Kind', kind), field('Value', val), field('Note', note), h('button', { class: 'sm primary', onclick: async () => { try { await api(`/workflow/watchlists/${w.id}/items`, { method: 'POST', body: { kind: kind.value, value: val.value, note: note.value } }); load(); } catch (ex) { errToast(ex); } } }, 'Add item')) : null);
    }
    function createDialog() {
      const name = h('input', { maxlength: 150 }), scope = h('select', null, h('option', { value: 'private' }, 'Private (only me)'), h('option', { value: 'unit' }, 'Unit-wide (visible to analysts)')), only = h('input', { type: 'checkbox' });
      const m = modal('New watchlist', h('div', { class: 'grid' }, field('Name', name), field('Visibility', scope), cn ? h('label', { style: 'flex-direction:row;align-items:center;gap:.5rem' }, only, 'Limit to case ' + cn) : null,
        h('button', { class: 'primary', onclick: async () => { try { await api('/workflow/watchlists', { method: 'POST', body: { name: name.value, scope: scope.value, case_number: only.checked ? cn : null } }); m.close(); load(); } catch (ex) { errToast(ex); } } }, 'Create')));
    }
    async function sample() {
      try {
        const w = await api('/workflow/watchlists', { method: 'POST', body: { name: SAMPLE_WATCHLIST.name, scope: 'private', case_number: cn || null } });
        for (const it of SAMPLE_WATCHLIST.items) await api(`/workflow/watchlists/${w.id}/items`, { method: 'POST', body: it });
        toast('Sample list created (synthetic values)', 'good'); load();
      } catch (ex) { errToast(ex); }
    }
    async function scan() {
      try {
        const r = await api('/workflow/watchlists/scan', { method: 'POST', body: { case_number: cn } });
        const m = modal(`Scan of ${cn}`, h('div', null, h('p', { class: 'muted small' }, `${r.watchlists_checked} watchlist(s) checked, ${r.notifications_created} new notification(s). ${r.note}`),
          table([{ k: 'watchlist', label: 'Watchlist' }, { k: 'kind', label: 'Kind' }, { k: 'watched_value', label: 'Watching' }, { k: 'matched', label: 'Matched' }, { k: 'reason', label: 'Why' }], r.hits, { emptyText: 'No matches in this case. (No-hit is not proof of absence.)' })), { wide: true });
        refreshBadge(); return m;
      } catch (ex) { errToast(ex); }
    }
    await load();
    return h('div', null, h('div', { class: 'row center' }, h('p', { class: 'muted', style: 'flex:1;margin:0' }, 'Watchlists alert you when a listed phone, vehicle, account, email or name appears in newly ingested data of a case you can access. Matches are normalised (phones: last 10 digits; vehicles: no spaces) and names use a light fuzzy match: signals for review, not findings.'),
      h('button', { class: 'primary', onclick: createDialog }, 'New watchlist'), h('button', { onclick: sample, title: 'Creates a private list of synthetic identifiers' }, 'Load sample'), cn ? h('button', { onclick: scan }, 'Scan case now') : null), box);
  }

  // ------------------------------------------------------------ notifications
  async function notifications() {
    const box = h('div');
    async function load() {
      let r;
      try { r = await api('/workflow/notifications'); } catch (ex) { return mount(box, notice(ex.message, 'bad')); }
      mount(box, h('div', { class: 'card' }, h('div', { class: 'row center', style: 'margin:0' }, h('h3', { style: 'margin:0;flex:1' }, `Inbox (${r.unread} unread)`),
        h('button', { class: 'sm', disabled: !r.unread, onclick: async () => { try { await api('/workflow/notifications/read-all', { method: 'POST' }); load(); refreshBadge(); } catch (ex) { errToast(ex); } } }, 'Mark all read')),
        r.notifications.length ? r.notifications.map(n => h('div', { class: 'wf-item' + (n.read ? '' : ' unread') },
          h('div', { class: 'grow' }, h('div', null, h('strong', null, n.title), ' ', chip(n.kind.replace(/_/g, ' '), n.kind.includes('reject') ? 'bad' : n.kind.includes('approved') ? 'good' : n.kind === 'watchlist_hit' ? 'warn' : 'info'), n.case_number ? ' ' : null, n.case_number ? chip(n.case_number) : null), h('div', { class: 'small' }, n.body), h('div', { class: 'muted small' }, fmtTime(n.created_at))),
          n.read ? null : h('button', { class: 'sm', onclick: async () => { try { await api(`/workflow/notifications/${n.id}/read`, { method: 'POST' }); load(); refreshBadge(); } catch (ex) { errToast(ex); } } }, 'Mark read'))) : empty('No notifications. Approval decisions, watchlist hits and access requests will appear here.')));
    }
    await load();
    return box;
  }

  // ------------------------------------------------------------ shell
  const items = [['approvals', 'Approvals'], ['deadlines', 'Deadlines']];
  if (canAnalyze) items.push(['watchlists', 'Watchlists']);
  items.push(['notifications', 'Notifications']);
  const views = { approvals, deadlines, watchlists, notifications };
  const bar = tabs(items, 'approvals', async id => { mount(body, empty('Loading...')); try { const v = await views[id](); mount(body, v); } catch (ex) { mount(body, notice(ex.message, 'bad')); } refreshBadge(); });
  mount(holder, style, h('div', { class: 'row center', style: 'margin:0' }, h('div', { style: 'flex:1' }, bar), h('span', { class: 'muted small', title: 'Unread notifications' }, 'Unread', badge)), body);
  mount(root, holder);
  refreshBadge();
  try { mount(body, await approvals()); } catch (ex) { mount(body, notice(ex.message, 'bad')); }
}
