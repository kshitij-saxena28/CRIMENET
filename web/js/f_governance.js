// Oversight: management dashboard, data protection (redaction, legal hold, retention, purge), backup, access history,
// legal packs and import connectors. Text only ever enters the DOM through h()/textContent.
import { h, api, mount, kpi, donut, bars, lineChart, columnChart, table, tabs, notice, empty, chip, toast, modal, field, fmtNum, fmtTime, download } from './lib.js';

const CSS = `
.gv-note{border-left:3px solid var(--warn);background:var(--panel2);padding:.5rem .8rem;border-radius:6px;font-size:.8rem;color:var(--muted);margin-bottom:.8rem}
.gv-sec{margin-bottom:1rem}.gv-sec h3{margin-top:0}
.gv-checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.4rem .9rem;margin-bottom:.7rem}
.gv-checks label{display:flex;gap:.45rem;align-items:center;flex-direction:row}
.gv-checks input{width:auto;flex:none;margin:0}
.gv-danger{border:1px solid var(--bad)}
.gv-cli{font-family:ui-monospace,Consolas,monospace;font-size:.78rem;background:var(--panel2);padding:.6rem .8rem;border-radius:8px;white-space:pre-wrap;word-break:break-all}
.gv-ok{color:var(--good)}.gv-bad{color:var(--bad)}
.gv-map select{min-width:180px}
`;
const errToast = ex => toast(ex.message || String(ex), 'bad');
const row = (...k) => h('div', { class: 'row' }, ...k);
const sec = (title, ...kids) => h('div', { class: 'card gv-sec' }, h('h3', null, title), ...kids);
const note = text => h('div', { class: 'gv-note' }, text);
const fmtHours = v => v == null ? '–' : v < 48 ? `${fmtNum(v, 1)} h` : `${fmtNum(v / 24, 1)} d`;
const dayStr = s => s ? String(s).slice(0, 10) : '–';

async function saveDownload(path, opts, fallback) {
  const res = await api(path, { ...opts, raw: true });
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename="?([^";]+)"?/.exec(cd);
  download(await res.blob(), m ? m[1] : fallback);
}
function needCase(ctx) {
  return ctx.caseNumber ? null : notice('Select a case in the header first. These tools always work on one case you can access.', 'warn');
}
const busy = async (btn, fn) => { btn.disabled = true; try { await fn(); } catch (ex) { errToast(ex); } finally { btn.disabled = false; } };

