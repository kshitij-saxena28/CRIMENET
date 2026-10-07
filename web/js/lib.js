// Shared helpers: DOM builder (XSS-safe: text is always set via textContent), API client, widgets, charts.
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}
export const $ = (sel, root = document) => root.querySelector(sel);
export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...kids) { clear(el); add(el, kids); return el; }

// ---------------------------------------------------------------- session + API
const TOKEN_KEY = 'dcn-token';
export const session = {
  get token() { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } },
  set token(v) { try { v ? sessionStorage.setItem(TOKEN_KEY, v) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ } },
  onExpire: () => {},
};
export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

function errText(data, status) {
  const d = data && data.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(x => (x.loc ? x.loc.slice(1).join('.') + ': ' : '') + x.msg).join('; ');
  return `Request failed (${status})`;
}
export async function api(path, { method = 'GET', body, form, params, raw } = {}) {
  let url = path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    const s = q.toString(); if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  const headers = {};
  if (session.token) headers.Authorization = 'Bearer ' + session.token;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, { method, headers, body: payload }); }
  catch { throw new ApiError('Cannot reach the server. Is it still running?', 0); }
  if (raw && res.ok) return res;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (res.status === 401 && path !== '/auth/login') { session.onExpire(); throw new ApiError('Session expired. Please sign in again.', 401); }
  if (!res.ok) throw new ApiError(errText(data, res.status), res.status);
  return data;
}

// ---------------------------------------------------------------- widgets
export function toast(msg, kind = '') {
  const box = $('#toasts') || document.body.appendChild(h('div', { id: 'toasts' }));
  const t = h('div', { class: 'toast ' + kind, role: 'status' }, msg);
  box.append(t); setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 350); }, kind === 'bad' ? 7000 : 3600);
}
export function modal(title, body, { wide = false } = {}) {
  const onKey = e => {
    if (e.key !== 'Escape') return;
    const all = document.querySelectorAll('.overlay'); if (all[all.length - 1] !== ov) return;
    e.stopPropagation(); close();
  };
  const close = () => { document.removeEventListener('keydown', onKey, true); ov.remove(); };
  const ov = h('div', { class: 'overlay', onclick: e => { if (e.target === ov) close(); } },
    h('div', { class: 'modal' + (wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'row center', style: 'margin:0' }, h('h3', { style: 'margin:0;flex:1' }, title), h('button', { class: 'sm', 'aria-label': 'Close', onclick: close }, 'Close')),
      body));
  document.body.append(ov);
  document.addEventListener('keydown', onKey, true);
  return { close, el: ov };
}
export const chip = (text, kind = '') => h('span', { class: 'chip ' + kind }, text);
export const notice = (text, kind = '') => h('div', { class: 'notice ' + kind }, text);
export const empty = text => h('div', { class: 'empty' }, text);
const REDUCED = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
export function countUp(el, target, { dur = 1100, dec = 0 } = {}) {
  const fmt = n => n.toLocaleString(undefined, { maximumFractionDigits: dec, minimumFractionDigits: dec });
  if (REDUCED() || !isFinite(target)) { el.textContent = fmt(target); return; }
  const t0 = performance.now();
  const step = now => { const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 4); el.textContent = fmt(target * e); if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(target); };
  el.textContent = fmt(0); requestAnimationFrame(step);
}
export const kpi = (label, value, sub) => {
  const v = h('div', { class: 'v' }, value ?? '–');
  const txt = String(value ?? '');
  if (/^-?[\d,]+(\.\d+)?$/.test(txt)) countUp(v, parseFloat(txt.replace(/,/g, '')), { dec: (txt.split('.')[1] || '').length });
  return h('div', { class: 'card kpi' }, h('div', { class: 'l' }, label), v, sub ? h('div', { class: 'muted small' }, sub) : null);
};
export function field(label, input) { return h('label', null, label, input); }
export function tabs(items, initial, onPick) {
  const thumb = h('span', { class: 'thumb' });
  const btns = items.map(([id, label]) => h('button', { role: 'tab', class: id === initial ? 'active' : '' }, label));
  const wrap = h('div', { class: 'tabs', role: 'tablist' }, thumb, btns);
  const place = () => {
    const on = btns.find(b => b.classList.contains('active')); if (!on || !on.offsetWidth) return;
    thumb.style.width = on.offsetWidth + 'px'; thumb.style.transform = `translateX(${on.offsetLeft - 4}px)`;
  };
  btns.forEach((x, i) => x.addEventListener('click', () => { btns.forEach(y => y.classList.remove('active')); x.classList.add('active'); place(); onPick(items[i][0]); }));
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(place).observe(wrap);
  requestAnimationFrame(() => { thumb.style.transition = 'none'; place(); requestAnimationFrame(() => { thumb.style.transition = ''; }); });
  wrap.select = id => { const i = items.findIndex(t => t[0] === id); if (i < 0) return; btns.forEach(y => y.classList.remove('active')); btns[i].classList.add('active'); place(); };
  return wrap;
}
export function fmtNum(n, d = 0) { return n == null || isNaN(n) ? '–' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }); }
export function fmtTime(s) { if (!s) return '–'; const d = new Date(s); return isNaN(d) ? String(s) : d.toLocaleString(); }
export const pct = v => v == null ? '–' : Math.round(v * 100) + '%';

