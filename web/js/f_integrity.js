// Data Integrity: the signed, hash-chained ledger, the integrity check, exports/watermarks and offline verification.
// Text only ever enters the DOM through h()/textContent. Styles: web/css/p_integrity.css (ig2- prefix).
import { h, api, mount, clear, kpi, tabs, table, notice, empty, chip, toast, modal, fmtNum, fmtTime, download } from './lib.js';

const errBox = ex => notice(ex.message || String(ex), 'bad');
const shortHash = s => (s ? s.slice(0, 12) + '…' : '–');
const hashEl = (s, short = false) => h('span', { class: 'ig2-hash' + (short ? ' ig2-short' : ''), title: s || '' }, short ? shortHash(s) : (s || '–'));
const CONCEPTS = [
  ['Block', 'One recorded event: who did what, when, on which case, plus fingerprints of the data involved. Never file contents or personal data.'],
  ['Hash', 'A fixed-length fingerprint (SHA-256). Change a single character of the data and the fingerprint changes completely.'],
  ['Chain', 'Every block stores the fingerprint of the block before it. Editing, deleting or re-ordering any block breaks every link after it.'],
  ['Anchor', 'Every so often the ledger writes one combined fingerprint (a Merkle root) of a whole batch, so a single event can be proven part of the batch with a short proof.'],
  ['Signature', "The server signs every block with a private key that never leaves the server. Anyone holding the public key can check it; nobody can forge it without the key."],
  ['Head', 'The newest block. Print or export its hash: later it is the only way to prove that recent blocks were not silently cut off.'],
];
const VERDICT = { 'INTACT': ['ig2-intact', 'good'], 'TAMPERING DETECTED': ['ig2-tamper', 'bad'], 'PARTIAL': ['ig2-partial', 'warn'] };

async function saveBundle(caseNumber) {
  const res = await api('/integrity/bundle', { raw: true, params: { case_number: caseNumber || '' } });
  const cd = res.headers.get('Content-Disposition') || '';
  const name = (/filename="?([^";]+)"?/.exec(cd) || [])[1] || (caseNumber ? `ledger_bundle_${caseNumber}.json` : 'ledger_bundle_full.json');
  download(await res.blob(), name);
}

