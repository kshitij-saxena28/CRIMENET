import { h, api, mount, field, table, tabs, notice, empty, chip, toast, download, fmtTime, kpi, fmtNum } from './lib.js';

export async function render(root, ctx) {
  const cn = ctx.caseNumber;
  const sections = {};
  const out = h('div');
  const fetchFile = async (ext, mime) => {
    try { const res = await api(`/report/${encodeURIComponent(cn)}/${ext}`, { raw: true }); download(await res.blob(), `${cn}_report.${ext}`); toast('Report downloaded', 'good'); } catch (ex) { toast(ex.message, 'bad'); }
  };
  sections.report = h('div', null, cn ? null : notice('Select a case to generate a report.', 'warn'),
    h('div', { class: 'row' },
      h('button', { class: 'primary', disabled: !cn, onclick: async () => {
        try { const r = await api('/report/' + encodeURIComponent(cn)); const ex = r.executive_summary || {};
          mount(out, h('div', { class: 'grid g4' }, Object.entries(ex).map(([k, v]) => kpi(k.replace(/_/g, ' '), fmtNum(v)))), h('p', { class: 'muted small' }, `Generated ${fmtTime(r.generated_at)}`), h('details', null, h('summary', null, 'Raw JSON'), h('pre', { class: 'txt' }, JSON.stringify(r, null, 2).slice(0, 60000))),
            h('div', { class: 'row', style: 'margin-top:.6rem' }, h('button', { onclick: () => download(new Blob([JSON.stringify(r, null, 2)], { type: 'application/json' }), `${cn}_report.json`) }, 'Download JSON')));
        } catch (ex) { toast(ex.message, 'bad'); } } }, 'Generate summary'),
      ['csv', 'docx', 'pdf'].map(x => h('button', { disabled: !cn, onclick: () => fetchFile(x) }, 'Download ' + x.toUpperCase()))), out);
  if (ctx.can('audit') || ctx.can('audit_case')) {
    const box = h('div'), verify = h('div');
    async function load() { if (!cn && !ctx.can('audit')) { mount(box, notice('Select a case to see its audit log.', 'warn')); return; } const r = await api('/audit', { params: { case_number: cn } }); mount(box, table([{ k: 'id', label: '#' }, { label: 'Time', render: e => fmtTime(e.created_at) }, { k: 'actor', label: 'Actor' }, { k: 'action', label: 'Action' }, { label: 'Details', render: e => h('span', { class: 'small' }, (e.details || '').slice(0, 160)) }], r.events, { emptyText: 'No audit events.' })); }
    sections.audit = h('div', null, h('div', { class: 'row' }, h('button', { onclick: async () => { try { const v = await api('/audit/verify'); mount(verify, notice(v.verified ? `Audit chain intact: ${v.checked} events verified.` : `Audit chain BROKEN: ${v.reason}`, v.verified ? 'good' : 'bad')); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Verify audit chain')), verify, h('div', { class: 'card' }, box));
    await load();
  }
  const items = [['report', 'Case report']]; if (sections.audit) items.push(['audit', 'Audit log']);
  const body = h('div'); mount(root, tabs(items, 'report', id => mount(body, sections[id])), body); mount(body, sections.report);
}