// ------------------------------------------------------------------------------------------------ dashboard
async function dashboardTab(ctx) {
  const box = h('div');
  const sel = h('select', { onchange: () => load() }, [30, 90, 180, 365].map(d => h('option', { value: d, selected: d === 90 }, `Last ${d} days`)));
  async function load() {
    mount(box, empty('Loading…'));
    let d;
    try { d = await api('/governance/dashboard', { params: { days: sel.value } }); } catch (ex) { return mount(box, notice(ex.message, 'bad')); }
    const wf = d.workflow || {};
    const integ = d.evidence.by_integrity || {};
    const bad = (integ.hash_mismatch || 0) + (d.evidence.files_missing || 0);
    mount(box,
      h('div', { class: 'grid g4' },
        kpi('Open cases', fmtNum(d.cases.open), `${fmtNum(d.cases.total)} in scope${d.scope.all_cases ? ' (all cases)' : ''}`),
        kpi('Unverified documents', fmtNum(d.backlog.unverified_documents), d.backlog.oldest_days != null ? `oldest ${fmtNum(d.backlog.oldest_days, 1)} days` : 'no backlog'),
        kpi('Median time to review', fmtHours(d.verification.median_hours), `p90 ${fmtHours(d.verification.p90_hours)} · ${fmtNum(d.verification.reviewed_in_window)} reviews`),
        kpi('Open alerts', fmtNum(d.alerts.open), `${fmtNum(d.alerts.total)} total`),
        kpi('Evidence items', fmtNum(d.evidence.total), bad ? `${bad} integrity issue(s)` : 'no integrity issues'),
        kpi('Pending approvals', wf.available && wf.approvals_pending != null ? fmtNum(wf.approvals_pending) : 'n/a', wf.available ? 'from Approvals & Deadlines' : 'workflow tables not present'),
        kpi('Overdue deadlines', wf.available && wf.deadlines_overdue != null ? fmtNum(wf.deadlines_overdue) : 'n/a', wf.deadlines_open != null ? `${fmtNum(wf.deadlines_open)} open` : ''),
        kpi('Window', `${d.window_days} d`, `generated ${fmtTime(d.generated_at)}`)),
      h('div', { class: 'grid g3', style: 'margin-top:1rem' },
        h('div', { class: 'card' }, h('h4', null, 'Cases by status'), donut(d.cases.by_status)),
        h('div', { class: 'card' }, h('h4', null, 'Open-case age'), bars(d.cases.age_buckets_open)),
        h('div', { class: 'card' }, h('h4', null, 'Unverified-document age'), bars(d.backlog.age_buckets))),
      h('div', { class: 'grid g2', style: 'margin-top:1rem' },
        h('div', { class: 'card' }, h('h4', null, 'Documents ingested per week'), columnChart(d.ingestion_weekly.map(w => ({ x: w.week, y: w.documents })))),
        h('div', { class: 'card' }, h('h4', null, 'Evidence uploaded per week'), lineChart(d.ingestion_weekly.map(w => ({ x: w.week, y: w.evidence }))))),
      h('div', { class: 'grid g3', style: 'margin-top:1rem' },
        h('div', { class: 'card' }, h('h4', null, 'Alerts by severity'), donut(d.alerts.by_severity)),
        h('div', { class: 'card' }, h('h4', null, 'Alerts by status'), bars(d.alerts.by_status)),
        h('div', { class: 'card' }, h('h4', null, 'Evidence integrity'), donut(integ), d.evidence.files_missing ? h('p', { class: 'muted small' }, `${d.evidence.files_missing} stored file(s) missing from disk.`) : null)),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('h4', null, 'Officer workload'),
        table([{ k: 'username', label: 'Officer' }, { k: 'role', label: 'Role' }, { k: 'cases_created', label: 'Cases created', num: true }, { k: 'documents_verified', label: 'Docs verified', num: true },
          { k: 'documents_rejected', label: 'Docs rejected', num: true }, { k: 'tasks_open', label: 'Open tasks', num: true }, { k: 'notes', label: 'Notes', num: true }], d.officers, { emptyText: 'No officer activity in this window.' }),
        h('p', { class: 'muted small' }, 'Counts of recorded actions, for capacity planning. They are not a performance rating.')),
      h('div', { class: 'grid g2', style: 'margin-top:1rem' },
        h('div', { class: 'card' }, h('h4', null, 'Oldest unverified documents'),
          table([{ k: 'case_number', label: 'Case' }, { k: 'filename', label: 'File' }, { k: 'age_days', label: 'Age (days)', num: true }], d.backlog.oldest, { emptyText: 'Nothing waiting for review.' })),
        h('div', { class: 'card' }, h('h4', null, 'Most active cases (audited actions)'),
          table([{ k: 'case_number', label: 'Case' }, { k: 'title', label: 'Title' }, { k: 'audited_actions', label: 'Actions', num: true }], d.top_cases, { emptyText: 'No audited case activity in this window.' }))),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('h4', null, 'Oldest open cases'),
        table([{ k: 'case_number', label: 'Case' }, { k: 'title', label: 'Title' }, { k: 'status', label: 'Status' }, { k: 'age_days', label: 'Age (days)', num: true }], d.cases.oldest_open)),
      h('p', { class: 'muted small' }, d.verification.basis + '. ' + d.note));
  }
  const wrap = h('div', null, row(h('label', null, 'Window', sel)), box);
  await load();
  return wrap;
}

// ------------------------------------------------------------------------------------------------ data protection
const CATS = [['phones', 'Phone numbers'], ['accounts', 'Bank / account numbers'], ['emails', 'E-mail addresses'], ['vehicles', 'Vehicle plates'],
  ['national_ids', 'National IDs (Aadhaar / PAN)'], ['names', 'Person names (known entities & FIR fields)'], ['addresses', 'Addresses & coordinates']];