function resultView(res) {
  const [cls] = VERDICT[res.verdict] || ['ig2-partial'];
  const ch = res.chain || {}, f = res.files || {}, rw = res.rows || {};
  const tot = Object.values(rw.tables || {}).reduce((a, t) => ({ c: a.c + t.checked, m: a.m + t.mismatch + t.missing + t.unrecorded }), { c: 0, m: 0 });
  const box = (label, good, text, sub) => h('div', { class: 'ig2-check' }, h('b', null, label), h('span', { class: good === true ? 'ig2-ok' : good === false ? 'ig2-no' : 'ig2-mid' }, text), sub ? h('div', { class: 'muted small' }, sub) : null);
  const problems = [
    ...(ch.problems || []).map(p => ({ where: 'Ledger block ' + p.block, kind: p.kind, msg: p.message })),
    ...(f.problems || []).map(p => ({ where: 'Evidence ' + p.evidence_id, kind: p.kind, msg: p.message })),
    ...((rw.problems) || []).map(p => ({ where: `${p.table} ${p.key}`, kind: p.kind, msg: p.message })),
    ...(res.audit_chain && res.audit_chain.verified === false ? [{ where: 'Audit log', kind: 'audit_chain', msg: res.audit_chain.reason || 'Audit chain does not verify' }] : []),
  ];
  return h('div', null,
    h('div', { class: 'ig2-verdict ' + cls, role: 'status' }, h('h3', null, res.verdict), h('div', null, res.explanation),
      h('div', { class: 'muted small', style: 'margin-top:.4rem' }, `Scope: ${res.scope} · checked ${fmtTime(res.checked_at)} · ${res.duration_ms} ms`)),
    h('div', { class: 'ig2-checks' },
      box('Ledger chain', ch.ok, ch.ok ? 'Intact' : `${(ch.problems || []).length} problem(s)`, `${fmtNum(ch.blocks_checked)} blocks, ${fmtNum(ch.anchors_checked)} anchors, ${fmtNum(ch.key_rotations)} key rotation(s)`),
      ch.witness ? box('Witness heads (outside the database)', ch.witness.ok, ch.witness.ok ? 'Match' : 'Mismatch', `${fmtNum(ch.witness.entries)} recorded head(s)`) : null,
      box('Evidence files', f.skipped ? null : !(f.problems || []).some(p => ['file_missing', 'file_altered', 'ledger_mismatch'].includes(p.kind)), f.skipped ? 'Not checked' : `${fmtNum(f.ok)} of ${fmtNum(f.checked)} unchanged`, 'Each file is re-hashed and compared with its hash from upload time'),
      box('Database rows', (rw.problems || []).length === 0, (rw.problems || []).length ? `${rw.problems.length} difference(s)` : 'Match ledger', `${fmtNum(tot.c)} rows compared with the ledger`),
      res.audit_chain ? box('Audit log chain', res.audit_chain.verified, res.audit_chain.verified ? 'Verified' : res.audit_chain.verified === false ? 'Broken' : 'Not checked', res.audit_chain.checked != null ? `${fmtNum(res.audit_chain.checked)} events` : null) : null),
    (res.partial_reasons || []).length ? notice('Incomplete: ' + res.partial_reasons.join('; ') + '.', 'warn') : null,
    problems.length ? h('div', null, h('h4', null, 'What differs'), table([{ k: 'where', label: 'Where' }, { k: 'kind', label: 'Kind', render: r => chip(r.kind.replace(/_/g, ' '), 'bad') }, { k: 'msg', label: 'What was found' }], problems)) : null,
    ch.other_problems ? notice(`${ch.other_problems} ledger problem(s) concern other cases; ask an administrator or auditor.`, 'warn') : null,
    h('div', { class: 'muted small' }, (res.limits || []).map(t => h('p', null, t))));
}

