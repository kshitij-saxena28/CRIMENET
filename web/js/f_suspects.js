// Persons of interest: an explainable, ranked shortlist of leads with a human verification step.
// Wording rule: this is a LEAD PRIORITY for investigators, never a statement about guilt.
import { h, api, mount, clear, svg, notice, empty, chip, toast, kpi, table, fmtNum, fmtTime } from './lib.js';

const TIER_KIND = { High: 'bad', Medium: 'warn', Low: '' };
const STATUS = {
  confirmed: ['Confirmed person of interest', 'bad'],
  dismissed: ['Dismissed', ''],
  needs_more_info: ['Needs more info', 'info'],
};
const DECISIONS = [
  ['confirmed_person_of_interest', 'Verify as person of interest', 'primary'],
  ['needs_more_info', 'Need more info', ''],
  ['dismissed', 'Dismiss lead', 'danger'],
];
const CAUTION = 'Decision support only. A high score means "look here first", never that a person did anything wrong; a low score never clears anyone. '
  + 'Check every lead against the source records before acting on it.';
const JUMPS = [['graph', 'Knowledge Graph'], ['timeline', 'Timeline & Map'], ['signals', 'Signals & Evidence'], ['canvas', 'Link Canvas']];
const errBox = ex => notice(ex.message || String(ex), 'bad');

function dial(score, tier, size = 64) {
  const r = 26, c = 2 * Math.PI * r, frac = Math.max(0, Math.min(100, score)) / 100;
  return h('div', { class: `sp-dial sp-tier-${tier.toLowerCase()}`, role: 'img', 'aria-label': `Priority ${score} out of 100, ${tier}`, style: `width:${size}px;height:${size}px` },
    svg('svg', { viewBox: '0 0 64 64', width: size, height: size },
      svg('circle', { cx: 32, cy: 32, r, class: 'sp-dial-track', fill: 'none', 'stroke-width': 6 }),
      svg('circle', { cx: 32, cy: 32, r, class: 'sp-dial-arc', fill: 'none', 'stroke-width': 6, 'stroke-linecap': 'round', 'stroke-dasharray': `${(c * frac).toFixed(1)} ${c.toFixed(1)}`, transform: 'rotate(-90 32 32)' })),
    h('b', null, String(score)));
}
const bandText = r => `${r.band.low}–${r.band.high}`;
const statusChip = r => (STATUS[r.status] ? chip(STATUS[r.status][0], STATUS[r.status][1]) : null);