function exportSection(ctx) {
  const checks = {}; 
  const box = h('div', { class: 'gv-checks' }, CATS.map(([k, label]) => {
    const cb = h('input', { type: 'checkbox', checked: !['names', 'addresses'].includes(k), onchange: () => preview() });
    checks[k] = cb; return h('label', null, cb, label);
  }));
  const mode = h('select', { onchange: () => preview() }, [['pseudonymise', 'Pseudonymise (PERSON-001, keeps links)'], ['mask', 'Mask (show last 4)'], ['remove', 'Remove']].map(([v, l]) => h('option', { value: v }, l)));
  const fmt = h('select', null, [['json', 'JSON'], ['csv', 'CSV'], ['docx', 'Word (.docx)']].map(([v, l]) => h('option', { value: v }, l)));
  const pats = h('textarea', { rows: 2, placeholder: 'Extra terms to redact, one per line (prefix re: for a regular expression)', style: 'width:100%', oninput: () => { clearTimeout(pats._t); pats._t = setTimeout(preview, 500); } });
  const out = h('div');
  const req = () => ({ case_number: ctx.caseNumber, mode: mode.value, format: fmt.value,
    redact: { ...Object.fromEntries(CATS.map(([k]) => [k, checks[k].checked])), free_text_patterns: pats.value.split('\n').map(s => s.trim()).filter(Boolean) } });
  async function preview() {
    if (!ctx.caseNumber) return;
    try {
      const r = await api('/governance/redacted-export', { method: 'POST', body: { ...req(), preview: true } });
      const l = r.redaction_log;
      mount(out, h('div', { class: 'grid g4' }, Object.entries(l.replacements_by_category).map(([k, n]) => kpi(k.replace('_', ' '), fmtNum(n), `${l.distinct_values_by_category[k]} distinct`))),
        h('p', { class: 'muted small' }, `Records: ${Object.entries(l.records).map(([k, n]) => `${n} ${k}`).join(', ')}. Limits: ${l.limitations.join(' ')}`));
    } catch (ex) { mount(out, notice(ex.message, 'warn')); }
  }
  const dl = h('button', { class: 'primary', onclick: () => busy(dl, async () => { await saveDownload('/governance/redacted-export', { method: 'POST', body: req() }, `redacted_export_${ctx.caseNumber}.${fmt.value}`); toast('Redacted export downloaded (audited)', 'good'); }) }, 'Download redacted export');
  const miss = needCase(ctx);
  const s = sec('Redacted export', note('For sharing outside the investigating team. Every export is audited (counts only, never values). Pseudonyms are consistent within one export so links between entities survive, and differ between exports.'),
    miss || h('div', null, box, row(field('Mode', mode), field('Format', fmt)), pats, out, row(dl)));
  if (!miss) preview();
  return s;
}

function holdDialog(c, done) {
  const reason = h('textarea', { rows: 3, style: 'width:100%', placeholder: 'Reason (recorded in the audit log)' });
  const btn = h('button', { class: 'primary' }, c.legal_hold ? 'Release hold' : 'Place legal hold');
  const m = modal(`${c.legal_hold ? 'Release legal hold' : 'Place legal hold'}: ${c.case_number}`, h('div', null,
    c.legal_hold ? notice(`Hold placed by ${c.hold_by}: ${c.hold_reason}`) : notice('While on hold a case cannot be purged.'), field('Reason', reason), row(btn)));
  btn.onclick = () => busy(btn, async () => {
    await api(`/governance/cases/${encodeURIComponent(c.case_number)}/hold`, { method: 'PUT', body: { legal_hold: !c.legal_hold, reason: reason.value.trim() } });
    m.close(); toast('Legal hold updated', 'good'); done();
  });
}
function retentionDialog(c, classes, done) {
  const days = h('input', { type: 'number', min: 1, max: 36500, value: c.retention_days || '', placeholder: 'days (blank = none)' });
  const cls = h('select', null, classes.map(x => h('option', { value: x, selected: x === c.classification }, x)));
  const btn = h('button', { class: 'primary' }, 'Save');
  const m = modal(`Retention & classification: ${c.case_number}`, h('div', null, field('Retention (days after last activity)', days), field('Classification', cls), row(btn)));
  btn.onclick = () => busy(btn, async () => {
    await api(`/governance/cases/${encodeURIComponent(c.case_number)}/retention`, { method: 'PUT', body: { retention_days: days.value ? Number(days.value) : null, classification: cls.value } });
    m.close(); toast('Retention saved', 'good'); done();
  });
}
function purgeDialog(c, done) {
  const confirm = h('input', { autocomplete: 'off', placeholder: c.case_number });
  const reason = h('textarea', { rows: 3, style: 'width:100%', placeholder: 'Legal basis / authority for destruction (min 15 characters)' });
  const result = h('div');
  const dry = h('button', null, '1. Dry run (deletes nothing)');
  const go = h('button', { class: 'primary', style: 'background:var(--bad);border-color:var(--bad)', disabled: true }, '2. Purge permanently');
  let dryFor = '';
  const body = () => ({ confirm_case_number: confirm.value, reason: reason.value.trim() });
  const sync = () => { go.disabled = !(dryFor === confirm.value + '|' + reason.value.trim() && confirm.value === c.case_number && reason.value.trim().length >= 15); };
  confirm.oninput = sync; reason.oninput = sync;
  const m = modal(`Purge case ${c.case_number}`, h('div', null,
    notice('Destructive and irreversible: deletes the case rows, documents, entities, relationships, events and evidence files. The audit log is never touched; a tombstone event with counts and file hashes is added. Blocked while on legal hold.', 'bad'),
    field(`Type the case number exactly (${c.case_number})`, confirm), field('Reason', reason), row(dry, go), result), { wide: true });
  dry.onclick = () => busy(dry, async () => {
    const r = await api(`/governance/cases/${encodeURIComponent(c.case_number)}/purge`, { method: 'POST', body: { ...body(), dry_run: true } });
    dryFor = confirm.value + '|' + reason.value.trim(); sync();
    const s = r.summary;
    mount(result, notice('Dry run only. Review what would be deleted, then confirm.', 'warn'),
      h('div', { class: 'grid g4' }, kpi('Files', fmtNum(s.files_deleted), `${fmtNum(s.bytes_deleted)} bytes`), kpi('Entities', fmtNum(s.entities_deleted), `${s.entities_shared_with_other_cases_detached} shared, detached`),
        kpi('Relationships', fmtNum(s.relationships_deleted)), kpi('Events', fmtNum(s.events_deleted))),
      h('p', { class: 'small' }, 'Rows by table: ' + (Object.entries(s.database_rows_by_table).map(([k, n]) => `${k} ${n}`).join(', ') || 'none')),
      table([{ k: 'ref', label: 'Ref' }, { k: 'filename', label: 'File' }, { k: 'bytes', label: 'Bytes', num: true }, { label: 'SHA-256', render: f => (f.sha256 || '').slice(0, 16) }, { k: 'action', label: 'Action' }], r.files, { emptyText: 'No stored files.' }),
      h('p', { class: 'muted small' }, `Audit chain before: ${r.audit_chain_before.verified ? 'verified' : 'NOT verified'} (${r.audit_chain_before.checked} events).`));
  });
  go.onclick = () => busy(go, async () => {
    const r = await api(`/governance/cases/${encodeURIComponent(c.case_number)}/purge`, { method: 'POST', body: { ...body(), dry_run: false } });
    mount(result, notice(`${r.message} Audit chain after: ${r.audit_chain_after.verified ? 'verified' : 'NOT verified'}.`, r.audit_chain_after.verified ? '' : 'bad'));
    go.disabled = true; dry.disabled = true; toast('Case purged', 'good'); done();
  });
}