// ---------------------------------------------------------------------------------------------- overview
async function overviewTab(ctx, priv) {
  const cn = ctx.caseNumber || '';
  const out = h('div'), resBox = h('div');
  const scopeAll = h('input', { type: 'checkbox', checked: priv && !cn, disabled: !priv || !cn });
  const st = await api('/integrity/status', { params: { case_number: priv ? '' : cn } });
  const stCase = cn ? await api('/integrity/status', { params: { case_number: cn } }).catch(() => null) : null;
  const lv = st.last_verification;
  const btn = (label, fn, cls = '') => {
    const b = h('button', { class: cls, onclick: async () => { b.disabled = true; try { await fn(); } catch (ex) { toast(ex.message || String(ex), 'bad'); } finally { b.disabled = false; } } }, label);
    return b;
  };
  const run = btn('Run integrity check', async () => {
    mount(resBox, empty('Checking the whole ledger, every evidence file and every recorded row…'));
    const useCase = !priv || !scopeAll.checked;
    const res = await api('/integrity/verify', { method: 'POST', params: { case_number: useCase ? cn : '' } });
    mount(resBox, resultView(res));
  }, 'primary');
  const headBtn = priv ? btn('Show head to record', async () => {
    const hd = await api('/integrity/head');
    modal('Ledger head', h('div', null,
      h('p', null, 'Write this line down or store it somewhere the server cannot reach. If a later bundle does not contain this block hash, blocks were cut off or rewritten.'),
      h('div', { class: 'ig2-pre' }, hd.print_line), h('p', { class: 'muted small' }, hd.note)));
  }) : null;
  const anchorBtn = priv && (ctx.can('write') || ctx.can('manage_users')) ? btn('Anchor now', async () => {
    const r = await api('/integrity/anchor', { method: 'POST' }); toast(r.created ? `Anchor block ${r.index} created` : r.message); ctx.go(ctx.view, true);
  }) : null;
  const bundleBtn = priv ? btn(cn ? 'Download signed bundle (this case)' : 'Download signed bundle (all)', () => saveBundle(cn)) : null;
  const bundleAll = priv && cn && ctx.user.role !== 'supervisor' ? btn('Download full-chain bundle', () => saveBundle('')) : null;
  const baseBtn = ctx.can('manage_users') ? btn('Accept unrecorded rows as baseline', async () => {
    if (!window.confirm('Record every row that has no ledger entry as the trusted starting point? Only do this for data you know is legitimate (for example loaded by a script).')) return;
    const r = await api('/integrity/baseline', { method: 'POST' }); toast(`${r.rows_recorded} row(s) recorded`); ctx.go(ctx.view, true);
  }) : null;
  const rotBtn = ctx.can('manage_users') ? btn('Rotate signing key', async () => {
    if (!window.confirm('Create a new signing key? The rotation is recorded in the ledger, signed by the current key.')) return;
    const r = await api('/integrity/rotate-key', { method: 'POST' }); toast(`New key ${r.new_key_id} active from block ${r.rotation_block + 1}`); ctx.go(ctx.view, true);
  }) : null;

  const kpis = priv ? [
    kpi('Chain length', fmtNum(st.chain_length), st.ledger_gaps ? `${st.ledger_gaps} gap marker(s)` : 'blocks recorded'),
    kpi('Last anchor', st.last_anchor ? `#${st.last_anchor.index}` : 'none yet', st.last_anchor ? `covers blocks ${st.last_anchor.covers[0]}–${st.last_anchor.covers[1]} · ${fmtNum(st.unanchored_blocks)} waiting` : `${fmtNum(st.unanchored_blocks)} block(s) waiting`),
    kpi('Head block', st.head ? `#${st.head.index}` : '–', st.head ? fmtTime(st.head.ts) : ''),
    kpi('Last check', lv ? lv.verdict : 'never', lv ? `${fmtTime(lv.ts)} by ${lv.actor}` : 'run one below'),
  ] : [
    kpi('Ledger events for this case', fmtNum(st.case_blocks), 'recorded and chained'),
    kpi('Ledger length', fmtNum(st.chain_length), 'blocks in total'),
    kpi('Last check of this case', lv ? lv.verdict : 'not run', lv ? fmtTime(lv.ts) : 'run one below'),
  ];
  mount(out,
    h('div', { class: 'ig2-hero' }, h('div', null, h('h2', null, priv ? 'Data Integrity' : 'Integrity of this case'),
      h('p', { class: 'muted' }, 'Every important action (evidence stored or downloaded, documents extracted and reviewed, exports, reports) is written to a signed, hash-chained ledger. The check re-computes everything and shows exactly what no longer matches.'))),
    h('div', { class: 'grid g3' }, kpis),
    priv && st.head ? h('div', { class: 'card', style: 'margin-top:1rem' },
      h('dl', { class: 'kv' },
        h('dt', null, 'Head hash'), h('dd', null, hashEl(st.head.block_hash)),
        st.last_anchor ? [h('dt', null, 'Anchor Merkle root'), h('dd', null, hashEl(st.last_anchor.merkle_root))] : null,
        st.key ? [h('dt', null, 'Signing key'), h('dd', null, h('span', { class: 'ig2-hash' }, st.key.fingerprint), ' ', chip(`id ${st.key.key_id}`, 'info'), st.key.rotations ? chip(`${st.key.rotations} rotation(s)`) : null)] : null,
        h('dt', null, 'Encryption at rest'), h('dd', null, st.encryption_at_rest.enabled ? chip('evidence files encrypted', 'good') : chip('off (set EVIDENCE_ENCRYPTION_KEY to enable)'), ' '),
        h('dt', null, 'Witness copy of heads'), h('dd', null, st.witness_file ? chip('kept outside the database', 'good') : chip('not written yet', 'warn')),
        st.pending_failures ? [h('dt', null, 'Write failures'), h('dd', null, chip(`${st.pending_failures} event(s) not yet recorded`, 'bad'))] : null)) : null,
    stCase && cn ? h('div', { class: 'muted small', style: 'margin-top:.6rem' }, `Case ${cn}: ${fmtNum(stCase.case_blocks)} ledger event(s).`) : null,
    h('div', { class: 'ig2-actions' }, run, priv && cn ? h('label', { class: 'row center', style: 'margin:0;gap:.35rem' }, scopeAll, 'whole system (not only this case)') : null, headBtn, anchorBtn, bundleBtn, bundleAll, baseBtn, rotBtn),
    !priv ? notice('This is a read-only view. It checks the ledger and only the evidence, documents and records of the selected case.', 'info') : null,
    resBox);
  return out;
}

