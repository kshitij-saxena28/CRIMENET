import { h, api, mount, clear, field, table, tabs, notice, empty, chip, toast, modal, kpi, fmtNum, pct } from './lib.js';
import { drawGraph } from './graphview.js';

const CSS = `
.na-prob{display:flex;align-items:center;gap:.4rem;min-width:110px}.na-prob .t{flex:1;height:8px;border-radius:4px;background:var(--panel2);overflow:hidden;border:1px solid var(--line)}
.na-prob .t span{display:block;height:100%;background:linear-gradient(90deg,var(--accent2),var(--accent))}.na-prob b{font-variant-numeric:tabular-nums;font-size:.8rem;min-width:2.6rem;text-align:right}
.na-why{margin:0;padding-left:1rem;font-size:.8rem;color:var(--muted)}.na-why li{margin:.1rem 0}
.na-grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:1rem}
.na-grid2{display:grid;gap:1rem;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
.na-split{display:grid;gap:1rem;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);align-items:start}
@media(max-width:1100px){.na-grid2,.na-split{grid-template-columns:1fr}}
.na-div{display:grid;grid-template-columns:minmax(120px,210px) 1fr minmax(90px,150px);gap:.5rem;align-items:center;margin:.3rem 0;font-size:.82rem}
.na-div .track{position:relative;height:14px;background:var(--panel2);border-radius:4px;border:1px solid var(--line)}
.na-div .track:before{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--muted);opacity:.6}
.na-div .fill{position:absolute;top:1px;bottom:1px;border-radius:3px}.na-div .fill.pos{background:var(--warn);left:50%}.na-div .fill.neg{background:var(--accent2);right:50%}
.na-delta{font-variant-numeric:tabular-nums;font-weight:600}.na-delta.up{color:var(--good)}.na-delta.down{color:var(--bad)}.na-delta.flat{color:var(--muted)}
.na-chips{display:flex;flex-wrap:wrap;gap:.35rem;margin:.4rem 0}.na-chips .chip button{border:0;background:transparent;padding:0 0 0 .3rem;cursor:pointer;color:inherit}
.na-legend{display:flex;flex-wrap:wrap;gap:.7rem;font-size:.78rem;color:var(--muted);margin:.4rem 0}.na-legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}
.na-mini{height:340px;border:1px solid var(--line);border-radius:10px;background:var(--panel2)}
.na-ctl{display:grid;gap:.8rem;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));margin-bottom:1rem}
.na-ctl .card{padding:.8rem}.na-ctl h4{margin-bottom:.4rem}.na-checks{display:flex;flex-wrap:wrap;gap:.3rem .8rem;max-height:120px;overflow:auto}
.na-checks label{flex-direction:row;align-items:center;gap:.35rem;color:var(--text);font-size:.82rem}
.na-flag{display:flex;flex-wrap:wrap;gap:.35rem;margin:.25rem 0}
.na-roletag{white-space:nowrap;display:inline-block;padding:.05rem .5rem;border-radius:999px;font-size:.75rem;color:#0b1220;font-weight:600}
.na-note{font-size:.8rem;color:var(--muted)}
details.na-det{margin:.5rem 0}details.na-det summary{cursor:pointer;color:var(--accent2);font-size:.85rem}
`;

const ROLE_COLORS = { 'Coordinator / Hub': '#f87171', 'Broker / Bridge': '#f59e0b', 'Courier / Intermediary': '#a78bfa', 'Core member': '#60a5fa', Member: '#94a3b8', Peripheral: '#64748b', Isolated: '#475569' };
const roleTag = r => h('span', { class: 'na-roletag', style: `background:${ROLE_COLORS[r] || '#94a3b8'}` }, r);

const probBar = p => h('div', { class: 'na-prob', title: 'Classifier score (a ranking signal, not a calibrated probability)' }, h('div', { class: 't' }, h('span', { style: `width:${Math.round(Math.min(1, Math.max(0, p)) * 100)}%` })), h('b', null, Math.round(p * 100) + '%'));
const errBox = ex => notice(ex.message || String(ex), 'bad');
const ms = o => (o ? `${(o.mean).toFixed(2)} ± ${(o.std).toFixed(2)}` : 'n/a');

