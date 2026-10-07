import { h, colorFor, TYPE_COLORS } from './lib.js';
// Cytoscape wrapper. nodes: [{id,type,name}], edges: [{source,target,relation,verification_state}]
export function drawGraph(container, nodes, edges, { center, onNode, sizeBy } = {}) {
  container.replaceChildren();
  if (typeof cytoscape === 'undefined') { container.append(h('div', { class: 'empty' }, 'Graph library failed to load.')); return null; }
  const ids = new Set(nodes.map(n => n.id));
  const deg = {}; edges.forEach(e => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
  const cy = cytoscape({
    container,
    elements: [
      ...nodes.map(n => ({ data: { id: n.id, label: n.name || n.id, type: n.type, deg: deg[n.id] || 0, raw: n } })),
      ...edges.filter(e => ids.has(e.source) && ids.has(e.target)).map((e, i) => ({ data: { id: 'e' + i, source: e.source, target: e.target, label: e.relation, state: e.verification_state, raw: e } })),
    ],
    style: [
      { selector: 'node', style: { 'background-color': ele => colorFor(ele.data('type')), label: 'data(label)', color: getComputedStyle(document.documentElement).getPropertyValue('--text').trim() || '#ddd', 'font-size': 9, 'text-valign': 'bottom', 'text-margin-y': 3, width: ele => 14 + Math.min(26, ele.data('deg') * 3), height: ele => 14 + Math.min(26, ele.data('deg') * 3), 'text-max-width': 90, 'text-wrap': 'ellipsis' } },
      { selector: 'node[id = "' + String(center || '').replace(/"/g, '') + '"]', style: { 'border-width': 3, 'border-color': '#fff' } },
      { selector: 'edge', style: { width: 1.4, 'line-color': '#64748b', 'target-arrow-color': '#64748b', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': .8, opacity: .8 } },
      { selector: 'edge[state != "verified"]', style: { 'line-style': 'dashed' } },
      { selector: ':selected', style: { 'border-width': 3, 'border-color': '#f59e0b', 'line-color': '#f59e0b' } },
      { selector: '.hl', style: { 'line-color': '#f59e0b', 'target-arrow-color': '#f59e0b', width: 3, 'border-width': 3, 'border-color': '#f59e0b' } },
    ],
    layout: { name: nodes.length > 250 ? 'grid' : 'cose', animate: false, fit: true, padding: 20, nodeRepulsion: 9000, idealEdgeLength: 70 },
    wheelSensitivity: .3,
  });
  if (onNode) cy.on('tap', 'node', ev => onNode(ev.target.data('raw')));
  return cy;
}
export function legendFor(nodes) {
  const types = [...new Set(nodes.map(n => n.type))];
  return h('div', { class: 'legend' }, types.map(t => h('span', null, h('i', { style: `background:${colorFor(t)}` }), t)));
}
export { TYPE_COLORS };