async function retentionSection(ctx) {
  const box = h('div');
  async function load() {
    let r;
    try { r = await api('/governance/retention'); } catch (ex) { return mount(box, notice(ex.message, 'bad')); }
    const cols = [{ k: 'case_number', label: 'Case' }, { k: 'title', label: 'Title' }, { k: 'status', label: 'Status' },
      { k: 'classification', label: 'Class.' }, { label: 'Retention', render: c => c.retention_days ? `${c.retention_days} d → ${dayStr(c.retention_due)}` : 'not set' },
      { label: 'Flags', render: c => h('span', null, c.legal_hold ? chip('LEGAL HOLD', 'bad') : null, ' ', c.past_retention ? chip('past retention', 'warn') : null) },
      { label: '', render: c => h('span', { class: 'row', style: 'margin:0' },
        h('button', { class: 'sm', onclick: () => holdDialog(c, load) }, c.legal_hold ? 'Release hold' : 'Hold'),
        h('button', { class: 'sm', onclick: () => retentionDialog(c, r.classifications, load) }, 'Retention'),
        ctx.can('manage_users') ? h('button', { class: 'sm', disabled: c.legal_hold, title: c.legal_hold ? 'Release the legal hold first' : '', onclick: () => purgeDialog(c, load) }, 'Purge…') : null) }];
    mount(box, h('div', { class: 'grid g4' }, kpi('Cases', fmtNum(r.summary.total)), kpi('On legal hold', fmtNum(r.summary.on_hold)), kpi('Past retention', fmtNum(r.summary.past_retention)), kpi('Purge candidates', fmtNum(r.summary.purge_review_candidates), 'not on hold')),
      h('div', { style: 'margin-top:.8rem' }), table(cols, r.cases, { emptyText: 'No cases.' }), h('p', { class: 'muted small' }, r.basis));
  }
  await load();
  return sec('Legal hold, retention & purge', box);
}

async function protectTab(ctx) {
  const wrap = h('div');
  if (ctx.can('report')) wrap.append(exportSection(ctx));
  if (ctx.can('manage_cases')) wrap.append(await retentionSection(ctx));
  if (!wrap.children.length) wrap.append(notice('Your role has no data-protection tools.', 'warn'));
  return wrap;
}

