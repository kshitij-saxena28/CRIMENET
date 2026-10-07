import { h, api, mount, clear, field, table, tabs, notice, empty, chip, toast, fmtNum, pct } from './lib.js';
import { drawGraph, legendFor } from './graphview.js';

export async function render(root, ctx) {
  const p = { case_number: ctx.caseNumber };
  const analyst = ctx.can('analyze');
  const nodesRes = await api('/graph/nodes', { params: p });
  const nodes = nodesRes.nodes;
  const list = h('datalist', { id: 'node-list' }, nodes.slice(0, 3000).map(n => h('option', { value: n.id }, n.name)));
  const nameOf = id => nodes.find(n => n.id === id)?.name || id;
  const body = h('div');
  const pick = id => (nodes.find(n => n.id === id || n.name?.toLowerCase() === id.toLowerCase())?.id) || id;
  let cy = null;

  // ---- neighbourhood
  const ent = h('input', { list: 'node-list', placeholder: 'Optional: entity ID or name', style: 'width:220px' });
  const hops = h('select', null, [1, 2, 3, 4].map(n => h('option', { value: n, selected: n === 2 }, `${n} hop${n > 1 ? 's' : ''}`)));
  const typ = h('select', null, h('option', { value: '' }, 'All types'), [...new Set(nodes.map(n => n.type))].sort().map(t => h('option', { value: t }, t)));
  const canvas = h('div', { class: 'canvas', role: 'img', 'aria-label': 'Knowledge graph' });
  const side = h('div', { class: 'card' }, h('div', { class: 'muted' }, 'Click a node to inspect it.'));
  const info = h('div', { class: 'small muted' });
  const MAX_WHOLE = 400;  // beyond this a full picture is unreadable; the most connected entities are drawn and the rest are one search away
  let allEdges = null;
  const unl = h('input', { type: 'checkbox', onchange: () => showAll() });
  const inspect = n => { mount(side, h('h4', null, n.type), h('h3', null, n.name), h('dl', { class: 'kv' }, h('dt', null, 'ID'), h('dd', null, n.id), Object.entries(n).filter(([k, v]) => !['id', 'name', 'type'].includes(k) && v != null && typeof v !== 'object').flatMap(([k, v]) => [h('dt', null, k), h('dd', null, String(v))])), h('button', { class: 'sm', onclick: () => { ent.value = n.id; show(); } }, 'Re-centre here')); };
  async function showAll() {
    ent.value = '';
    try {
      allEdges = allEdges || (await api('/graph/edges', { params: p })).edges;
      let ns = typ.value ? nodes.filter(n => n.type === typ.value) : nodes.slice();
      const deg = {}; allEdges.forEach(e => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
      const unlinked = ns.filter(n => !deg[n.id]).length;
      if (!unl.checked && unlinked < ns.length) ns = ns.filter(n => deg[n.id]);  // entities with no links only add clutter; a tick brings them back
      const total = ns.length;
      if (ns.length > MAX_WHOLE) ns = ns.sort((a, b) => (deg[b.id] || 0) - (deg[a.id] || 0)).slice(0, MAX_WHOLE);
      const keep = new Set(ns.map(n => n.id)), es = allEdges.filter(e => keep.has(e.source) && keep.has(e.target));
      info.textContent = `Whole graph: ${ns.length} entities, ${es.length} links` + (!unl.checked && unlinked && unlinked < nodes.length ? ` (${unlinked} unlinked hidden)` : '') + (total > ns.length ? ` (the ${ns.length} most connected of ${total}; choose an entity to see the rest)` : '');
      if (!ns.length) { canvas.replaceChildren(empty('No entities of this type.')); return; }
      cy = drawGraph(canvas, ns, es, { onNode: inspect });
      mount(legendHolder, legendFor(ns));
    } catch (ex) { toast(ex.message, 'bad'); }
  }
  async function show() {
    if (!ent.value.trim()) return showAll();  // nothing chosen: the whole graph
    const id = pick(ent.value.trim());
    try {
      const r = await api('/graph/neighborhood', { params: { ...p, entity_id: id, hops: hops.value, entity_type: typ.value } });
      if (!r.center_found) { info.textContent = 'Entity not found in this case.'; canvas.replaceChildren(empty('Entity not found.')); return; }
      info.textContent = `${r.nodes.length} nodes, ${r.edges.length} edges` + (r.truncated ? ` (showing part of ${r.total_nodes} nodes / ${r.total_edges} edges)` : '');
      cy = drawGraph(canvas, r.nodes, r.edges, { center: id, onNode: inspect });
      mount(legendHolder, legendFor(r.nodes));
    } catch (ex) { toast(ex.message, 'bad'); }
  }
  const legendHolder = h('div');
  const nbr = h('div', null,
    h('div', { class: 'row' }, field('Focus on an entity (optional)', ent), field('Depth', hops), field('Type filter', typ), h('button', { class: 'primary', onclick: show }, 'Show network'), h('button', { onclick: showAll, title: 'Clear the entity and show every entity and link' }, 'Whole graph'), h('label', { title: 'Also draw entities that have no links yet' }, unl, 'Include unlinked'), info),
    legendHolder, h('div', { class: 'layout2' }, canvas, side));

  // ---- path + hidden
  const src = h('input', { list: 'node-list', placeholder: 'Source' }), dst = h('input', { list: 'node-list', placeholder: 'Target' });
  const pathOut = h('div'), pathCanvas = h('div', { class: 'canvas', style: 'height:360px;margin-top:.8rem' });
  async function findPath(hidden) {
    const a = pick(src.value.trim()), b = pick(dst.value.trim()); if (!a || !b) return toast('Enter both entities', 'bad');
    try {
      if (!hidden) {
        const r = await api('/graph/path', { params: { ...p, source: a, target: b } });
        if (!r.path?.length) { mount(pathOut, notice('No path found between these entities in the verified graph.')); pathCanvas.replaceChildren(); return; }
        mount(pathOut, h('p', null, h('b', null, `${r.hops} hop${r.hops === 1 ? '' : 's'}: `), r.path.map(nameOf).join(' → ')),
          table([{ k: 'source', label: 'From' }, { k: 'relation', label: 'Relation' }, { k: 'target', label: 'To' }, { label: 'Conf.', render: e => pct(e.confidence) }, { k: 'source_ref', label: 'Source' }], r.relationships));
        drawGraph(pathCanvas, r.path.map(id => nodes.find(n => n.id === id) || { id, type: '?', name: id }), r.relationships);
      } else {
        const r = await api('/graph/hidden-connections', { params: { ...p, source: a, target: b, max_hops: 4 } });
        mount(pathOut, r.paths.length ? table([{ label: 'Route', render: x => x.path.map(nameOf).join(' → ') }, { k: 'hops', label: 'Hops', num: true }, { label: 'Confidence', num: true, render: x => pct(x.path_confidence) }], r.paths) : notice('No indirect routes up to 4 hops.'));
        pathCanvas.replaceChildren();
      }
    } catch (ex) { toast(ex.message, 'bad'); }
  }
  const paths = h('div', null,
    h('div', { class: 'row' }, field('From', src), field('To', dst), h('button', { class: 'primary', onclick: () => findPath(false) }, 'Shortest path'),
      analyst ? h('button', { onclick: () => findPath(true) }, 'Hidden connections') : null), pathOut, pathCanvas);

  // ---- communities + centrality (lazy)
  const comm = h('div'), cent = h('div');
  async function loadComm() {
    mount(comm, empty('Loading…'));
    const [c, ce] = await Promise.all([api('/graph/communities', { params: p }), api('/graph/centrality', { params: p })]);
    const groups = c.communities.filter(g => g.length > 1).sort((a, b) => b.length - a.length);
    mount(comm, h('p', { class: 'muted small' }, `${c.communities.length} communities, ${groups.length} with more than one member.`),
      table([{ label: '#', render: (r) => r.i }, { label: 'Size', num: true, render: r => r.g.length }, { label: 'Members', render: r => r.g.slice(0, 12).map(nameOf).join(', ') + (r.g.length > 12 ? ' …' : '') }],
        groups.slice(0, 60).map((g, i) => ({ i: i + 1, g })), { onRow: r => { ent.value = r.g[0]; hops.value = '2'; tabsApi('nbr'); show(); }, emptyText: 'No multi-member communities.' }));
    const top = m => Object.entries(ce[m] || {}).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([id, v]) => ({ id, name: nameOf(id), v }));
    mount(cent, h('div', { class: 'grid g3' }, ['degree', 'betweenness', 'pagerank'].map(m => h('div', { class: 'card' }, h('h4', null, m), table([{ k: 'name', label: 'Entity' }, { label: 'Score', num: true, render: r => r.v.toFixed(4) }], top(m))))));
  }

  // ---- resolution
  const resVal = h('input', { placeholder: 'Name, phone, plate…' }), resOut = h('div');
  const resolution = h('div', null, h('p', { class: 'muted small' }, 'Fuzzy match a value against known entities. Candidates require human confirmation.'),
    h('div', { class: 'row' }, field('Value', resVal), h('button', { class: 'primary', onclick: async () => {
      if (!resVal.value.trim()) return;
      try { const r = await api('/entity-resolution', { params: { ...p, value: resVal.value.trim() } }); mount(resOut, table(Object.keys(r.matches[0] || {}).slice(0, 6).map(k => ({ k, label: k })), r.matches, { emptyText: 'No similar entities.' })); } catch (ex) { toast(ex.message, 'bad'); }
    } }, 'Find matches')), resOut);

  const sections = { nbr, paths, comm: h('div', null, comm, h('h3', { style: 'margin-top:1rem' }, 'Centrality leaders'), cent) };
  if (analyst) sections.res = resolution;
  const items = [['nbr', 'Neighbourhood'], ['paths', 'Paths'], ['comm', 'Communities & centrality']]; if (analyst) items.push(['res', 'Entity resolution']);
  const view = h('div');
  const tabsApi = id => { mount(view, sections[id]); if (id === 'nbr' && cy) setTimeout(() => { cy.resize(); cy.fit(undefined, 20); }, 0); if (id === 'comm' && !comm.dataset.done) { comm.dataset.done = 1; loadComm().catch(ex => toast(ex.message, 'bad')); } };
  mount(root, list, tabs(items, 'nbr', tabsApi), view);
  tabsApi('nbr');
  if (!nodes.length) { root.prepend(notice('This case has no verified entities yet. Ingest and verify FIRs, or load the demo dataset.', 'warn')); return; }
  showAll();  // default: the whole graph; pick an entity above to focus on its neighbourhood
}
