import { h, api, mount, kpi, donut, bars, table, chip, notice, empty, fmtNum, pct } from './lib.js';

export async function render(root, ctx) {
  const cn = ctx.caseNumber;
  const p = { case_number: cn };
  const analyst = ctx.can('analyze');
  const [ov, alerts, models, sum] = await Promise.all([
    analyst ? api('/analytics/overview', { params: p }) : null,
    api('/alerts', { params: p }),
    api('/models/status').catch(() => null),
    analyst ? null : api('/graph/summary', { params: p }),
  ]);
  const g = ov ? ov.graph : sum;
  const open = alerts.filter(a => !['Dismissed', 'False Positive', 'Reviewed'].includes(a.status));
  const high = open.filter(a => a.severity === 'High').length;
  const scope = cn ? `Case ${cn}` : 'All cases';

  const hero = h('div', { class: 'hero-card' },
    h('div', { class: 'hero-row' },
      h('div', null,
        h('span', { class: 'chip info' }, scope),
        h('h2', null, high ? `${high} high-priority ` : 'Nothing urgent, ', h('em', null, high ? 'need a decision' : 'all quiet')),
        h('p', null, `${fmtNum(g.nodes)} entities and ${fmtNum(g.relationships)} relationships in view. AI ranks what to look at; a person decides what it means.`)),
      h('div', { class: 'hero-stat' },
        h('div', null, h('b', null, fmtNum(open.length)), h('span', null, 'Open alerts')),
        h('div', null, h('b', null, ov ? fmtNum(ov.communities) : '–'), h('span', null, 'Communities')))));

  const alertsCard = h('div', { class: 'card b-7' }, h('h3', null, 'AI insights & alerts'),
    h('p', { class: 'muted small' }, 'Alerts rank items for human review. They are not findings of guilt.'),
    alerts.length
      ? h('div', null, ...alerts.slice(0, 6).map(a => h('div', { class: 'sev ' + a.severity },
          h('i'), h('div', { style: 'flex:1;min-width:0' }, h('b', null, a.title), h('div', { class: 'muted small' }, `${a.code} · ${a.status}`)),
          chip(pct(a.confidence), a.severity === 'High' ? 'bad' : 'warn'))))
      : empty('No alerts yet. Run anomaly analysis in Analytics.'));

  const modelCard = h('div', { class: 'card b-5' }, h('h3', null, 'Model health'),
    models ? [h('div', { class: 'muted small' }, `Runtime: ${models.runtime}`), table([{ k: 'name', label: 'Component' }, { k: 'version', label: 'Version' }, { label: 'Status', render: m => chip(m.status, m.status === 'ready' ? 'good' : 'warn') }], models.models)] : empty('Unavailable'));

  const role = ctx.user.role;
  const startFor = {
    supervisor: [['ingest', 'Upload & fix', 'Add FIRs and correct extracted facts.'], ['suspects', 'Review leads', 'See who to look at first and why.'], ['workflow', 'Approve', 'Sign off requests raised by others.'], ['requests', 'Access requests', 'Approve or reject new users.']],
    investigator: [['ingest', 'Verify facts', 'Accept or reject what the software extracted.'], ['graph', 'Explore links', 'See how people and numbers connect.'], ['suspects', 'Check leads', 'Confirm or dismiss persons of interest.'], ['reports', 'Report', 'Export the case report.']],
    admin: [['admin', 'Users & demo', 'Manage users, roles and demo data.'], ['requests', 'Access requests', 'Create users after supervisor approval.'], ['ingest', 'Ingest', 'Upload FIRs and tables.'], ['governance', 'Oversight', 'Backups, legal packs and dashboard.']],
    demo: [['ingest', 'Try ingestion', 'Upload a sample FIR and watch it get read.'], ['graph', 'Explore', 'Open the knowledge graph.'], ['suspects', 'Persons of interest', 'See the ranked leads.'], ['social', 'Social media', 'Import posts and see risk flags.']],
  }[role];
  const start = startFor ? h('div', null, h('h4', { style: 'margin-top:1.2rem' }, 'Start here'), h('div', { class: 'start-steps' }, startFor.map(([id, t, d]) => h('button', { onclick: () => ctx.go(id) }, h('b', null, t), h('span', null, d))))) : null;
  mount(root,
    cn ? null : notice('Showing aggregated data for all cases. Pick a case in the top bar to focus.'),
    hero, start,
    h('div', { class: 'kpis', style: 'margin-top:1rem' },
      kpi('Entities', fmtNum(g.nodes)), kpi('Relationships', fmtNum(g.relationships)),
      kpi('Timeline events', ov ? fmtNum(ov.timeline_events) : '–'),
      kpi('Open alerts', fmtNum(open.length)),
      ov ? kpi('Verified FIRs', fmtNum(ov.verified_documents)) : null),
    h('div', { class: 'bento', style: 'margin-top:1rem' },
      ov ? h('div', { class: 'card b-5' }, h('h3', null, 'Entity composition'), donut(ov.entity_types))
         : h('div', { class: 'card b-5' }, notice('Your role has a masked, read-only view: personal identifiers are hidden and analytics are not available.')),
      ov ? h('div', { class: 'card b-7' }, h('h3', null, 'Relationship types'), bars(ov.relationship_types)) : null,
      alertsCard, modelCard,
      ov && ov.top_degree?.length ? h('div', { class: 'card', style: 'grid-column:1/-1' }, h('h3', null, 'Most connected entities'),
        table([{ k: 'entity_id', label: 'ID' }, { k: 'name', label: 'Name' }, { label: 'Score', num: true, render: r => r.score.toFixed(4) }], ov.top_degree)) : null));
  if (analyst) {
    const rec = await api('/recommendations', { params: p }).catch(() => ({ recommendations: [] }));
    if (rec.recommendations.length) root.append(h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'Suggested next steps'),
      table([{ label: 'Priority', render: r => chip(r.priority, r.priority === 'High' ? 'bad' : 'info') }, { k: 'action', label: 'Action' }, { k: 'reason', label: 'Why' }], rec.recommendations)));
  }
}