// ------------------------------------------------------------------------------------------------ backup
async function backupTab(ctx) {
  const list = h('div');
  const pw = h('input', { type: 'password', autocomplete: 'new-password', placeholder: 'Passphrase (min 12 characters)' });
  const pw2 = h('input', { type: 'password', autocomplete: 'new-password', placeholder: 'Repeat passphrase' });
  const create = h('button', { class: 'primary', onclick: () => busy(create, async () => {
    if (pw.value !== pw2.value) throw new Error('Passphrases do not match');
    const r = await api('/governance/backup', { method: 'POST', body: { passphrase: pw.value } });
    pw.value = ''; pw2.value = ''; toast(`Backup created: ${r.name}`, 'good'); await load();
  }) }, 'Create encrypted backup');
  function verifyDialog(name) {
    const p = h('input', { type: 'password', autocomplete: 'off', placeholder: 'Backup passphrase' });
    const out = h('div');
    const btn = h('button', { class: 'primary' }, 'Verify');
    modal(`Verify ${name}`, h('div', null, note('Decrypts in memory only, checks every file hash, opens the database and re-verifies the audit chain. Nothing is written to live data.'), field('Passphrase', p), row(btn), out), { wide: true });
    btn.onclick = () => busy(btn, async () => {
      try {
        const r = await api(`/governance/backups/${encodeURIComponent(name)}/verify`, { method: 'POST', body: { passphrase: p.value } });
        mount(out, notice(r.ok ? 'Backup verified.' : 'Backup opened but some checks failed.', r.ok ? '' : 'bad'),
          table([{ label: '', render: c => h('span', { class: c.ok ? 'gv-ok' : 'gv-bad' }, c.ok ? 'PASS' : 'FAIL') }, { k: 'name', label: 'Check' }, { label: 'Detail', render: c => typeof c.detail === 'object' ? (c.detail.reason || '') + (c.detail.checked != null ? ` (${c.detail.checked} events)` : '') : (c.detail || '') }], r.checks),
          h('p', { class: 'muted small' }, `${r.files_checked} files checked · created ${fmtTime(r.manifest.created_at)} by ${r.manifest.created_by} · app ${r.manifest.app_version}`));
      } catch (ex) { mount(out, notice(ex.message, 'bad')); }
    });
  }
  async function load() {
    try {
      const r = await api('/governance/backups');
      mount(list, table([{ k: 'name', label: 'Backup' }, { label: 'Size', num: true, render: b => `${fmtNum(b.bytes / 1024, 0)} KB` }, { label: 'Created', render: b => fmtTime(b.modified + 'Z') },
        { label: '', render: b => h('span', { class: 'row', style: 'margin:0' }, h('button', { class: 'sm', onclick: () => verifyDialog(b.name) }, 'Verify'),
          h('button', { class: 'sm', onclick: () => busy(null_btn(), async () => saveDownload(`/governance/backups/${encodeURIComponent(b.name)}/download`, {}, b.name)) }, 'Download')) }], r.backups, { emptyText: 'No backups yet.' }),
        h('p', { class: 'muted small' }, `Size cap: ${r.size_cap_mb} MB of database + evidence per backup (environment variable BACKUP_MAX_MB).`));
    } catch (ex) { mount(list, notice(ex.message, 'bad')); }
  }
  const null_btn = () => ({ set disabled(v) {} });
  await load();
  return h('div', null,
    sec('Create an encrypted backup', note('A consistent database snapshot plus all evidence files and a SHA-256 manifest, encrypted with a key derived (scrypt) from your passphrase. The passphrase is NOT stored and is not derived from the server secret: if it is lost, the backup cannot be opened.'),
      row(field('Passphrase', pw), field('Repeat', pw2), create)),
    sec('Backups', list),
    sec('Restoring', h('p', { class: 'small' }, 'There is deliberately no restore button. Restores are performed offline into a fresh, empty directory so live data can never be overwritten by accident:'),
      h('div', { class: 'gv-cli' }, 'python scripts/restore_backup.py <backup.dcnbak> --target /path/to/new_empty_dir\n(set SECRET_KEY to the original server secret to also re-verify the audit chain; use --verify-only to check without writing)')));
}

