import { h, api, mount, field, table, tabs, notice, empty, chip, toast, download, fmtTime, pct, session } from './lib.js';

const ALERT_STATUSES = ['New', 'Reviewing', 'Confirmed', 'Escalated', 'Reviewed', 'Dismissed', 'False Positive'];

export async function render(root, ctx) {
  const p = { case_number: ctx.caseNumber };
  const sections = {};
  // ---- alerts
  const alertBox = h('div');
  async function loadAlerts() {
    const alerts = await api('/alerts', { params: p });
    mount(alertBox, table([{ k: 'code', label: 'Code' }, { label: 'Severity', render: a => chip(a.severity, a.severity === 'High' ? 'bad' : 'warn') }, { k: 'title', label: 'Signal' }, { k: 'entity_id', label: 'Entity' }, { label: 'Conf.', render: a => pct(a.confidence) },
      { label: 'Why', render: a => (a.explanation || []).join('; ') },
      { label: 'Status', render: a => ctx.can('review') ? h('select', { 'aria-label': 'Alert status', onchange: async e => { try { await api('/alerts/' + encodeURIComponent(a.code), { method: 'PATCH', body: { status: e.target.value } }); toast('Alert updated', 'good'); } catch (ex) { toast(ex.message, 'bad'); loadAlerts(); } } }, ALERT_STATUSES.map(s => h('option', { value: s, selected: s === a.status }, s))) : a.status }], alerts, { emptyText: 'No alerts.' }));
  }
  sections.alerts = h('div', { class: 'card' }, h('p', { class: 'muted small' }, 'Alerts prioritise items for human review. Every alert lists its reasons and source references.'), alertBox);

  // ---- FIR board
  if (ctx.can('analyze')) {
    const fr = await api('/fir-relations', { params: p });
    sections.board = h('div', null,
      h('div', { class: 'grid g3' }, [['Verified FIRs', fr.summary.verified_firs], ['Candidate links', fr.summary.candidate_links], ['Verified links', fr.summary.verified_links]].map(([l, v]) => h('div', { class: 'card kpi' }, h('div', { class: 'l' }, l), h('div', { class: 'v' }, v ?? 0)))),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'FIR ↔ FIR links'),
        h('p', { class: 'muted small' }, 'Links are suggested from shared phones, vehicles, accounts, serials and more. They stay candidates until a human verifies them.'),
        table([{ k: 'source_fir', label: 'FIR A' }, { k: 'target_fir', label: 'FIR B' }, { label: 'Strength', render: r => chip(r.match_strength, r.match_strength === 'Strong' ? 'bad' : r.match_strength === 'Moderate' ? 'warn' : '') }, { label: 'Score', num: true, render: r => r.match_score }, { label: 'Shared identifiers', render: r => (r.reasons || []).map(x => `${x.type}: ${(x.shared || []).join(', ')}`).join(' | ') }, { k: 'interlink_status', label: 'Status' }], fr.relationships, { emptyText: 'No links. Verify two or more FIRs that share identifiers.' })),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'FIRs'), table([{ k: 'fir_number', label: 'FIR' }, { k: 'filename', label: 'File' }, { k: 'status', label: 'Status' }], fr.firs, { emptyText: 'No FIRs.' })));
  }

  // ---- evidence
  if (ctx.can('sensitive_read')) {
    const evBox = h('div'), file = h('input', { type: 'file' }), src = h('input', { value: 'Investigator upload' });
    async function loadEv() {
      const r = await api('/evidence', { params: p });
      mount(evBox, table([{ k: 'evidence_id', label: 'ID' }, { k: 'filename', label: 'File' }, { k: 'uploaded_by', label: 'By' }, { label: 'Added', render: e => fmtTime(e.created_at) }, { label: 'SHA-256', render: e => h('code', { title: e.sha256 }, (e.sha256 || '').slice(0, 14) + '…') }, { label: 'Integrity', render: e => chip(e.integrity_status, e.integrity_status === 'verified' ? 'good' : e.integrity_status === 'tampered' ? 'bad' : '') },
        { label: '', render: e => h('div', { class: 'row', style: 'margin:0;gap:.3rem' },
          h('button', { class: 'sm', onclick: async () => { try { const v = await api(`/evidence/${encodeURIComponent(e.evidence_id)}/verify`, { params: p }); toast(`Integrity: ${v.status}`, v.status === 'verified' ? 'good' : 'bad'); loadEv(); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Verify'),
          h('button', { class: 'sm', onclick: async () => { try { const res = await api(`/evidence/${encodeURIComponent(e.evidence_id)}/download`, { params: p, raw: true }); download(await res.blob(), e.filename || e.evidence_id); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Download')) }], r.evidence, { emptyText: 'No evidence stored.' }));
    }
    sections.evidence = h('div', null,
      ctx.can('write') ? h('div', { class: 'card', style: 'margin-bottom:1rem' }, h('h3', null, 'Add evidence'), h('div', { class: 'row' }, field('File', file), field('Source', src), h('button', { class: 'primary', onclick: async () => {
        if (!file.files.length) return toast('Choose a file', 'bad'); if (!ctx.caseNumber) return toast('Select a case first', 'bad');
        const fd = new FormData(); fd.append('file', file.files[0]); fd.append('source', src.value); fd.append('case_number', ctx.caseNumber);
        try { await api('/evidence/upload', { method: 'POST', form: fd }); toast('Evidence stored with hash', 'good'); file.value = ''; loadEv(); } catch (ex) { toast(ex.message, 'bad'); }
      } }, 'Upload'))) : null,
      h('div', { class: 'card' }, h('h3', null, 'Evidence vault'), evBox));
    await loadEv();
  }
  await loadAlerts();
  const items = [['alerts', 'Alerts']]; if (sections.board) items.push(['board', 'FIR board']); if (sections.evidence) items.push(['evidence', 'Evidence vault']);
  const body = h('div');
  mount(root, tabs(items, 'alerts', id => mount(body, sections[id])), body); mount(body, sections.alerts);
}