// Diverging bars: rows = [{label, value, text, note}], scale = max |value| shown at full width.
function divBars(rows, scale) {
  const max = scale || Math.max(1e-9, ...rows.map(r => Math.abs(r.value)));
  return h('div', null, rows.map(r => {
    const w = Math.min(50, (Math.abs(r.value) / max) * 50);
    return h('div', { class: 'na-div' }, h('div', { title: r.note || '' }, r.label),
      h('div', { class: 'track', role: 'img', 'aria-label': `${r.label}: ${r.value}` }, h('div', { class: 'fill ' + (r.value >= 0 ? 'pos' : 'neg'), style: `width:${w}%` })),
      h('div', { class: 'muted small' }, r.text));
  }));
}
function delta(before, after, { lowerIsBetter = false, digits = 0, suffix = '', neutral = false } = {}) {
  if (before == null || after == null) return h('span', { class: 'na-delta flat' }, '–');
  const d = after - before;
  if (Math.abs(d) < Math.pow(10, -digits) / 2) return h('span', { class: 'na-delta flat' }, '● 0');
  const good = lowerIsBetter ? d < 0 : d > 0;
  return h('span', { class: 'na-delta ' + (neutral ? 'flat' : good ? 'up' : 'down') }, (d > 0 ? '▲ +' : '▼ ') + fmtNum(d, digits) + suffix);
}
const arrow = r => {
  if (r.status === 'removed' || r.status === 'gone') return chip('removed', 'bad');
  if (r.rank_after == null) return '–';
  const d = r.rank_before - r.rank_after;
  if (r.status === 'entered top 10') return chip('new in top 10', 'warn');
  if (r.status === 'left top 10') return chip('left top 10', '');
  return h('span', { class: 'na-delta ' + (d > 0 ? 'up' : d < 0 ? 'down' : 'flat') }, d > 0 ? `▲ ${d}` : d < 0 ? `▼ ${-d}` : '●');
};