// table(columns=[{k,label,num,render}], rows, {onRow, empty})
export function table(cols, rows, { onRow, emptyText = 'Nothing to show.' } = {}) {
  if (!rows || !rows.length) return empty(emptyText);
  const tb = h('tbody');
  for (const r of rows) {
    const tr = h('tr', onRow ? { style: 'cursor:pointer', onclick: () => onRow(r) } : null);
    for (const c of cols) {
      let v = c.render ? c.render(r) : r[c.k];
      if (v == null || v === '') v = '–';
      tr.append(h('td', { class: c.num ? 'num' : '' }, typeof v === 'object' && !(v instanceof Node) ? JSON.stringify(v) : v));
    }
    tb.append(tr);
  }
  return h('div', { class: 'tw' }, h('table', null, h('thead', null, h('tr', null, cols.map(c => h('th', { class: c.num ? 'num' : '' }, c.label)))), tb));
}

// ---------------------------------------------------------------- charts (inline SVG)
export const TYPE_COLORS = { PERSON: '#7c9cff', PHONE: '#5eead4', VEHICLE: '#fbbf24', ACCOUNT: '#c4a1ff', ORGANIZATION: '#f472b6', CASE: '#94a3b8', LOCATION: '#4ade80', EMAIL: '#fb923c', DEVICE: '#22d3ee', DOCUMENT: '#cbd5e1' };
const PALETTE = ['#7c9cff', '#5eead4', '#fbbf24', '#c4a1ff', '#f472b6', '#4ade80', '#fb923c', '#22d3ee', '#fb7185', '#94a3b8'];
export const colorFor = (k, i = 0) => TYPE_COLORS[String(k).toUpperCase()] || PALETTE[i % PALETTE.length];
const NS = 'http://www.w3.org/2000/svg';
export function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(NS, tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
export function donut(data) {
  const entries = Object.entries(data || {}).filter(([, v]) => v > 0);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  if (!total) return empty('No data yet.');
  const R = 62, r = 38, cx = 80, cy = 80; let ang = -Math.PI / 2;
  const s = svg('svg', { viewBox: '0 0 160 160', width: 160, height: 160, role: 'img', 'aria-label': 'Composition chart' });
  entries.forEach(([k, v], i) => {
    const a2 = ang + (v / total) * Math.PI * 2 - 0.0001, large = a2 - ang > Math.PI ? 1 : 0;
    const p = a => [cx + Math.cos(a), cy + Math.sin(a)];
    const [x1, y1] = p(ang), [x2, y2] = p(a2);
    const d = entries.length === 1 ? `M${cx - R},${cy}a${R},${R} 0 1,0 ${2 * R},0a${R},${R} 0 1,0 ${-2 * R},0M${cx - r},${cy}a${r},${r} 0 1,1 ${2 * r},0a${r},${r} 0 1,1 ${-2 * r},0`
      : `M${cx + R * (x1 - cx)},${cy + R * (y1 - cy)}A${R},${R} 0 ${large} 1 ${cx + R * (x2 - cx)},${cy + R * (y2 - cy)}L${cx + r * (x2 - cx)},${cy + r * (y2 - cy)}A${r},${r} 0 ${large} 0 ${cx + r * (x1 - cx)},${cy + r * (y1 - cy)}Z`;
    s.append(svg('path', { d, fill: colorFor(k, i), 'fill-rule': 'evenodd' }, svg('title', null, `${k}: ${v}`)));
    ang = a2;
  });
  s.append(svg('text', { x: cx, y: cy + 5, 'text-anchor': 'middle', style: 'font-size:16px;font-weight:700;fill:var(--text)' }, total));
  const legend = h('div', { class: 'legend' }, entries.map(([k, v], i) => h('span', null, h('i', { style: `background:${colorFor(k, i)}` }), `${k} ${v}`)));
  return h('div', { class: 'row center' }, s, legend);
}
export function bars(data, color) {
  const entries = Object.entries(data || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return empty('No data yet.');
  const max = Math.max(...entries.map(e => e[1])) || 1;
  return h('div', null, entries.map(([k, v]) => h('div', { class: 'bar' }, h('span', { title: k, style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, k),
    h('div', { class: 't' }, h('span', { style: `width:${(v / max) * 100}%;${color ? 'background:' + color : ''}` })), h('span', { class: 'num muted' }, fmtNum(v)))));
}
export function lineChart(points, { w = 520, hgt = 150 } = {}) { // points: [{x:label,y:number}]
  if (!points.length) return empty('No data yet.');
  const max = Math.max(...points.map(p => p.y)) || 1, pad = 24;
  const X = i => pad + (points.length === 1 ? (w - 2 * pad) / 2 : (i * (w - 2 * pad)) / (points.length - 1));
  const Y = v => hgt - pad - (v / max) * (hgt - 2 * pad);
  const s = svg('svg', { viewBox: `0 0 ${w} ${hgt}`, style: 'width:100%;height:auto', role: 'img', 'aria-label': 'Trend chart' });
  const lg = 'lg' + Math.random().toString(36).slice(2, 7), pts = points.map((p, i) => `${X(i)},${Y(p.y)}`).join(' ');
  s.append(svg('defs', null, svg('linearGradient', { id: lg, x1: 0, y1: 0, x2: 0, y2: 1 }, svg('stop', { offset: '0%', 'stop-color': 'var(--accent)', 'stop-opacity': .28 }), svg('stop', { offset: '100%', 'stop-color': 'var(--accent)', 'stop-opacity': 0 }))));
  if (points.length > 1) s.append(svg('polygon', { points: `${X(0)},${hgt - pad} ${pts} ${X(points.length - 1)},${hgt - pad}`, fill: `url(#${lg})`, class: 'area' }));
  s.append(svg('polyline', { class: 'ln', pathLength: 1, points: pts, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2.2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  points.forEach((p, i) => s.append(svg('circle', { cx: X(i), cy: Y(p.y), r: 3, fill: 'var(--accent)' }, svg('title', null, `${p.x}: ${p.y}`))));
  s.append(svg('text', { x: pad, y: hgt - 6 }, points[0].x), svg('text', { x: w - pad, y: hgt - 6, 'text-anchor': 'end' }, points[points.length - 1].x), svg('text', { x: 4, y: 12 }, fmtNum(max)));
  return s;
}
export function columnChart(points, { w = 620, hgt = 140 } = {}) {
  if (!points.length) return empty('No data yet.');
  const max = Math.max(...points.map(p => p.y)) || 1, pad = 20, bw = Math.max(2, (w - 2 * pad) / points.length - 2);
  const s = svg('svg', { viewBox: `0 0 ${w} ${hgt}`, style: 'width:100%;height:auto', role: 'img', 'aria-label': 'Activity chart' });
  const gid = 'cg' + Math.random().toString(36).slice(2, 7);
  s.append(svg('defs', null, svg('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, svg('stop', { offset: '0%', 'stop-color': 'var(--accent)' }), svg('stop', { offset: '100%', 'stop-color': 'var(--accent2)', 'stop-opacity': .55 }))));
  points.forEach((p, i) => { const bh = (p.y / max) * (hgt - 2 * pad); s.append(svg('rect', { class: 'col', style: `--i:${i}`, x: pad + i * (bw + 2), y: hgt - pad - bh, width: bw, height: bh, rx: 2, fill: `url(#${gid})` }, svg('title', null, `${p.x}: ${p.y}`))); });
  s.append(svg('text', { x: pad, y: hgt - 5 }, points[0].x), svg('text', { x: w - pad, y: hgt - 5, 'text-anchor': 'end' }, points[points.length - 1].x));
  return s;
}
export function download(blob, name) {
  const a = h('a', { href: URL.createObjectURL(blob), download: name }); document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