// ------------------------------------------------------------------------------------------------ access & sessions
async function accessTab(ctx) {
  const wrap = h('div');
  const mine = h('div');
  try {
    const r = await api('/governance/my-activity');
    mount(mine, table([{ label: 'Time', render: e => fmtTime(e.time + 'Z') }, { k: 'action', label: 'Action' }, { k: 'case_number', label: 'Case' }], r.events, { emptyText: 'No recorded actions in the last 30 days.' }));
  } catch (ex) { mount(mine, notice(ex.message, 'bad')); }
  wrap.append(sec('My recent activity', h('p', { class: 'muted small' }, 'Your own audited actions. Everything you do in the system is recorded in the tamper-evident audit log.'), mine));
  if (ctx.can('manage_users')) {
    const user = h('input', { placeholder: 'username (blank = everyone)' });
    const days = h('select', null, [7, 30, 90, 180, 365].map(d => h('option', { value: d, selected: d === 30 }, `${d} days`)));
    const box = h('div');
    async function load() {
      try {
        const r = await api('/governance/login-history', { params: { username: user.value.trim(), days: days.value } });
        const s = r.summary;
        mount(box, h('div', { class: 'grid g4' }, kpi('Sign-ins', fmtNum(s.logins || 0)), kpi('Failed sign-ins', fmtNum(s.failed_logins || 0)), kpi('Password changes', fmtNum(s.password_changes || 0)), kpi('Forced sign-outs', fmtNum(s.forced_logouts || 0))),
          h('div', { style: 'margin-top:.8rem' }),
          table([{ k: 'username', label: 'User' }, { k: 'logins', label: 'Sign-ins', num: true }, { k: 'failed_logins', label: 'Failed', num: true }, { label: 'Failed streak (now / max)', render: u => `${u.current_failed_streak} / ${u.max_failed_streak}` },
            { label: 'Last sign-in', render: u => fmtTime(u.last_login && u.last_login + 'Z') }, { label: 'IPs (failed)', render: u => u.ips.join(', ') },
            { label: '', render: u => h('button', { class: 'sm', onclick: () => forceLogout(u.username) }, 'Force sign-out') }], r.users, { emptyText: 'No sign-in activity in this window.' }),
          h('h4', null, 'Events'), table([{ label: 'Time', render: e => fmtTime(e.time + 'Z') }, { k: 'action', label: 'Action' }, { k: 'actor', label: 'Actor' }, { k: 'target', label: 'Target' }, { k: 'ip', label: 'IP' }], r.events.slice(0, 100)),
          h('p', { class: 'muted small' }, r.note));
      } catch (ex) { mount(box, notice(ex.message, 'bad')); }
    }
    async function forceLogout(name) {
      const btn = h('button', { class: 'primary' }, 'Sign them out everywhere');
      const m = modal(`Force sign-out: ${name}`, h('div', null, notice(name === ctx.user?.username ? 'This is your own account: you will be signed out too.' : 'All active sessions of this user become invalid immediately. The action is audited.', 'warn'), row(btn)));
      btn.onclick = () => busy(btn, async () => {
        const r = await api(`/governance/users/${encodeURIComponent(name)}/force-logout`, { method: 'POST' });
        m.close(); toast(r.warning || `Sessions of ${name} revoked`, 'good'); load();
      });
    }
    wrap.append(sec('Sign-in history & sessions', row(field('User', user), field('Window', days), h('button', { onclick: load }, 'Refresh')), box));
    await load();
  }
  return wrap;
}

// ------------------------------------------------------------------------------------------------ legal packs
async function legalTab(ctx) {
  const miss = needCase(ctx);
  if (miss) return miss;
  const cn = ctx.caseNumber;
  const evSel = h('select', { style: 'min-width:320px' });
  let evs = [];
  try { evs = (await api('/evidence', { params: { case_number: cn } })).evidence; } catch (ex) { return notice(ex.message, 'bad'); }
  evs.forEach(e => evSel.append(h('option', { value: e.evidence_id }, `${e.evidence_id} · ${e.filename}`)));
  const dlBtn = (label, path, params, name) => { const b = h('button', { onclick: () => busy(b, async () => { await saveDownload(path, { params }, name); toast('Generated (audited)', 'good'); }) }, label); return b; };
  return h('div', null,
    note('Template aids for the legal officer. They are pre-filled from this system\'s records and must be reviewed, completed and signed by the responsible persons. Format per the current schedule of the Bharatiya Sakshya Adhiniyam, 2023.'),
    sec('Section 63 BSA certificate (electronic records)',
      evs.length ? row(field('Evidence item', evSel),
        h('button', { class: 'primary', onclick: e => busy(e.target, () => saveDownload('/governance/legal/bsa63-certificate', { params: { evidence_id: evSel.value, case_number: cn, format: 'docx' } }, 'bsa63.docx')) }, 'Word (.docx)'),
        h('button', { onclick: e => busy(e.target, () => saveDownload('/governance/legal/bsa63-certificate', { params: { evidence_id: evSel.value, case_number: cn, format: 'pdf' } }, 'bsa63.pdf')) }, 'PDF'))
        : empty('This case has no evidence items yet. Upload evidence first.'),
      h('p', { class: 'muted small' }, 'Part A (producer) and Part B (expert) with the SHA-256 recorded at intake and a re-computed hash check.')),
    sec('Annexures', h('div', { class: 'grid g2' },
      h('div', null, h('h4', null, 'Evidence index & chain of custody'), h('p', { class: 'muted small' }, `${evs.length} item(s) with hashes, uploader, timestamps, integrity state and custody events.`),
        row(dlBtn('Word', '/governance/legal/evidence-index', { case_number: cn, format: 'docx' }, 'evidence_index.docx'), dlBtn('PDF', '/governance/legal/evidence-index', { case_number: cn, format: 'pdf' }, 'evidence_index.pdf'))),
      h('div', null, h('h4', null, 'FIR / document index'), h('p', { class: 'muted small' }, 'Every FIR and document in the case with review status and hash.'),
        row(dlBtn('Word', '/governance/legal/document-index', { case_number: cn, format: 'docx' }, 'document_index.docx'), dlBtn('PDF', '/governance/legal/document-index', { case_number: cn, format: 'pdf' }, 'document_index.pdf'))))));
}