// ---------------------------------------------------------------------------------------------- ledger browser
const EVENT_TYPES = ['', 'document_extracted', 'document_reviewed', 'document_reprocessed', 'table_ingested', 'evidence_uploaded', 'evidence_downloaded', 'evidence_verified',
  'case_created', 'entity_created', 'relationship_created', 'export_generated', 'export_anomaly', 'social_imported', 'social_item_flagged', 'surveillance_entry_added',
  'surveillance_entry_amended', 'surveillance_report_generated', 'anchor', 'key_rotation', 'ledger_gap', 'integrity_check', 'demo_enabled', 'demo_disabled', 'case_purged', 'baseline_accepted'];

async function ledgerTab(ctx, priv) {
  const state = { case: priv ? '' : ctx.caseNumber, type: '', after: null, rows: [] };
  const list = h('div'), more = h('div');
  const caseIn = h('select', { onchange: e => { state.case = e.target.value; reset(); } },
    priv ? h('option', { value: '' }, 'All cases') : null, (ctx.cases || []).map(c => h('option', { value: c.case_number, selected: c.case_number === state.case }, c.case_number)));
  const typeIn = h('select', { onchange: e => { state.type = e.target.value; reset(); } }, EVENT_TYPES.map(t => h('option', { value: t }, t || 'All events')));
  async function load() {
    const r = await api('/integrity/blocks', { params: { case_number: state.case, event_type: state.type, limit: 50, desc: true, after: state.after ?? '' } });
    state.rows.push(...r.blocks); state.after = r.next; draw();
  }
  function draw() {
    mount(list, table([
      { k: 'index', label: '#', num: true }, { k: 'ts', label: 'Time (UTC)', render: r => r.ts.replace('T', ' ').slice(0, 19) },
      { k: 'event_type', label: 'Event', render: r => chip(r.event_type, r.event_type === 'export_anomaly' || r.event_type === 'ledger_gap' ? 'bad' : '') },
      { k: 'actor', label: 'Actor' }, { k: 'case_number', label: 'Case' }, { k: 'ref', label: 'Ref' }, { k: 'block_hash', label: 'Block hash', render: r => hashEl(r.block_hash, true) },
    ], state.rows, { onRow: r => detail(r.index), emptyText: 'No ledger blocks match.' }));
    mount(more, state.after != null ? h('button', { onclick: () => load().catch(ex => toast(ex.message, 'bad')) }, 'Load older blocks') : null);
  }
  function reset() { state.rows = []; state.after = null; load().catch(ex => mount(list, errBox(ex))); }
  async function detail(idx) {
    const b = await api('/integrity/blocks/' + idx);
    const proofBox = h('div');
    const proofBtn = h('button', { onclick: async () => {
      try {
        const p = await api('/integrity/proof/' + idx);
        mount(proofBox, h('p', { class: p.valid ? 'muted' : '' }, `Anchor block ${p.anchor_index} · Merkle root `, hashEl(p.merkle_root), p.valid ? ' · proof valid' : ' · PROOF DOES NOT MATCH'),
          h('p', { class: 'muted small' }, p.explanation),
          h('div', { class: 'ig2-path' }, p.path.map(([side, hs]) => h('div', null, h('span', { class: 'ig2-side' }, side), hashEl(hs, false)))));
      } catch (ex) { mount(proofBox, notice(ex.message, 'warn')); }
    } }, 'Show Merkle inclusion proof');
    modal(`Block ${b.index} · ${b.event_type}`, h('div', null,
      h('dl', { class: 'kv' },
        h('dt', null, 'Time'), h('dd', null, b.ts), h('dt', null, 'Actor'), h('dd', null, b.actor || '–'), h('dt', null, 'Case'), h('dd', null, b.case_number || '–'),
        h('dt', null, 'Reference'), h('dd', null, b.ref || '–'), h('dt', null, 'Payload hash'), h('dd', null, hashEl(b.payload_sha256)),
        h('dt', null, 'Previous hash'), h('dd', null, hashEl(b.prev_hash)), h('dt', null, 'Block hash'), h('dd', null, hashEl(b.block_hash)),
        h('dt', null, 'Signature'), h('dd', null, hashEl(b.signature), ' ', chip(`key ${b.key_id}`, 'info')),
        h('dt', null, 'Link to next'), h('dd', null, b.next_block_prev_hash_matches == null ? 'newest block' : b.next_block_prev_hash_matches ? chip('next block points here', 'good') : chip('next block does NOT point here', 'bad'))),
      h('h4', null, 'Recorded content'), h('div', { class: 'ig2-pre' }, JSON.stringify(b.payload, null, 2)), proofBtn, proofBox), { wide: true });
  }
  mount(list, empty('Loading…'));
  load().catch(ex => mount(list, errBox(ex)));
  return h('div', null,
    h('div', { class: 'ig2-filters' }, h('label', null, 'Case', caseIn), h('label', null, 'Event', typeIn)),
    h('p', { class: 'muted small' }, 'Click a block to see its fingerprints, signature and a Merkle inclusion proof. Blocks hold ids, hashes and counts only.'), list, more);
}

