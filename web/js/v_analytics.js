import { h, api, mount, kpi, donut, bars, lineChart, table, tabs, notice, empty, chip, toast, fmtNum, pct } from './lib.js';

export async function render(root, ctx) {
  const p = { case_number: ctx.caseNumber };
  const [ov, evo] = await Promise.all([api('/analytics/overview', { params: p }), api('/analytics/network-evolution', { params: p }).catch(() => ({ snapshots: [] }))]);
  const topTbl = (rows, label) => h('div', { class: 'card' }, h('h4', null, label), table([{ k: 'entity_id', label: 'ID' }, { k: 'name', label: 'Name' }, { label: 'Score', num: true, render: r => r.score.toFixed(4) }], rows, { emptyText: 'No data.' }));
  const cdr = ov.cdr, fin = ov.financial;
  const anomOut = h('div');
  const runBtn = h('button', { class: 'primary', onclick: async () => {
    if (!ctx.caseNumber) return toast('Select a case first', 'bad');
    runBtn.disabled = true;
    try {
      const r = await api('/analytics/run', { method: 'POST', params: p });
      mount(anomOut, notice(`${r.alerts_created} new alert(s) created. Calibration: ${r.calibration?.status}. ${r.calibration?.note || ''}`, r.alerts_created ? 'warn' : ''),
        table([{ k: 'entity_id', label: 'ID' }, { k: 'name', label: 'Name' }, { k: 'type', label: 'Type' }, { k: 'score', label: 'Score', num: true }, { label: 'Factors', render: x => (x.factors || []).join('; ') }], r.review_candidates, { emptyText: 'No entities crossed the review threshold.' }));
    } catch (ex) { toast(ex.message, 'bad'); } finally { runBtn.disabled = false; }
  } }, 'Run anomaly analysis');

  const sections = {
    glance: h('div', null,
      h('div', { class: 'grid g4' }, kpi('Entities', fmtNum(ov.graph.nodes)), kpi('Relationships', fmtNum(ov.graph.relationships)), kpi('Components', fmtNum(ov.graph.components)), kpi('Contradictions', fmtNum(ov.contradictions.length))),
      h('div', { class: 'grid g2', style: 'margin-top:1rem' }, h('div', { class: 'card' }, h('h3', null, 'Entity types'), donut(ov.entity_types)), h('div', { class: 'card' }, h('h3', null, 'Relationship types'), bars(ov.relationship_types))),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'Network growth (active relationships by day)'), lineChart((evo.snapshots || []).map(s => ({ x: s.date, y: s.relationships }))))),
    structure: h('div', { class: 'grid g2' }, topTbl(ov.top_degree, 'Degree'), topTbl(ov.top_betweenness, 'Betweenness (bridges)'), topTbl(ov.top_pagerank, 'PageRank'), topTbl(ov.top_eigenvector, 'Eigenvector')),
    comms: h('div', null,
      h('div', { class: 'grid g4' }, kpi('Calls', fmtNum(cdr.call_count)), kpi('Late-hour calls', fmtNum(cdr.late_hour_calls)), kpi('Talk time (h)', fmtNum((cdr.total_duration_seconds || 0) / 3600, 1)), kpi('Unique pairs', fmtNum(cdr.unique_pairs))),
      h('div', { class: 'grid g2', style: 'margin-top:1rem' },
        h('div', { class: 'card' }, h('h3', null, 'Top communicating pairs'), table([{ k: 'source', label: 'A' }, { k: 'target', label: 'B' }, { k: 'calls', label: 'Calls', num: true }, { label: 'Seconds', num: true, render: r => fmtNum(r.duration) }], cdr.top_pairs)),
        h('div', { class: 'card' }, h('h3', null, 'Most active entities'), table([{ k: 'entity_id', label: 'ID' }, { k: 'calls', label: 'Calls', num: true }, { label: 'Seconds', num: true, render: r => fmtNum(r.duration_seconds) }], cdr.top_entities)))),
    money: h('div', null,
      h('div', { class: 'grid g4' }, kpi('Transactions', fmtNum(fin.transaction_count)), kpi('Total amount', fmtNum(fin.total_amount)), kpi('Unique pairs', fmtNum(fin.unique_pairs)), kpi('Circular flows', fmtNum((fin.candidate_circular_flows || []).length))),
      h('div', { class: 'grid g2', style: 'margin-top:1rem' },
        h('div', { class: 'card' }, h('h3', null, 'High-value transfers'), table([{ k: 'source', label: 'From' }, { k: 'target', label: 'To' }, { label: 'Amount', num: true, render: r => fmtNum(r.amount) }, { k: 'event_time', label: 'When' }], fin.high_value_transactions, { emptyText: 'None.' })),
        h('div', { class: 'card' }, h('h3', null, 'Candidate chains and circular flows'), h('p', { class: 'muted small' }, 'Candidates for review only, not evidence of wrongdoing.'),
          table([{ label: 'Path', render: c => (c.path || c.chain || []).join(' → ') || JSON.stringify(c) }, { label: 'Kind', render: c => c.kind || 'chain' }], [...(fin.candidate_chains || []).map(c => ({ ...c, kind: 'chain' })), ...(fin.candidate_circular_flows || []).map(c => ({ ...c, kind: 'circular' }))].slice(0, 50), { emptyText: 'None.' })))),
    cross: h('div', { class: 'card' }, h('h3', null, 'Cross-FIR relationships'), table([{ k: 'source_fir', label: 'FIR A' }, { k: 'target_fir', label: 'FIR B' }, { k: 'match_strength', label: 'Strength' }, { label: 'Score', num: true, render: r => r.match_score }, { k: 'interlink_status', label: 'Status' }], ov.fir_relationships, { emptyText: 'No cross-FIR links yet. Verify at least two FIRs that share identifiers.' })),
    contra: h('div', { class: 'card' }, h('h3', null, 'Contradictions & data conflicts'), table([{ k: 'type', label: 'Type' }, { k: 'entity', label: 'Entity' }, { k: 'message', label: 'Detail' }], ov.contradictions, { emptyText: 'No contradictions detected.' })),
    anom: h('div', { class: 'card' }, h('h3', null, 'Anomaly ranking'), h('p', { class: 'muted small' }, 'Isolation Forest + LOF over graph and communication features. Scores rank entities for human review; they are uncalibrated and never a finding of guilt.'), runBtn, anomOut),
  };
  const body = h('div');
  mount(root, tabs([['glance', 'Case at a glance'], ['structure', 'Network structure'], ['comms', 'Communications'], ['money', 'Financial'], ['cross', 'Cross-FIR'], ['contra', 'Contradictions'], ['anom', 'Anomalies']], 'glance', id => mount(body, sections[id])), body);
  mount(body, sections.glance);
}
