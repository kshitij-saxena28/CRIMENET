// Hand-drawn 24px stroke icon set (no external assets). icon('graph') -> <svg>
const NS = 'http://www.w3.org/2000/svg';
const P = d => ['path', { d }];
const C = (cx, cy, r) => ['circle', { cx, cy, r }];
const R = (x, y, w, h, rx = 2) => ['rect', { x, y, width: w, height: h, rx }];
const ICONS = {
  logo: [P('M4 4h11l5 5v11H4z'),P('M15 4v5h5'),C(9,12,1.6),C(15,15,1.6),P('M10.4 12.6l3.2 1.8')],
  command: [R(3, 3, 7, 9), R(14, 3, 7, 5), R(14, 12, 7, 9), R(3, 16, 7, 5)],
  ingest: [P('M12 3v12'), P('m7 10 5 5 5-5'), P('M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2')],
  cases: [P('M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z')],
  graph: [C(6, 6, 2.5), C(18, 8, 2.5), C(9, 18, 2.5), P('M8.3 6.6l7.4 1M7.2 8.3l1.3 7.3M16.6 10.2l-6.2 6.2')],
  canvas: [R(3, 3, 18, 18, 4), P('M9 15l1-4 5-2-1 5z')],
  timeline: [C(12, 12, 9), P('M12 7v5l3 2')],
  analytics: [P('M5 20v-6M11 20V5M17 20v-9M3 20h18')],
  netai: [P('M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z'), P('M19 16v4M17 18h4')],
  copilot: [P('M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z'), P('M9 12h.01M12 12h.01M15 12h.01')],
  signals: [P('M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z'), P('m8.5 12 2.5 2.5 4.5-5')],
  social: [C(6, 12, 2.5), C(18, 6, 2.5), C(18, 18, 2.5), P('M8.3 11l7.4-4M8.3 13l7.4 4')],
  surveillance: [P('M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z'), C(12, 12, 3), P('M12 1v2M12 21v2')],
  integrity: [P('M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z'), P('M9 12h6M12 9v6')],
  back: [P('M19 12H5M11 6l-6 6 6 6')],
  plus: [P('M12 5v14M5 12h14')],
  workflow: [P('M9 6h11M9 12h11M9 18h11'), P('m3.5 6 1.2 1.2L7 5M3.5 12l1.2 1.2L7 11M3.5 18l1.2 1.2L7 17')],
  governance: [P('M3 21h18M5 21V10M9.5 21V10M14.5 21V10M19 21V10M2 10l10-6 10 6')],
  reports: [P('M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z'), P('M14 3v5h5M9 13h6M9 17h4')],
  admin: [P('M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1'), C(15, 6, 2), C(9, 12, 2), C(17, 18, 2)],
  suspects: [C(12, 8, 4), P('M4 21a8 8 0 0 1 16 0'), P('M17 4l3 3M20 4l-3 3')],
  requests: [P('M4 6h16v12H4z'), P('m4 7 8 6 8-6')],
  search: [C(11, 11, 7), P('m20 20-4-4')],
  bell: [P('M6 9a6 6 0 1 1 12 0c0 6 2 7 2 8H4c0-1 2-2 2-8z'), P('M10 21h4')],
  sun: [C(12, 12, 4), P('M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4')],
  moon: [P('M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z')],
  menu: [P('M4 7h16M4 12h16M4 17h16')],
  collapse: [P('m15 6-6 6 6 6')],
  logout: [P('M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M16 8l4 4-4 4M20 12H9')],
  user: [C(12, 8, 4), P('M4 21a8 8 0 0 1 16 0')],
  arrow: [P('M5 12h14M13 6l6 6-6 6')],
  check: [P('m5 12 5 5 9-10')],
  spark: [P('M13 2 4 14h7l-1 8 9-12h-7z')],
  eye: [P('M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z'), C(12, 12, 3)],
  layers: [P('m12 3 9 5-9 5-9-5z'), P('m3 13 9 5 9-5')],
  lock: [R(5, 11, 14, 10, 2.5), P('M8 11V8a4 4 0 0 1 8 0v3')],
  globe: [C(12, 12, 9), P('M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18')],
};
export function icon(name, cls = '') {
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); if (cls) s.setAttribute('class', cls);
  for (const [tag, attrs] of ICONS[name] || ICONS.command) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    s.append(e);
  }
  return s;
}
export const hasIcon = n => n in ICONS;