// ---------------------------------------------------------------------------------------------- exports
async function exportsTab(ctx) {
  const res = h('div'), found = h('div');
  const wm = h('input', { placeholder: 'WM-XXXXXXXXXX' }), who = h('input', { placeholder: 'user name' });
  async function load(extra = {}) {
    mount(res, empty('Loading…'));
    try {
      const d = await api('/integrity/exports', { params: { case_number: ctx.caseNumber || '', actor: who.value.trim(), watermark: wm.value.trim(), ...extra } });
      const busy = Object.entries(d.recent_by_user || {}).sort((a, b) => b[1] - a[1]);
      mount(res,
        busy.length ? notice(`Last ${d.window_minutes} min: ` + busy.map(([u, n]) => `${u} ${n}`).join(', ') + ` (alert above ${d.threshold})`, busy.some(([, n]) => n > d.threshold) ? 'bad' : 'info') : null,
        table([{ k: 'ledger_ref', label: 'Ref' }, { k: 'ts', label: 'Time (UTC)', render: r => r.ts.replace('T', ' ').slice(0, 19) }, { k: 'actor', label: 'Who' },
          { k: 'kind', label: 'What', render: r => chip(r.kind) }, { k: 'format', label: 'Format' }, { k: 'case_number', label: 'Case' }, { k: 'watermark_id', label: 'Watermark' },
          { k: 'bytes', label: 'Bytes', num: true, render: r => r.bytes != null ? fmtNum(r.bytes) : '–' }], d.exports, { emptyText: 'No exports recorded.' }));
    } catch (ex) { mount(res, errBox(ex)); }
  }
  const pick = h('input', { type: 'file', onchange: async e => {
    const f = e.target.files[0]; if (!f) return;
    const dig = await crypto.subtle.digest('SHA-256', await f.arrayBuffer());
    const hex = [...new Uint8Array(dig)].map(x => x.toString(16).padStart(2, '0')).join('');
    try {
      const d = await api('/integrity/exports', { params: { sha256: hex } });
      mount(found, d.exports.length ? notice(`This exact file was exported: ${d.exports.map(x => `${x.ledger_ref} by ${x.actor} at ${x.ts.replace('T', ' ').slice(0, 19)} UTC (${x.kind}, ${x.watermark_id || 'no watermark'})`).join('; ')}`, 'good')
        : notice('No export in the ledger has this file hash (it was edited after export, is a different file, or was not produced here). Its SHA-256: ' + hex, 'warn'));
    } catch (ex) { mount(found, errBox(ex)); }
  } });
  await load();
  return h('div', null,
    notice('Every report, redacted export, legal pack, backup download and evidence download is recorded with who, when and a file hash; documents also carry a visible watermark (user, time, ledger reference). A determined insider can still photograph a screen: these controls make leaks traceable, not impossible.', 'info'),
    h('div', { class: 'ig2-filters' }, h('label', null, 'Watermark id', wm), h('label', null, 'User', who), h('button', { onclick: () => load() }, 'Filter')),
    res,
    h('h4', null, 'Trace a file'), h('div', { class: 'ig2-drop' }, h('p', { class: 'muted small' }, 'Pick a file you found. Its SHA-256 is computed in your browser and compared with the exports in the ledger; the file itself is not uploaded.'), pick, found));
}