export async function render(root, ctx) {
  if (!ctx.caseNumber) { mount(root, notice('Select a case to use Network AI.', 'warn')); return; }
  const p = { case_number: ctx.caseNumber };
  let nodes = [], edges = [];
  try {
    [nodes, edges] = await Promise.all([api('/graph/nodes', { params: p }).then(r => r.nodes), api('/graph/edges', { params: p }).then(r => r.edges)]);
  } catch (ex) { mount(root, errBox(ex)); return; }
  const nodeById = new Map(nodes.map(n => [n.id, n]));
  const nm = id => nodeById.get(id)?.name || id;
  const deg = {}; edges.forEach(e => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
  const activeNodes = nodes.filter(n => deg[n.id]);
  const nodeList = h('datalist', { id: 'na-nodes' }, nodes.slice(0, 3000).map(n => h('option', { value: n.id }, n.name)));
  const pick = v => (nodes.find(n => n.id === v || (n.name || '').toLowerCase() === String(v).toLowerCase())?.id) || v;
  const analyst = ctx.can('analyze');
  const panes = {}, loaded = {};
  const body = h('div');

  // ============================================================ 1. link prediction
  function linkPane() {
    const box = h('div'), limit = h('select', { 'aria-label': 'Suggestions to show' }, [10, 25, 50, 100].map(n => h('option', { value: n, selected: n === 25 }, `Top ${n}`)));
    async function load() {
      mount(box, empty('Training and evaluating the link model...'));
      try {
        const d = await api('/netai/link-predictions', { params: { ...p, limit: limit.value } });
        const parts = [];
        if (d.status !== 'ok') { mount(box, notice(d.message || 'Graph too small to train.', 'warn'), h('p', { class: 'na-note' }, `Graph: ${d.graph.connected} connected entities, ${d.graph.relationships} relationships.`)); return; }
        const ev = d.evaluation;
        if (ev) {
          const a = ev.auc, k = ev.precision_at_k;
          parts.push(h('div', { class: 'na-grid' },
            kpi('Held-out AUC (model)', ms(a.logistic_regression), `${ev.repeats} splits, ${ev.held_out_links_per_repeat} hidden links each`),
            kpi('AUC: common-neighbours baseline', ms(a.baseline_common_neighbours), `Adamic-Adar ${ms(a.baseline_adamic_adar)} · chance 0.50`),
            kpi(`Precision@${k.k} (model)`, ms(k.logistic_regression), `baseline ${ms(k.baseline_common_neighbours)} · random ${k.random_guess.toFixed(2)}`),
            kpi('AUC vs hard negatives only', ms(ev.auc_hard_negatives_only.logistic_regression), 'non-links that are 2 hops apart'),
          ));
          parts.push(h('details', { class: 'na-det' }, h('summary', null, 'How this was measured and how far to trust it'),
            h('p', { class: 'na-note' }, ev.protocol),
            h('p', { class: 'na-note' }, `Gradient-boosting reference AUC ${ms(a.gradient_boosting_reference)}. The deployed model is a logistic regression so every score can be explained feature by feature. In sparse, tree-like graphs (e.g. person-phone-account chains) the common-neighbour baseline can fall below chance because the hard negatives are exactly the pairs that share neighbours.`),
            h('ul', { class: 'na-why' }, d.limits.map(l => h('li', null, l)))));
        } else parts.push(notice(d.message || 'Model trained, but there were too few links for a held-out evaluation.', 'warn'));
        parts.push(notice('Suggestions are hypotheses for a human to check against evidence. They are not findings, and nothing here is added to the verified graph automatically.'));
        parts.push(h('p', { class: 'na-note' }, `${fmtNum(d.candidates_scored)} candidate pairs scored (2-3 hops apart or with recorded events but no relationship). Trained on ${fmtNum(d.training.positives)} hidden-link examples.`));
        parts.push(table([
          { label: '#', num: true, render: s => s.rank },
          { label: 'Suggested link', render: s => h('div', null, h('b', null, s.source_name), ' ', chip(s.source_type), h('span', { class: 'muted' }, '  ↔  '), h('b', null, s.target_name), ' ', chip(s.target_type),
              s.suggested_relation ? h('div', { class: 'na-note' }, 'Likely relation: ' + s.suggested_relation) : null) },
          { label: 'Score', render: s => probBar(s.probability) },
          { label: 'Why (top contributing evidence)', render: s => s.reasons.length ? h('ul', { class: 'na-why' }, s.reasons.map(r => h('li', { title: `contribution ${r.contribution}` }, r.text))) : h('span', { class: 'na-note' }, 'No single feature stands out') },
          { label: '', render: s => h('div', { class: 'row', style: 'margin:0;gap:.3rem' }, h('button', { class: 'sm', onclick: () => showOnGraph(s) }, 'Show on graph'),
              h('button', { class: 'sm', onclick: () => explainLink(s.source, s.target) }, 'Explain'),
              ctx.can('review') ? h('button', { class: 'sm primary', onclick: () => addCandidate(s) }, 'Add as candidate') : null) },
        ], d.suggestions, { emptyText: 'No candidate links found: every plausible pair is already linked.' }));
        mount(box, parts);
      } catch (ex) { mount(box, errBox(ex)); }
    }
    async function showOnGraph(s) {
      const cv = h('div', { class: 'na-mini', style: 'height:420px' });
      modal(`${s.source_name} ↔ ${s.target_name}`, h('div', null, h('p', { class: 'na-note' }, 'Dashed amber line = the hypothesised link. Solid lines are verified relationships.'), cv, h('div', { class: 'na-note' }, (s.reasons || []).map(r => r.text).join(' · '))), { wide: true });
      try {
        const [a, b, pa] = await Promise.all([api('/graph/neighborhood', { params: { ...p, entity_id: s.source, hops: 1 } }), api('/graph/neighborhood', { params: { ...p, entity_id: s.target, hops: 1 } }), api('/graph/path', { params: { ...p, source: s.source, target: s.target } })]);
        const ids = new Set(); const ns = [], es = [], seen = new Set();
        [a, b].forEach(r => { r.nodes.forEach(n => { if (!ids.has(n.id)) { ids.add(n.id); ns.push(n); } }); r.edges.forEach(e => { const k = e.source + '|' + e.target + '|' + e.relation; if (!seen.has(k)) { seen.add(k); es.push(e); } }); });
        (pa.path || []).forEach(id => { if (!ids.has(id)) { ids.add(id); ns.push(nodeById.get(id) || { id, name: id, type: '?' }); } });
        (pa.relationships || []).forEach(e => { const k = e.source + '|' + e.target + '|' + e.relation; if (!seen.has(k)) { seen.add(k); es.push(e); } });
        es.push({ source: s.source, target: s.target, relation: 'PREDICTED', verification_state: 'candidate' });
        const cy = drawGraph(cv, ns, es, { center: s.source });
        if (cy) {
          cy.getElementById(s.target).style({ 'border-width': 3, 'border-color': '#fff' });
          cy.edges('[relation = "PREDICTED"]').style({ 'line-color': '#f59e0b', 'target-arrow-color': '#f59e0b', width: 3, 'line-style': 'dashed', label: 'predicted', color: '#f59e0b', 'font-size': 9 });
        }
      } catch (ex) { mount(cv, errBox(ex)); }
    }
    function addCandidate(s) {
      const rel = h('input', { value: s.suggested_relation || 'ASSOCIATED_WITH', maxlength: 80, style: 'width:100%' });
      const note = h('textarea', { maxlength: 500, placeholder: 'Why do you think these are linked? (optional)' });
      const m = modal('Add as candidate relationship', h('div', null,
        h('p', null, h('b', null, s.source_name), ' ↔ ', h('b', null, s.target_name)),
        notice('This creates an UNVERIFIED candidate (source "netai-link-prediction", confidence = model score, capped at 50%). It appears in the review queue and stays out of the verified graph until a reviewer verifies it.'),
        field('Relation type', rel), field('Note', note),
        h('div', { class: 'row', style: 'margin-top:.8rem' }, h('button', { class: 'primary', onclick: async () => {
          try { const r = await api('/netai/link-predictions/accept', { method: 'POST', body: { case_number: ctx.caseNumber, source: s.source, target: s.target, relation_type: rel.value, note: note.value } }); toast(r.message, 'good'); m.close(); load(); } catch (ex) { toast(ex.message, 'bad'); }
        } }, 'Save candidate'))));
    }
    limit.addEventListener('change', load);
    load();
    return h('div', null, h('div', { class: 'row center' }, h('h3', { style: 'margin:0' }, 'Suggested missing links'), h('span', { class: 'spacer' }), limit, h('button', { onclick: load }, 'Re-run')), box);
  }

  // ============================================================ 2. explain
  function explainPane() {
    const kind = h('select', { 'aria-label': 'What to explain' }, h('option', { value: 'alert' }, 'An alert'), h('option', { value: 'entity' }, 'An entity'), h('option', { value: 'link' }, 'A link between two entities'));
    const alertSel = h('select', { 'aria-label': 'Alert', style: 'min-width:280px' });
    const ent = h('input', { list: 'na-nodes', placeholder: 'Entity ID or name', style: 'width:230px' }), a = h('input', { list: 'na-nodes', placeholder: 'Entity A', style: 'width:200px' }), b = h('input', { list: 'na-nodes', placeholder: 'Entity B', style: 'width:200px' });
    const out = h('div', null, empty('Pick something to explain.'));
    const rows = { alert: field('Alert', alertSel), entity: field('Entity', ent), link: h('div', { class: 'row', style: 'margin:0' }, field('Entity A', a), field('Entity B', b)) };
    const slot = h('div');
    const sync = () => mount(slot, rows[kind.value]);
    kind.addEventListener('change', sync); sync();
    api('/alerts', { params: p }).then(al => { mount(alertSel, al.length ? al.map(x => h('option', { value: x.code }, `${x.code}: ${x.title}`)) : h('option', { value: '' }, 'No alerts in this case')); }).catch(() => {});
    const evidenceBlock = ev => !ev ? null : h('div', { style: 'margin-top:.8rem' }, h('h4', null, 'Evidence behind this'),
      ev.source_refs?.length ? h('div', { class: 'na-chips' }, ev.source_refs.slice(0, 20).map(r => chip(r, 'info'))) : h('p', { class: 'na-note' }, 'No source references attached.'),
      ev.relationships?.length ? table([{ label: 'From', render: r => r.source_name || nm(r.source) }, { k: 'relation', label: 'Relation' }, { label: 'To', render: r => r.target_name || nm(r.target) }, { label: 'Conf.', render: r => pct(r.confidence) }, { k: 'source_ref', label: 'Source' }, { k: 'verification_state', label: 'State' }], ev.relationships) : null,
      ev.sample_events?.length ? h('details', { class: 'na-det' }, h('summary', null, `${ev.event_count} related event(s), first ${ev.sample_events.length}`), table([{ k: 'event_type', label: 'Type' }, { k: 'event_time', label: 'When' }, { k: 'counterparty', label: 'Counterparty' }, { k: 'amount', label: 'Amount', num: true }, { k: 'source_ref', label: 'Source' }], ev.sample_events)) : null);
    const featBars = d => d.features?.length ? h('div', null, h('h4', null, 'Which factors drive the score'),
      h('p', { class: 'na-note' }, d.method || ''),
      divBars(d.features.map(f => ({ label: f.label, value: f.robust_z, text: `${fmtNum(f.value, f.value < 1 ? 3 : 0)} vs median ${fmtNum(f.case_median, f.case_median < 1 ? 3 : 0)}${f.score_effect ? ` · Δscore ${-f.score_effect >= 0 ? '+' : ''}${-f.score_effect}` : ''}`, note: f.sentence })), 6)) : null;
    async function go() {
      mount(out, empty('Computing explanation...'));
      try {
        if (kind.value === 'alert') {
          if (!alertSel.value) return mount(out, notice('This case has no alerts. Run analytics first, or explain an entity instead.', 'warn'));
          const d = await api(`/netai/explain/alert/${encodeURIComponent(alertSel.value)}`, { params: p });
          mount(out, h('div', { class: 'card' },
            h('div', { class: 'row center', style: 'margin:0' }, h('h3', { style: 'margin:0' }, d.alert.title), chip(d.alert.severity, d.alert.severity === 'High' ? 'bad' : 'warn'), chip(d.alert.model_version, 'info'),
              d.reproduced ? chip(`score ${d.reproduced_score}` + (d.matches_stored ? ' (reproduced)' : ` (stored ${d.stored_score})`), d.matches_stored ? 'good' : 'warn') : null),
            h('p', null, 'Entity: ', h('b', null, nm(d.alert.entity_id)), ` (${d.alert.entity_id || 'none'})`),
            d.note ? notice(d.note) : null,
            h('ul', null, d.sentences.map(s => h('li', null, s))), featBars(d), evidenceBlock(d.evidence), h('p', { class: 'na-note' }, d.disclaimer)));
        } else if (kind.value === 'entity') {
          const id = pick(ent.value.trim()); if (!id) return mount(out, notice('Enter an entity.', 'warn'));
          const d = await api('/netai/explain/entity', { params: { ...p, entity_id: id } });
          mount(out, h('div', { class: 'card' }, h('div', { class: 'row center', style: 'margin:0' }, h('h3', { style: 'margin:0' }, d.entity.name), chip(d.entity.type), d.role ? roleTag(d.role.role) : null, d.anomaly_score != null ? chip('anomaly score ' + d.anomaly_score, 'warn') : null),
            d.role ? h('p', { class: 'na-note' }, `Structural role hint (${pct(d.role.confidence)} rule margin): ${d.role.reasons.join('; ')}`) : null,
            d.note ? notice(d.note) : null, h('ul', null, d.sentences.map(s => h('li', null, s))), featBars(d),
            d.neighbours.length ? h('div', { style: 'margin-top:.8rem' }, h('h4', null, 'Direct connections'), table([{ k: 'name', label: 'Entity' }, { k: 'type', label: 'Type' }, { label: 'Relation', render: n => n.relations.join(', ') }, { label: 'Conf.', render: n => pct(n.confidence) }], d.neighbours)) : null,
            evidenceBlock(d.evidence), h('p', { class: 'na-note' }, d.disclaimer)));
        } else {
          const s = pick(a.value.trim()), t = pick(b.value.trim()); if (!s || !t) return mount(out, notice('Enter both entities.', 'warn'));
          const d = await api('/netai/explain/link', { params: { ...p, source: s, target: t } });
          mount(out, h('div', { class: 'card' }, h('div', { class: 'row center', style: 'margin:0' }, h('h3', { style: 'margin:0' }, `${d.source.name} ↔ ${d.target.name}`), chip(d.linked ? 'recorded link' : 'no recorded link', d.linked ? 'good' : 'warn'), d.model?.probability != null ? chip('model score ' + pct(d.model.probability), 'info') : null),
            h('ul', null, d.sentences.map(x => h('li', null, x))),
            d.common_neighbours.length ? h('p', null, h('b', null, 'Common neighbours: '), d.common_neighbours.map(c => `${c.name} (${c.type})`).join(', ')) : null,
            d.model?.contributions ? h('div', null, h('h4', null, 'Link-model feature contributions'), h('p', { class: 'na-note' }, d.model.note),
              divBars(d.model.contributions.map(c => ({ label: c.label, value: c.contribution, text: c.text, note: c.text }))))
              : (d.model?.message ? notice(d.model.message, 'warn') : null),
            d.relationships.length ? h('div', { style: 'margin-top:.8rem' }, h('h4', null, 'Recorded relationships'), table([{ label: 'From', render: r => nm(r.source) }, { k: 'relation', label: 'Relation' }, { label: 'To', render: r => nm(r.target) }, { label: 'Conf.', render: r => pct(r.confidence) }, { k: 'source_ref', label: 'Source' }, { k: 'verification_state', label: 'State' }], d.relationships)) : null,
            h('p', { class: 'na-note' }, d.disclaimer)));
        }
      } catch (ex) { mount(out, errBox(ex)); }
    }
    explainLink = (s, t) => { kind.value = 'link'; sync(); a.value = s; b.value = t; showTab('explain'); go(); };
    return h('div', null, h('div', { class: 'card', style: 'margin-bottom:1rem' }, h('div', { class: 'row' }, field('Explain', kind), slot, h('button', { class: 'primary', onclick: go }, 'Explain'))), out);
  }
  let explainLink = () => {};

  // ============================================================ 3. roles + critical nodes + simulation
  function rolesPane() {
    const removal = new Set();
    const canvas = h('div', { class: 'canvas', style: 'height:480px', role: 'img', 'aria-label': 'Network coloured by structural role' });
    const legend = h('div', { class: 'na-legend' });
    const table1 = h('div'), crit = h('div'), chipsBox = h('div', { class: 'na-chips' }), simOut = h('div');
    const roleFilter = h('select', { 'aria-label': 'Filter by role' }), roleOf = new Map();
    let cy = null, rolesData = null;
    const refreshSel = () => {
      chipsBox.replaceChildren(...(removal.size ? [...removal].map(id => h('span', { class: 'chip warn' }, nm(id), h('button', { 'aria-label': 'Remove ' + nm(id), onclick: () => toggle(id) }, '×'))) : [h('span', { class: 'na-note' }, 'Click nodes in the graph or rows below to build a removal set.')]));
      if (cy) cy.nodes().forEach(n => n.style({ 'border-width': removal.has(n.id()) ? 4 : 0, 'border-color': '#ef4444' }));
    };
    function toggle(id) { removal.has(id) ? removal.delete(id) : removal.add(id); refreshSel(); }
    async function load() {
      try {
        const [r, c] = await Promise.all([api('/netai/roles', { params: p }), api('/netai/critical-nodes', { params: { ...p, k: 5 } })]);
        rolesData = r;
        const drawn = activeNodes.length ? activeNodes : nodes;
        if (!drawn.length) { mount(canvas, empty('No entities in this case yet.')); }
        else {
          cy = drawGraph(canvas, drawn, edges, { onNode: n => toggle(n.id) });
          if (cy) { r.roles.forEach(x => roleOf.set(x.entity_id, x.role)); cy.nodes().forEach(n => { const role = roleOf.get(n.id()); n.style({ 'background-color': role ? ROLE_COLORS[role] : '#475569', opacity: role ? 1 : .55 }); }); }
        }
        mount(legend, Object.entries(ROLE_COLORS).filter(([k]) => k !== 'Isolated').map(([k, v]) => h('span', null, h('i', { style: `background:${v}` }), `${k} (${r.summary[k] || 0})`)), h('span', null, h('i', { style: 'background:#475569' }), 'other entity types'));
        if (r.status !== 'ok') { mount(table1, notice(r.message, 'warn')); }
        else {
          mount(roleFilter, h('option', { value: '' }, 'All roles'), Object.keys(r.summary).filter(k => k !== 'Isolated').map(k => h('option', { value: k }, `${k} (${r.summary[k]})`)));
          drawRoles();
        }
        const artRows = c.articulation_points.slice(0, 12);
        mount(crit, h('div', { class: 'na-split' },
          h('div', null, h('h4', null, 'Cut-vertices (removal splits the network)'), table([{ label: 'Entity', render: a => h('a', { href: '#', onclick: e => { e.preventDefault(); toggle(a.entity_id); } }, a.name) }, { k: 'type', label: 'Type' }, { k: 'degree', label: 'Links', num: true }, { k: 'components_after', label: 'Comps after', num: true }, { k: 'largest_after', label: 'Largest after', num: true }], artRows, { emptyText: 'No cut-vertices: no single entity holds the network together.' })),
          h('div', null, h('h4', null, 'Greedy best removals (most fragmentation first)'), table([{ k: 'step', label: '#', num: true }, { label: 'Entity', render: a => h('a', { href: '#', onclick: e => { e.preventDefault(); toggle(a.entity_id); } }, a.name) }, { label: 'Pairs cut', num: true, render: a => fmtNum(a.pairs_removed_this_step) }, { label: 'Largest share after', render: a => pct(a.largest_share_after) }, { k: 'components_after', label: 'Comps', num: true }], c.greedy, { emptyText: 'Nothing to disrupt.' })),),
          h('p', { class: 'na-note' }, c.objective));
        refreshSel();
      } catch (ex) { mount(table1, errBox(ex)); }
    }
    function drawRoles() {
      const rows = rolesData.roles.filter(x => x.role !== 'Isolated' && (!roleFilter.value || x.role === roleFilter.value));
      mount(table1, table([{ label: 'Entity', render: x => h('a', { href: '#', onclick: e => { e.preventDefault(); toggle(x.entity_id); if (cy) cy.animate({ center: { eles: cy.getElementById(x.entity_id) }, duration: 200 }); } }, nm(x.entity_id)) },
        { label: 'Role hint', render: x => roleTag(x.role) }, { label: 'Rule margin', render: x => pct(x.confidence) },
        { label: 'Why', render: x => h('ul', { class: 'na-why' }, x.reasons.map(t => h('li', null, t))) }], rows.slice(0, 200), { emptyText: 'No entities of the selected types have recorded relationships.' }));
    }
    roleFilter.addEventListener('change', () => rolesData && drawRoles());
    load();
    return h('div', null,
      notice('Roles are structural hints derived from network position. They are NOT assertions about anyone\'s real or criminal role and must be corroborated with evidence.'),
      h('div', { class: 'na-split' },
        h('div', { class: 'card' }, h('div', { class: 'row center', style: 'margin:0' }, h('h3', { style: 'margin:0;flex:1' }, 'Role hints'), roleFilter), table1),
        h('div', { class: 'card' }, h('h3', null, 'Network by role'), legend, canvas)),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'Critical nodes'), crit));
  }

  // ============================================================ shell
  const items = [['link', 'Link prediction'], ['explain', 'Explain'], ['roles', 'Roles & critical nodes']];
  const makers = { link: linkPane, explain: explainPane, roles: rolesPane };
  const tabBar = tabs(items, 'link', id => showTab(id, true));
  function showTab(id, fromBar) {
    if (!fromBar) tabBar.select(id);
    if (!panes[id]) panes[id] = makers[id]();
    mount(body, panes[id]);
  }
  if (!analyst) { mount(root, notice('Network AI needs the analyze permission.', 'warn')); return; }
  mount(root, h('style', null, CSS), nodeList,
    h('p', { class: 'muted small' }, 'Graph intelligence for review: predicted links, explanations, structural role hints. Every output is a hypothesis for a human to verify, never a finding.'), tabBar, body);
  panes.explain = explainPane();
  showTab('link');
}