// ------------------------------------------------------------------------------------------------ import
const SAMPLE_CSV = `FIR No,Police Station,District,Complainant Name,Father's Name,Complainant Address,Date of Registration,Date of Occurrence,Place of Occurrence,Sections,Accused Name,Mobile No,Vehicle No,Brief Facts,IO Name,Remarks
0301/2026,Sector 20,Noida,Synthetic Person A,Synthetic Father A,"Flat 1, Synthetic Colony, Noida",02/05/2026,01/05/2026,Synthetic Market Road,"379, 34",Unknown,9000000001,UP16 AB 1001,Two-wheeler reported stolen from a market parking area (synthetic).,SI Demo One,synthetic
0302/2026,Sector 20,Noida,Synthetic Person B,Synthetic Father B,"House 22, Synthetic Sector, Noida",03/05/2026,02/05/2026,Synthetic Bus Stand,420,Synthetic Accused X,9000000002,,Online payment fraud of Rs 12000 reported (synthetic).,SI Demo One,synthetic
`;

async function importTab(ctx) {
  const wrap = h('div');
  const stage = h('div');
  const file = h('input', { type: 'file', accept: '.csv,.xlsx' });
  let profiles = [];
  const loadProfiles = async () => { try { profiles = (await api('/governance/import/profiles')).profiles; } catch { profiles = []; } };
  await loadProfiles();

  async function preview(f) {
    const form = new FormData(); form.append('file', f);
    let p;
    try { p = await api('/governance/import/preview', { method: 'POST', form }); } catch (ex) { return mount(stage, notice(ex.message, 'bad')); }
    const selects = {};
    const fieldOpts = [h('option', { value: '' }, '(ignore)'), ...p.fields.map(f2 => h('option', { value: f2.key }, f2.label + (f2.required ? ' *' : '')))];
    const rowsUi = p.columns.map(c => {
      const s = h('select', null, fieldOpts.map(o => o.cloneNode(true)));
      s.value = c.suggested ? c.suggested.field : '';
      selects[c.name] = s;
      return h('tr', null, h('td', null, c.name), h('td', { class: 'muted small' }, c.samples.filter(Boolean).slice(0, 2).join(' · ')), h('td', { class: 'gv-map' }, s),
        h('td', null, c.suggested ? chip(`${c.suggested.method} ${Math.round(c.suggested.confidence * 100)}%`, c.suggested.confidence >= 0.9 ? 'good' : 'warn') : ''));
    });
    const mapping = () => { const m = {}; for (const [col, s] of Object.entries(selects)) if (s.value && !m[s.value]) m[s.value] = col; return m; };
    const profSel = h('select', null, h('option', { value: '' }, 'Apply saved profile…'), profiles.map(pr => h('option', { value: pr.id }, pr.name)));
    profSel.onchange = () => {
      const pr = profiles.find(x => String(x.id) === profSel.value); if (!pr) return;
      for (const s of Object.values(selects)) s.value = '';
      for (const [fld, col] of Object.entries(pr.mapping)) if (selects[col]) selects[col].value = fld;
    };
    const profName = h('input', { placeholder: 'Profile name' });
    const saveProf = h('button', { onclick: () => busy(saveProf, async () => { if (!profName.value.trim()) return toast('Give the profile a name first', 'bad'); await api('/governance/import/profiles', { method: 'POST', body: { name: profName.value.trim(), mapping: mapping() } }); toast('Profile saved', 'good'); await loadProfiles(); }) }, 'Save mapping as profile');
    const caseSel = h('select', null, (ctx.cases || []).map(c => h('option', { value: c.case_number, selected: c.case_number === ctx.caseNumber }, `${c.case_number} · ${c.title}`)));
    const result = h('div');
    const commit = h('button', { class: 'primary', onclick: () => busy(commit, async () => {
      const r = await api('/governance/import/commit', { method: 'POST', body: { upload_token: p.upload_token, case_number: caseSel.value, mapping: mapping(), profile_name: profSel.selectedOptions[0]?.text?.startsWith('Apply') ? '' : profSel.selectedOptions[0]?.text || '' } });
      mount(result, notice(`${r.imported} FIR document(s) created in Pending Review; ${r.duplicates} duplicate(s); ${r.skipped_count} row(s) skipped. ${r.note}`, r.imported ? '' : 'warn'),
        r.skipped.length ? table([{ k: 'row', label: 'Row', num: true }, { k: 'reason', label: 'Why skipped' }], r.skipped) : null);
      ctx.refresh && ctx.refresh();
    }) }, 'Import as FIRs pending review');
    mount(stage, note(p.note),
      h('p', null, `${p.filename}: ${p.rows} row(s), ${p.columns.length} column(s).`),
      row(profSel, h('span', { class: 'muted small' }, 'Column mapping')),
      h('div', { class: 'tw' }, h('table', null, h('thead', null, h('tr', null, ['Source column', 'Sample values', 'Maps to', 'Suggestion'].map(t => h('th', null, t)))), h('tbody', null, rowsUi))),
      h('h4', null, 'First rows'), table(p.columns.slice(0, 6).map(c => ({ label: c.name, render: r => r[c.name] })), p.preview_rows),
      row(field('Target case', caseSel), commit), row(profName, saveProf), result);
  }
  file.onchange = () => file.files[0] && preview(file.files[0]);
  const sample = h('button', { onclick: () => preview(new File([SAMPLE_CSV], 'sample_cctns_register.csv', { type: 'text/csv' })) }, 'Try with sample (synthetic)');
  wrap.append(sec('Column-mapping wizard (CSV / XLSX, CCTNS-style registers)', note('Rows become FIR-style documents in Pending Review. Nothing is verified automatically: a human reviews each one in Ingestion & Review before it reaches the graph.'), row(field('Register file', file), sample), stage));

  const inbox = h('div');
  async function loadInbox() {
    try {
      const s = await api('/governance/inbox/status');
      mount(inbox, h('div', { class: 'grid g4' }, kpi('Watcher', s.enabled ? (s.watching ? 'running' : 'enabled') : 'disabled', s.enabled ? 'polls every 10 s' : 'set INBOX_WATCH=true'),
        kpi('Processed', fmtNum(s.totals.processed || 0)), kpi('Failed', fmtNum(s.totals.failed || 0)), kpi('Waiting files', fmtNum((s.pending || []).reduce((a, x) => a + x.files, 0)))),
        s.directory ? h('p', { class: 'small' }, 'Folder: ', h('code', null, s.directory + '/<CASE-NUMBER>/')) : null,
        h('p', { class: 'muted small' }, s.how_it_works), s.last_scan_at ? h('p', { class: 'muted small' }, `Last scan ${fmtTime(s.last_scan_at + 'Z')}`) : null,
        table([{ label: 'Time', render: e => fmtTime(e.time + 'Z') }, { k: 'outcome', label: 'Outcome' }, { k: 'case_number', label: 'Case' }, { k: 'file', label: 'File' }, { label: 'Detail', render: e => e.reason || (e.result ? JSON.stringify(e.result) : '') }], s.recent, { emptyText: 'No files processed since the server started.' }));
    } catch (ex) { mount(inbox, notice(ex.message, 'bad')); }
  }
  await loadInbox();
  const scan = h('button', { onclick: () => busy(scan, async () => { const r = await api('/governance/inbox/scan-now', { method: 'POST', body: {} }); toast(`Scan complete: ${r.processed.length} processed, ${r.failed.length} failed`, r.failed.length ? 'bad' : 'good'); await loadInbox(); }) }, 'Scan now');
  wrap.append(sec('Folder inbox', ctx.can('manage_users') ? row(scan, h('button', { onclick: loadInbox }, 'Refresh')) : null, inbox));
  return wrap;
}

// ------------------------------------------------------------------------------------------------ page
export async function render(root, ctx) {
  const items = [];
  if (ctx.can('oversight')) items.push(['dashboard', 'Dashboard', dashboardTab]);
  if (ctx.can('report') || ctx.can('manage_cases')) items.push(['protect', 'Data protection', protectTab]);
  if (ctx.can('manage_users')) items.push(['backup', 'Backup', backupTab]);
  items.push(['access', 'Access & sessions', accessTab]);
  if (ctx.can('report')) items.push(['legal', 'Legal packs', legalTab]);
  if (ctx.can('write')) items.push(['import', 'Import', importTab]);
  const body = h('div');
  const show = async id => {
    const it = items.find(x => x[0] === id);
    mount(body, empty('Loading…'));
    try { mount(body, await it[2](ctx)); } catch (ex) { mount(body, notice(ex.message || String(ex), 'bad')); }
  };
  mount(root, h('style', null, CSS), tabs(items.map(([id, label]) => [id, label]), items[0][0], show), body);
  await show(items[0][0]);
}