// ---------------------------------------------------------------------------------------------- offline + concepts
function offlineTab() {
  return h('div', { class: 'card' }, h('h3', null, 'How to verify offline'),
    h('p', { class: 'muted' }, 'A signed bundle can be checked on any computer with Python, without the server, the database or a network. This is how a court, an auditor or another department can check the records independently.'),
    h('ol', { class: 'ig2-steps' },
      h('li', null, 'Download the signed bundle from the Overview tab (a case slice, or the full chain if you are an administrator or auditor).'),
      h('li', null, 'Copy scripts/verify_ledger.py and the bundle to the checking computer.'),
      h('li', null, 'Run the command below. Add --trust-key with the key fingerprint you noted when the system was set up, and --expect-head with a head hash you recorded earlier.'),
      h('li', null, 'The script prints VERIFIED or TAMPERING DETECTED with the exact blocks concerned (exit code 0 or 1).')),
    h('div', { class: 'ig2-pre' }, 'python scripts/verify_ledger.py bundle.json --trust-key <key fingerprint> --expect-head <head hash>'),
    notice('Removing the newest blocks can only be detected against a head hash recorded somewhere the server cannot change (on paper, in the case file, in the witness file). Record the head after important milestones.', 'warn'),
    h('h4', null, 'What it cannot prove'),
    h('ul', null, h('li', null, 'That data was true when it was entered; only that it was not changed afterwards.'), h('li', null, 'Anything about data changed before the ledger began, or in tables the ledger does not record.'),
      h('li', null, 'That nobody photographed a screen or copied a file; watermarks make leaks attributable, not impossible.')));
}
function conceptsTab() {
  return h('div', { class: 'ig2-concepts' }, CONCEPTS.map(([t, d]) => h('div', { class: 'ig2-concept' }, h('b', null, t), h('span', { class: 'muted' }, d))));
}

export async function render(root, ctx) {
  const priv = ctx.can('integrity') || ctx.can('audit');
  if (!priv && !ctx.caseNumber) { mount(root, notice('Select a case to see the integrity of its records.', 'warn')); return; }
  const items = [['overview', 'Overview'], ['ledger', 'Ledger']];
  if (priv) items.push(['exports', 'Exports']);
  items.push(['offline', 'Verify offline'], ['concepts', 'Concepts']);
  const body = h('div', { class: 'ig2-page' });
  const show = async id => {
    mount(body, empty('Loading…'));
    try {
      const node = id === 'overview' ? await overviewTab(ctx, priv) : id === 'ledger' ? await ledgerTab(ctx, priv) : id === 'exports' ? await exportsTab(ctx) : id === 'offline' ? offlineTab() : conceptsTab();
      mount(body, node);
    } catch (ex) { mount(body, errBox(ex)); }
  };
  mount(root, tabs(items, 'overview', show), body);
  await show('overview');
}