export async function render(root, ctx) {
  if (!ctx.caseNumber) { mount(root, notice('Select a case to see its persons of interest.', 'warn')); return; }
  if (!ctx.can('analyze')) { mount(root, notice('Your role cannot view lead rankings.', 'warn')); return; }
  const canReview = ctx.can('review');
  const state = { data: null, tier: '', type: '', hideDismissed: false, includeVictims: false, selected: null };
  const head = h('div'), filters = h('div', { class: 'sp-filters' }), list = h('div'), drawerHost = h('div');
  const how = howItWorks();
  mount(root, h('div', { class: 'sp-page' }, head, filters, list, how, drawerHost));

  async function load() {
    mount(list, empty('Scoring the case...'));
    try {
      state.data = await api('/suspects/rank', { params: { case_number: ctx.caseNumber, limit: 200, include_victims: state.includeVictims ? 'true' : 'false' } });
    } catch (ex) { mount(head, errBox(ex)); clear(list); return; }
    draw();
  }

  function draw() {
    const d = state.data;
    const open = d.results.filter(r => r.status !== 'dismissed');
    mount(head,
      h('div', { class: 'sp-hero' },
        h('div', null, h('h2', null, 'Persons of interest'), h('p', { class: 'muted' }, 'Who to look at first in this case, ranked by an explainable evidence model. Each entry shows why, how sure we can be, and what is missing.')),
        h('div', { class: 'sp-kpis' },
          kpi('People ranked', fmtNum(d.candidates), `${d.victims_excluded} victims/complainants/witnesses hidden`),
          kpi('High priority', fmtNum(d.tier_counts.High), `${fmtNum(d.tier_counts.Medium)} medium`),
          kpi('Verified', fmtNum(d.results.filter(r => r.status === 'confirmed').length), `${fmtNum(d.results.filter(r => r.status === 'dismissed').length)} dismissed`))),
      notice(CAUTION, 'warn'),
      d.unattributed_identifiers.length ? notice(`${d.unattributed_identifiers.length} phone/vehicle/account identifier(s) recur across FIRs but are not tied to a named person: `
        + d.unattributed_identifiers.slice(0, 4).map(u => `${u.type.toLowerCase()} ${u.name} (${u.firs.join(', ')})`).join('; ') + '. Finding out whose they are is itself a lead.') : null,
      !open.length && d.candidates ? notice('Every ranked person has been dismissed.', 'info') : null);
    const types = [...new Set(d.results.map(r => r.type))].sort();
    mount(filters,
      h('label', null, 'Tier', h('select', { onchange: e => { state.tier = e.target.value; drawList(); } }, [['', 'All tiers'], ['High', 'High'], ['Medium', 'Medium'], ['Low', 'Low']].map(([v, l]) => h('option', { value: v, selected: v === state.tier }, l)))),
      h('label', null, 'Type', h('select', { onchange: e => { state.type = e.target.value; drawList(); } }, [h('option', { value: '' }, 'All types'), ...types.map(t => h('option', { value: t, selected: t === state.type }, t.charAt(0) + t.slice(1).toLowerCase()))])),
      h('label', { class: 'sp-check' }, h('input', { type: 'checkbox', checked: state.hideDismissed, onchange: e => { state.hideDismissed = e.target.checked; drawList(); } }), 'Hide dismissed'),
      h('label', { class: 'sp-check', title: 'Victims, complainants and witnesses are hidden by default because they are not subjects of the investigation' },
        h('input', { type: 'checkbox', checked: state.includeVictims, onchange: e => { state.includeVictims = e.target.checked; load(); } }), 'Show victims, complainants and witnesses'));
    drawList();
  }

  function drawList() {
    const rows = state.data.results.filter(r => (!state.tier || r.tier === state.tier) && (!state.type || r.type === state.type) && !(state.hideDismissed && r.status === 'dismissed'));
    if (!rows.length) { mount(list, empty(state.data.candidates ? 'No one matches these filters.' : 'No people or organisations in this case yet. Ingest FIRs or add entities first.')); return; }
    const heroes = rows.slice(0, 3), rest = rows.slice(3);
    mount(list,
      h('div', { class: 'sp-heroes' }, heroes.map(r => card(r, true))),
      rest.length ? h('div', { class: 'sp-grid' }, rest.map(r => card(r, false))) : null);
  }

  function card(r, big) {
    const cls = `sp-card card sp-tier-${r.tier.toLowerCase()}${r.status === 'dismissed' ? ' sp-dismissed' : ''}${big ? ' sp-big' : ''}${r.pinned ? ' sp-pinned' : ''}`;
    return h('article', { class: cls, tabindex: 0, 'aria-label': `${r.name}, rank ${r.rank}, priority ${r.score}`,
      onclick: () => openDrawer(r.entity_id), onkeydown: e => { if (e.key === 'Enter') openDrawer(r.entity_id); } },
    h('div', { class: 'sp-card-top' },
      h('span', { class: 'sp-rank', title: r.pinned ? 'Pinned: verified person of interest' : 'Rank' }, (r.pinned ? '📌 ' : '#') + r.rank),
      dial(r.score, r.tier, big ? 76 : 58),
      h('div', { class: 'sp-id' }, h('b', null, r.name), h('div', { class: 'sp-chips' }, chip(r.tier, TIER_KIND[r.tier]), chip(r.type.toLowerCase()), r.role.label !== 'Role not recorded' ? chip(r.role.label, r.role.class === 'accused' ? 'warn' : '') : null, statusChip(r)))),
    h('ul', { class: 'sp-reasons' }, (r.top_factors.length ? r.top_factors : [{ sentence: 'No strong evidence signal in the recorded data.' }]).map(f => h('li', null, f.sentence))),
    h('div', { class: 'sp-foot muted small' }, `Plausible range ${bandText(r)} · data confidence: ${r.confidence.toLowerCase() === 'narrow' ? 'good' : r.confidence.toLowerCase() === 'moderate' ? 'fair' : 'thin'}`, h('span', { class: 'sp-open' }, 'Why this person →')));
  }

  // ---------------------------------------------------------------- detail drawer
  async function openDrawer(id) {
    state.selected = id;
    const shell = h('div', { class: 'sp-drawer-wrap' });
    const close = () => { state.selected = null; clear(drawerHost); };
    const panel = h('aside', { class: 'sp-drawer', role: 'dialog', 'aria-label': 'Lead detail' }, h('div', { class: 'sp-drawer-body' }, empty('Loading the explanation...')));
    shell.append(h('div', { class: 'sp-scrim', onclick: close }), panel);
    mount(drawerHost, shell);
    const onKey = e => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
    document.addEventListener('keydown', onKey);
    try {
      const p = await api(`/suspects/${encodeURIComponent(id)}/profile`, { params: { case_number: ctx.caseNumber } });
      mount(panel, profileView(p, close));
    } catch (ex) { mount(panel, h('div', { class: 'sp-drawer-body' }, errBox(ex), h('button', { onclick: close }, 'Close'))); }
  }

  function profileView(p, close) {
    const positives = p.factors.filter(f => f.contribution > 0.05), negatives = p.factors.filter(f => f.contribution < -0.05);
    const maxPts = Math.max(1, ...p.factors.map(f => Math.abs(f.points)));
    const factorRow = f => h('details', { class: 'sp-factor' },
      h('summary', null, h('span', { class: 'sp-f-label' }, f.label), h('span', { class: 'sp-f-fam muted small' }, f.family_label),
        h('span', { class: 'sp-bar', 'aria-hidden': 'true' }, h('i', { class: f.contribution < 0 ? 'neg' : 'pos', style: `width:${Math.round((Math.abs(f.points) / maxPts) * 100)}%` })),
        h('b', { class: 'sp-pts' }, `${f.points > 0 ? '+' : ''}${f.points} pts`)),
      h('p', null, f.sentence),
      f.evidence && f.evidence.length ? h('ul', { class: 'sp-evidence' }, f.evidence.map(e => h('li', null, chip(e.kind), ' ', e.ref ? h('span', { class: 'mono' }, e.ref + ' ') : null, e.text))) : h('p', { class: 'muted small' }, 'No individual records to list for this factor.'),
      f.evidence && f.evidence.length ? h('div', { class: 'row' }, ['transaction', 'communication', 'meeting'].some(k => f.evidence.some(e => e.kind === k)) ? h('button', { class: 'sm', onclick: () => jump('timeline', p.entity_id) }, 'Show in timeline') : null,
        h('button', { class: 'sm', onclick: () => jump('graph', p.entity_id) }, 'Show in graph')) : null);
    const decide = canReview ? decisionForm(p, close) : notice('Your role can view this lead but not record a verification decision.', 'info');
    return h('div', { class: 'sp-drawer-body' },
      h('div', { class: 'sp-drawer-head' },
        dial(p.score, p.tier, 84),
        h('div', { class: 'sp-id' }, h('h3', null, p.name), h('div', { class: 'sp-chips' }, chip(p.tier + ' priority', TIER_KIND[p.tier]), chip(p.type.toLowerCase()), chip(p.role.label), statusChip(p)),
          h('div', { class: 'muted small' }, p.rank ? `Rank ${p.rank} of ${p.total_ranked} · ` : 'Not in the default ranking · ', `plausible range ${bandText(p)} (${p.band.width.toLowerCase()} band)`)),
        h('button', { class: 'sm', onclick: close, 'aria-label': 'Close detail' }, 'Close')),
      notice(CAUTION, 'warn'),
      h('section', null, h('h4', null, 'Why this person'), h('p', { class: 'sp-summary' }, p.summary)),
      h('section', null, h('h4', null, 'Evidence behind the score'), h('p', { class: 'muted small' }, 'Points show how much the priority would drop if that factor were removed. Open a row for the records.'),
        positives.length ? positives.map(factorRow) : h('p', { class: 'muted' }, 'No factor raises this person above the case baseline.'),
        negatives.length ? [h('h5', null, 'Factors that lower the priority'), negatives.map(factorRow)] : null),
      h('section', null, h('h4', null, 'What would change this ranking'),
        p.missing.length ? h('ul', null, p.missing.map(m => h('li', null, m.text))) : h('p', { class: 'muted' }, 'No obvious data gaps for this person.'),
        h('p', { class: 'muted small' }, 'Records available for this person: ' + Object.entries(p.coverage).map(([k, v]) => `${k} ${v}`).join(' · '))),
      h('section', null, h('h4', null, 'Cross-FIR appearances'),
        p.cross_fir.firs.length || p.cross_fir.cases.length ? h('p', null, 'Appears in: ', [...p.cross_fir.firs, ...p.cross_fir.cases.filter(c => !p.cross_fir.firs.includes(c))].join(', ')) : h('p', { class: 'muted' }, 'No FIR appearances recorded.'),
        p.cross_fir.shared_identifiers.length ? h('ul', null, p.cross_fir.shared_identifiers.map(s => h('li', null, `${s.type.toLowerCase()} `, h('span', { class: 'mono' }, s.name), ' also appears in ' + s.firs.join(', ')))) : null),
      h('section', null, h('h4', null, 'Linked people and organisations'),
        table([{ label: 'Name', render: l => l.name }, { label: 'Type', render: l => l.type.toLowerCase() }, { label: 'Interactions', num: true, render: l => l.interactions },
          { label: 'Note', render: l => (l.accused_linked ? chip('linked to an accused', 'warn') : '') }], p.linked_entities, { emptyText: 'No linked people or organisations.' })),
      h('section', null, h('h4', null, 'Timeline highlights'),
        table([{ label: 'When', render: e => fmtTime(e.time) }, { label: 'Type', render: e => e.type.toLowerCase() }, { label: 'With', render: e => e.with },
          { label: 'Amount', num: true, render: e => (e.amount != null ? 'INR ' + fmtNum(e.amount) : '') }, { label: 'Record', render: e => h('span', { class: 'mono' }, e.event_id) }], p.timeline, { emptyText: 'No dated events for this person.' })),
      h('div', { class: 'row sp-jumps' }, JUMPS.map(([v, l]) => h('button', { class: 'sm', onclick: () => jump(v, p.entity_id) }, l))),
      h('section', { class: 'sp-decide' }, h('h4', null, 'Human verification'), decide,
        p.decision_history.length ? h('div', null, h('h5', null, 'Decision history'), h('ul', { class: 'sp-history' }, p.decision_history.map(x => h('li', null, h('b', null, (STATUS[x.decision === 'confirmed_person_of_interest' ? 'confirmed' : x.decision]?.[0]) || x.decision), ` by ${x.decided_by}, ${fmtTime(x.decided_at)}: `, x.reason)))) : null));
  }

  function jump(view, id) { ctx.focusEntity = id; ctx.go(view); }

  function decisionForm(p, close) {
    const reason = h('textarea', { rows: 3, maxlength: 1000, placeholder: 'Required: why (what did you check, what did you find?)', 'aria-label': 'Reason for the decision' });
    const btns = DECISIONS.map(([val, label, kind]) => h('button', { class: kind, disabled: true, 'data-decision': val, onclick: () => submit(val) }, label));
    const sync = () => btns.forEach(b => { b.disabled = reason.value.trim().length < 3; });
    reason.addEventListener('input', sync);
    async function submit(decision) {
      btns.forEach(b => { b.disabled = true; });
      try {
        await api(`/suspects/${encodeURIComponent(p.entity_id)}/decision`, { method: 'POST', body: { case_number: ctx.caseNumber, decision, reason: reason.value.trim() } });
        toast('Decision recorded and audit-logged', 'good');
        await load();
        openDrawer(p.entity_id);
      } catch (ex) { toast(ex.message || 'Could not record the decision', 'bad'); sync(); }
    }
    return h('div', null,
      h('p', { class: 'muted small' }, 'Verify only after checking the source records. Dismissed leads drop to the bottom of the list; verified persons of interest are pinned to the top and their contacts are then treated as accused-linked. Every decision is stored with your name and the time, and audit-logged.'),
      reason, h('div', { class: 'row' }, btns));
  }

  // ---------------------------------------------------------------- how it works
  function howItWorks() {
    const box = h('div'), det = h('details', { class: 'sp-how card' }, h('summary', null, 'How this ranking works'), box);
    let done = false;
    det.addEventListener('toggle', async () => {
      if (!det.open || done) return;
      done = true; mount(box, empty('Loading the model card...'));
      try {
        const m = await api('/suspects/model-card');
        const ev = m.evaluation && m.evaluation.benchmark;
        const std = ev && ev.results && ev.results.standard;
        const ms = o => (o ? `${o.mean.toFixed(2)} ± ${o.sd.toFixed(2)}` : 'n/a');
        mount(box,
          notice(m.caution, 'warn'),
          h('p', null, m.purpose),
          h('p', { class: 'muted' }, m.formula),
          h('h5', null, 'Signals and their weights (log-odds when the signal is at its maximum)'),
          table([{ label: 'Signal', render: f => f.label }, { label: 'Group', render: f => f.family }, { label: 'Weight', num: true, render: f => f.weight }], m.features),
          h('h5', null, 'FIR role'), h('p', { class: 'muted' }, m.role_prior.map(r => `${r.role}: ${r.log_odds > 0 ? '+' : ''}${r.log_odds}`).join(' · ')),
          h('p', { class: 'muted' }, m.uncertainty), h('p', { class: 'muted' }, m.feedback),
          h('h5', null, 'Measured performance'),
          std ? h('div', null,
            h('p', { class: 'muted small' }, `Synthetic benchmark, ${ev.seeds} worlds, "standard" difficulty (planted ring hidden among decoy hubs). Ground truth was used only for scoring.`),
            table([{ label: 'Method', render: r => r.n }, { label: 'AUC', render: r => ms(r.a) }, { label: 'Precision@10', render: r => ms(r.p) }],
              [['Evidence model (behaviour only, no roles)', 'evidence_model_behavioural_only'], ['Evidence model (with partial FIR roles)', 'evidence_model_partial_roles'], ['Naive: degree centrality', 'degree_centrality'], ['Naive: PageRank', 'pagerank']]
                .map(([n, k]) => ({ n, a: std[k].auc, p: std[k].p_at_10 })))) : h('p', { class: 'muted' }, 'No evaluation file is installed.'),
          h('p', null, m.supervised_component.used ? '' : 'No supervised model: ' + m.supervised_component.reason),
          h('h5', null, 'Limitations'), h('ul', null, m.limitations.map(l => h('li', null, l))));
      } catch (ex) { done = false; mount(box, errBox(ex)); }
    });
    return det;
  }

  await load();
}
