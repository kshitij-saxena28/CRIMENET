import { h, api, mount, field, tabs, notice, empty, chip, toast, modal, svg, fmtNum, download } from './lib.js';
import { createEventMap, CAT_COLORS, catColor } from './mapview.js';

// Timeline & Map: what happened when (Timeline), where (Map), how FIRs line up (Compare FIRs).
// All times are shown exactly as recorded in the source documents (no timezone conversion).

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAY_MS = 86400000;
const dayOf = s => (s ? String(s).slice(0, 10) : '');
const dayNum = d => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d || ''); return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) / DAY_MS : NaN; };
const numDay = n => new Date(n * DAY_MS).toISOString().slice(0, 10);
const fmtT = s => { const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(s || ''); return m ? `${m[3]}/${m[2]}/${m[1]}` + (m[4] ? ` ${m[4]}:${m[5]}` : '') : (s || '–'); };
const fmtD = d => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d || ''); return m ? `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : (d || '–'); };
const fmtLen = hours => (hours >= 48 ? `${(hours / 24).toFixed(1)} days` : `${hours.toFixed(hours < 10 ? 1 : 0)} hours`);
const cap = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s));
const errMsg = ex => (ex && ex.message) || 'Something went wrong.';

function csvCell(v) {
  let s = v == null ? '' : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // stop spreadsheet formula injection
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

export async function render(root, ctx) {
  const body = h('div');
  mount(root, body);
  let seq = 0;

  async function load() {
    const mine = ++seq;
    mount(body, h('div', { class: 'skel', style: 'height:90px;margin-bottom:1rem' }), h('div', { class: 'skel', style: 'height:300px' }), h('p', { class: 'muted small' }, 'Loading events…'));
    let t;
    try { t = await api('/timeline', { params: { case_number: ctx.caseNumber, limit: 8000 } }); }
    catch (ex) {
      if (mine !== seq) return;
      mount(body, notice(ex.status === 403 ? 'Your role does not include timeline analysis.' : 'Could not load the timeline: ' + errMsg(ex), 'bad'), h('button', { onclick: load }, 'Try again'));
      return;
    }
    if (mine !== seq) return;
    if (!t.event_count) {
      mount(body, notice(ctx.caseNumber
        ? `No events are recorded for ${ctx.caseNumber} yet. Events appear when FIRs are verified (Ingestion & Review) or when a table of calls, transfers or sightings is imported.`
        : 'No events have been recorded yet. Verify a FIR or import a table of calls or transfers (Ingestion & Review). In the demo, an administrator can load sample data from the Admin Portal.', 'warn'));
      return;
    }
    build(t);
  }

  function build(t) {
    const f = { cat: '', fir: '', q: '', from: '', to: '' };
    const firs = Object.keys(t.fir_summary || {});
    const evAll = t.events || [];
    let active = 'tl', mapApi = null, playTimer = null, refreshMap = () => {};
    const stopPlay = () => { if (playTimer) { clearInterval(playTimer); playTimer = null; } };

    const filtered = () => {
      const needle = f.q.trim().toLowerCase();
      return evAll.filter(e => (!f.cat || e.category === f.cat) && (!f.fir || (e.fir_number || 'Unassigned') === f.fir)
        && (!f.from || (e.event_time && dayOf(e.event_time) >= f.from)) && (!f.to || (e.event_time && dayOf(e.event_time) <= f.to))
        && (!needle || `${e.label} ${e.entity_name} ${e.related_entity_name} ${e.entity_id} ${e.related_entity_id || ''} ${e.source_ref || ''} ${e.fir_number || ''} ${e.geo_place || ''} ${e.event_id} ${JSON.stringify(e.metadata || {})}`.toLowerCase().includes(needle)));
    };

    // ---------------------------------------------------------------- shared filter bar
    const cat = h('select', { 'aria-label': 'Category' }, h('option', { value: '' }, 'All categories'), (t.categories || []).map(c => h('option', { value: c }, c)));
    const fir = h('select', { 'aria-label': 'FIR' }, h('option', { value: '' }, 'All FIRs'), firs.map(x => h('option', { value: x }, x)));
    const q = h('input', { placeholder: 'Name, ID, source, place…', type: 'search', 'aria-label': 'Search events' });
    const from = h('input', { type: 'date', 'aria-label': 'From date' }), to = h('input', { type: 'date', 'aria-label': 'To date' });
    const summary = h('span', { class: 'muted small', role: 'status' });
    const reset = h('button', { class: 'sm', onclick: () => { Object.assign(f, { cat: '', fir: '', q: '', from: '', to: '' }); cat.value = fir.value = q.value = from.value = to.value = ''; changed(); } }, 'Clear filters');
    let qTimer = null;
    cat.addEventListener('change', () => { f.cat = cat.value; changed(); });
    fir.addEventListener('change', () => { f.fir = fir.value; changed(); });
    from.addEventListener('change', () => { f.from = from.value; changed(); });
    to.addEventListener('change', () => { f.to = to.value; changed(); });
    q.addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(() => { f.q = q.value; changed(); }, 180); });
    const filterBar = h('div', { class: 'row' }, field('Category', cat), field('FIR', fir), field('Search', q), field('From', from), field('To', to), reset, summary);
    const filterHolder = h('div');
    const setDates = (a, b) => { f.from = a; f.to = b; from.value = a; to.value = b; changed(); };
    const focusEntity = id => { ctx.graphFocus = id; if (ctx.go) ctx.go('graph'); else toast('Open the Knowledge Graph and search for ' + id); };

    // ---------------------------------------------------------------- event detail dialog
    function showDetail(e) {
      const rows = [['When', fmtT(e.event_time)], ['Category', e.category], ['Event type', e.event_type], ['FIR', e.fir_number || 'Unassigned'], ['Source', e.source_ref || 'none recorded'],
        ['Evidence', e.evidence_status], ['Confidence', e.confidence != null ? Math.round(e.confidence * 100) + '%' : ''], ['Amount', e.amount != null ? fmtNum(e.amount, 2) : ''],
        ['Duration', e.duration_seconds ? e.duration_seconds + ' s' : ''],
        ['Map position', e.map_lat != null ? `${e.map_lat.toFixed(4)}, ${e.map_lon.toFixed(4)} (${e.geo_source === 'event' ? 'recorded with the event' : e.geo_source === 'linked_location' ? 'from the linked location record' : 'approximate, matched from place name "' + (e.geo_place || '') + '"'})` : 'no place recorded'],
        ...Object.entries(e.metadata || {}).filter(([, v]) => v != null && v !== '' && typeof v !== 'object').map(([k, v]) => ['Note: ' + k.replace(/_/g, ' '), String(v)])];
      const ent = (label, id, name) => id ? h('div', { class: 'row center', style: 'margin:0 0 .3rem' }, h('span', null, `${label}: `, h('b', null, name || id)), h('button', { class: 'sm', onclick: () => { m.close(); focusEntity(id); } }, 'Open in graph')) : null;
      const m = modal(e.label || e.event_type, h('div', null,
        ent('Entity', e.entity_id, e.entity_name), ent('Related', e.related_entity_id, e.related_entity_name),
        h('dl', { class: 'kv', style: 'margin-top:.6rem' }, rows.filter(r => r[1] !== '' && r[1] != null).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])),
        e.map_lat != null ? h('button', { class: 'sm', style: 'margin-top:.8rem', onclick: () => { m.close(); pendingFocus = e; tabsEl.select('map'); pick('map'); } }, 'Show on map') : null));
    }
    let pendingFocus = null;

    // ---------------------------------------------------------------- TIMELINE tab
    function dailyChart(ev) {
      const dated = ev.filter(e => e.event_time);
      if (!dated.length) return empty('No dated events match the filters.');
      const nums = dated.map(e => dayNum(dayOf(e.event_time))).filter(n => !isNaN(n));
      const lo = Math.min(...nums), hi = Math.max(...nums), span = hi - lo + 1, step = Math.max(1, Math.ceil(span / 180)), nb = Math.ceil(span / step);
      const cats = (t.categories || []).filter(c => dated.some(e => e.category === c));
      const bins = Array.from({ length: nb }, () => ({ n: 0, c: {} }));
      dated.forEach(e => { const n = dayNum(dayOf(e.event_time)); if (isNaN(n)) return; const b = bins[Math.floor((n - lo) / step)]; b.n++; b.c[e.category] = (b.c[e.category] || 0) + 1; });
      const max = Math.max(...bins.map(b => b.n)), W = 900, H = 190, L = 40, B = 24, T = 8, pw = W - L - 8, bw = pw / nb, ph = H - B - T;
      const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, style: 'width:100%;height:auto', role: 'img', 'aria-label': `Events per ${step === 1 ? 'day' : step + ' days'}, from ${fmtD(numDay(lo))} to ${fmtD(numDay(hi))}, busiest ${max}` });
      [0, 0.5, 1].forEach(fr => { const y = T + ph - fr * ph; s.append(svg('line', { x1: L, x2: W - 8, y1: y, y2: y, stroke: 'var(--line)', 'stroke-width': 1 }), svg('text', { x: L - 6, y: y + 3, 'text-anchor': 'end' }, fmtNum(Math.round(max * fr)))); });
      bins.forEach((b, i) => {
        const x = L + i * bw, day0 = numDay(lo + i * step), day1 = numDay(Math.min(hi, lo + (i + 1) * step - 1));
        let y = T + ph;
        cats.forEach(c => { const n = b.c[c] || 0; if (!n) return; const hh = (n / max) * ph; y -= hh; s.append(svg('rect', { x: x + 0.5, y, width: Math.max(1, bw - 1), height: hh, fill: catColor(c) })); });
        s.append(svg('rect', { x, y: T, width: bw, height: ph, fill: 'transparent', style: 'cursor:pointer', tabindex: 0, role: 'button', 'aria-label': `${fmtD(day0)}: ${b.n} events. Activate to filter to this ${step === 1 ? 'day' : 'period'}` },
          svg('title', null, `${fmtD(day0)}${step > 1 ? ' – ' + fmtD(day1) : ''}: ${b.n} events` + cats.filter(c => b.c[c]).map(c => `\n${c}: ${b.c[c]}`).join(''))));
        const hit = s.lastChild;
        const act = () => { (f.from === day0 && f.to === day1) ? setDates('', '') : setDates(day0, day1); };
        hit.addEventListener('click', act); hit.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); act(); } });
      });
      const ticks = Math.min(6, nb);
      for (let i = 0; i <= ticks; i++) { const idx = Math.round((i / ticks) * (nb - 1)); s.append(svg('text', { x: L + idx * bw + bw / 2, y: H - 6, 'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle' }, fmtD(numDay(lo + idx * step)).slice(0, -5))); }
      return s;
    }

    function lanesChart(ev, by) {
      const dated = ev.filter(e => e.event_time);
      if (!dated.length) return empty('No dated events match the filters.');
      const keyOf = e => (by === 'cat' ? e.category : (e.fir_number || 'Unassigned'));
      const times = dated.map(e => +new Date(dayOf(e.event_time) + 'T' + (String(e.event_time).slice(11, 19) || '00:00:00')));
      const lo = Math.min(...times), hi = Math.max(...times), span = Math.max(1, hi - lo);
      const keys = [...new Set(dated.map(keyOf))].slice(0, 14), W = 900, L = 130, RH = 28;
      const s = svg('svg', { viewBox: `0 0 ${W} ${keys.length * RH + 26}`, style: 'width:100%;height:auto', role: 'img', 'aria-label': 'Swimlane timeline: one row per ' + (by === 'cat' ? 'category' : 'FIR') });
      let sampled = false;
      keys.forEach((k, i) => {
        s.append(svg('rect', { class: 'lane', x: L, y: i * RH + 3, width: W - L - 10, height: RH - 6, rx: 4 }), svg('text', { x: 4, y: i * RH + RH / 2 + 4 }, cap(k, 20), svg('title', null, k)));
        let evs = dated.filter(e => keyOf(e) === k);
        if (evs.length > 1200) { const stride = Math.ceil(evs.length / 1200); evs = evs.filter((_, j) => j % stride === 0); sampled = true; }
        evs.forEach(e => {
          const x = L + ((+new Date(dayOf(e.event_time) + 'T' + (String(e.event_time).slice(11, 19) || '00:00:00')) - lo) / span) * (W - L - 20) + 5;
          const c = svg('circle', { cx: x, cy: i * RH + RH / 2, r: 3, fill: catColor(e.category), opacity: 0.8, style: 'cursor:pointer' }, svg('title', null, `${fmtT(e.event_time)} · ${e.label}`));
          c.addEventListener('click', () => showDetail(e)); s.append(c);
        });
      });
      for (let i = 0; i <= 5; i++) s.append(svg('text', { x: L + (i / 5) * (W - L - 20) + 5, y: keys.length * RH + 18, 'text-anchor': i === 0 ? 'start' : i === 5 ? 'end' : 'middle' }, fmtD(new Date(lo + (i / 5) * span).toISOString().slice(0, 10)).slice(0, -5)));
      return h('div', null, s, sampled ? h('p', { class: 'muted small' }, 'Rows with more than 1,200 events show every Nth dot. Use the filters to see them all.') : null);
    }
    const legend = cats => h('div', { class: 'legend', style: 'margin-top:.4rem' }, cats.map(c => h('span', null, h('i', { style: `background:${catColor(c)}` }), c)));

    // sortable, paged event table
    const tbl = { sort: 'time', dir: 1, page: 0, size: 50 };
    const SORTS = { time: e => e.event_time || '9999', category: e => e.category, event: e => e.label, entity: e => (e.entity_name || '').toLowerCase(), related: e => (e.related_entity_name || '').toLowerCase(), amount: e => (e.amount == null ? -Infinity : e.amount), place: e => (e.geo_place || '').toLowerCase(), source: e => e.source_ref || '' };
    function eventTable(ev) {
      const rows = [...ev].sort((a, b) => { const x = SORTS[tbl.sort](a), y = SORTS[tbl.sort](b); return (x < y ? -1 : x > y ? 1 : 0) * tbl.dir || (a.sequence - b.sequence); });
      const pages = Math.max(1, Math.ceil(rows.length / tbl.size)); tbl.page = Math.min(tbl.page, pages - 1);
      const view = rows.slice(tbl.page * tbl.size, (tbl.page + 1) * tbl.size);
      const col = (key, label, num) => h('th', { class: num ? 'num' : '', 'aria-sort': tbl.sort === key ? (tbl.dir > 0 ? 'ascending' : 'descending') : 'none' },
        h('button', { class: 'sm', style: 'border:0;background:transparent;padding:0;font:inherit;text-transform:inherit;letter-spacing:inherit;color:inherit', title: 'Sort by ' + label, onclick: () => { if (tbl.sort === key) tbl.dir *= -1; else { tbl.sort = key; tbl.dir = 1; } drawList(); } }, label + (tbl.sort === key ? (tbl.dir > 0 ? ' ▲' : ' ▼') : '')));
      const link = (id, name) => (id ? h('button', { class: 'sm', style: 'border:0;background:transparent;padding:0;color:var(--accent2);text-decoration:underline', title: 'Open ' + (name || id) + ' in the knowledge graph', onclick: e => { e.stopPropagation(); focusEntity(id); } }, cap(name || id, 28)) : '');
      const tb = h('tbody', null, view.map(e => h('tr', { style: 'cursor:pointer', tabindex: 0, onclick: () => showDetail(e), onkeydown: k => { if (k.key === 'Enter') showDetail(e); } },
        h('td', null, fmtT(e.event_time)), h('td', null, h('span', { class: 'chip' }, h('i', { style: `display:inline-block;width:8px;height:8px;border-radius:50%;background:${catColor(e.category)}` }), e.category)),
        h('td', null, e.label), h('td', null, link(e.entity_id, e.entity_name)), h('td', null, link(e.related_entity_id, e.related_entity_name)),
        h('td', { class: 'num' }, e.amount != null ? fmtNum(e.amount, 2) : ''), h('td', null, e.geo_place ? e.geo_place + (e.geo_source === 'gazetteer' ? ' (≈)' : '') : (e.map_lat != null ? 'mapped' : '')), h('td', null, e.source_ref || ''))));
      const pager = h('div', { class: 'row center', style: 'margin:.6rem 0 0' },
        h('button', { class: 'sm', disabled: tbl.page === 0, onclick: () => { tbl.page--; drawList(); } }, '‹ Previous'),
        h('span', { class: 'muted small' }, `Page ${tbl.page + 1} of ${pages} · rows ${rows.length ? tbl.page * tbl.size + 1 : 0}–${Math.min(rows.length, (tbl.page + 1) * tbl.size)} of ${fmtNum(rows.length)}`),
        h('button', { class: 'sm', disabled: tbl.page >= pages - 1, onclick: () => { tbl.page++; drawList(); } }, 'Next ›'),
        h('button', { class: 'sm', onclick: () => exportCsv(rows) }, 'Download CSV'));
      if (!rows.length) return empty('No events match the filters. Use “Clear filters” to start again.');
      return h('div', null, h('div', { class: 'tw' }, h('table', null, h('thead', null, h('tr', null, col('time', 'Time'), col('category', 'Category'), col('event', 'Event'), col('entity', 'Entity'), col('related', 'Related'), col('amount', 'Amount', true), col('place', 'Place'), col('source', 'Source'))), tb)), pager);
    }
    function exportCsv(rows) {
      const head = ['event_id', 'time', 'category', 'event_type', 'entity_id', 'entity_name', 'related_id', 'related_name', 'amount', 'fir', 'place', 'lat', 'lon', 'position_source', 'source_ref'];
      const lines = [head.join(','), ...rows.map(e => [e.event_id, e.event_time, e.category, e.event_type, e.entity_id, e.entity_name, e.related_entity_id, e.related_entity_name, e.amount, e.fir_number, e.geo_place, e.map_lat, e.map_lon, e.geo_source, e.source_ref].map(csvCell).join(','))];
      download(new Blob([lines.join('\r\n')], { type: 'text/csv' }), `timeline-${ctx.caseNumber || 'all-cases'}.csv`);
      toast(`Exported ${rows.length} events`, 'good');
    }

    const tl = { chart: h('div'), lanes: h('div'), list: h('div'), by: fir.options.length > 2 ? 'fir' : 'cat' };
    const laneBy = h('select', { 'aria-label': 'Group swimlanes by' }, h('option', { value: 'fir', selected: tl.by === 'fir' }, 'FIR'), h('option', { value: 'cat', selected: tl.by === 'cat' }, 'Category'));
    laneBy.addEventListener('change', () => { tl.by = laneBy.value; drawLaneCard(); });
    function drawLaneCard() { const ev = filtered(); mount(tl.lanes, lanesChart(ev, tl.by), legend((t.categories || []).filter(c => ev.some(e => e.category === c)))); }
    function drawList() { mount(tl.list, eventTable(filtered())); }
    function drawTimeline() {
      const ev = filtered();
      mount(tl.chart, dailyChart(ev), legend((t.categories || []).filter(c => ev.some(e => e.category === c))));
      drawLaneCard(); drawList();
    }
    const timelineTab = h('div', null,
      h('details', { class: 'whatis' }, h('summary', null, 'What am I looking at?'), h('p', { class: 'muted small' }, 'Every recorded event for this case in time order: calls, transfers, sightings, meetings and FIR milestones. The bars show how busy each day was (click a bar to focus on that day). The rows show when things happened in each FIR or category. The table lists the events; click one for details or to open the person or number in the graph.')),
      filterHolder,
      h('div', { class: 'card' }, h('h4', null, 'Daily activity'), tl.chart),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('div', { class: 'row center', style: 'margin:0 0 .4rem' }, h('h4', { style: 'flex:1;margin:0' }, 'Swimlanes'), field('Rows by', laneBy)), tl.lanes),
      h('div', { class: 'card', style: 'margin-top:1rem' }, h('h4', null, 'Events'), tl.list));

    // ---------------------------------------------------------------- MAP tab
    function buildMapTab() {
      const box = h('div', { class: 'map', role: 'application', 'aria-label': 'Event map', style: 'height:560px' });
      const note = h('p', { class: 'muted small', role: 'status' });
      const mapLegend = h('div');
      const tileNote = h('span', { class: 'muted small' });
      const tilesCb = h('input', { type: 'checkbox', id: 'tm-tiles' });
      const slider = h('input', { type: 'range', min: 0, max: 0, value: 0, style: 'flex:1;min-width:160px', 'aria-label': 'Show events up to this day' });
      const sliderLabel = h('span', { class: 'small', style: 'min-width:9rem', role: 'status' });
      const cum = h('input', { type: 'checkbox', checked: true, id: 'tm-cum' });
      const playBtn = h('button', { class: 'sm', title: 'Play the events forward day by day' }, '▶ Play');
      const place = h('input', { placeholder: 'Find a place (e.g. Noida)', 'aria-label': 'Find a place', style: 'width:200px' });
      const placeOut = h('span', { class: 'muted small' });
      let days = [], idx = 0, mapped = [];

      const windowed = () => {
        if (!days.length || idx >= days.length - 1 && cum.checked) return mapped;
        const d = days[idx]; return mapped.filter(e => (cum.checked ? dayOf(e.event_time) <= d : dayOf(e.event_time) === d));
      };
      function paintLegend(list) {
        const counts = {}; list.forEach(e => { counts[e.category] = (counts[e.category] || 0) + 1; });
        mount(mapLegend, h('div', { class: 'legend' }, Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([c, n]) => h('span', null, h('i', { style: `background:${catColor(c)}` }), `${c} ${fmtNum(n)}`)),
          list.some(e => e.geo_precision === 'approximate') ? h('span', null, h('i', { style: 'background:transparent;border:1px dashed var(--muted)' }), 'faint = approximate position') : null));
      }
      function paintSlider() {
        const d = days[idx];
        sliderLabel.textContent = !days.length ? 'no dated events' : (idx >= days.length - 1 && cum.checked ? 'all dates (to ' + fmtD(d) + ')' : (cum.checked ? 'up to ' : 'only ') + fmtD(d));
      }
      function draw({ fit }) {
        const ev = filtered(); mapped = ev.filter(e => e.map_lat != null);
        if (fit) {
          days = [...new Set(mapped.filter(e => e.event_time).map(e => dayOf(e.event_time)))].sort();
          idx = Math.max(0, days.length - 1); slider.max = Math.max(0, days.length - 1); slider.value = idx; slider.disabled = days.length < 2; playBtn.disabled = days.length < 2;
        }
        const shown = windowed();
        paintLegend(shown); paintSlider();
        const approx = mapped.filter(e => e.geo_precision === 'approximate').length;
        note.textContent = `${fmtNum(shown.length)} of ${fmtNum(ev.length)} matching events are on the map` + (ev.length - mapped.length ? `; ${fmtNum(ev.length - mapped.length)} have no place recorded (for example bank transfers) and cannot be shown` : '') + (approx ? `; ${fmtNum(approx)} positions are approximate (matched from a place name)` : '') + '.';
        if (mapApi) mapApi.update(shown, { fit });
      }
      refreshMap = () => draw({ fit: true });
      slider.addEventListener('input', () => { stopPlay(); playBtn.textContent = '▶ Play'; idx = +slider.value; draw({ fit: false }); });
      cum.addEventListener('change', () => { stopPlay(); playBtn.textContent = '▶ Play'; draw({ fit: false }); });
      playBtn.addEventListener('click', () => {
        if (playTimer) { stopPlay(); playBtn.textContent = '▶ Play'; return; }
        if (idx >= days.length - 1) { idx = 0; }
        playBtn.textContent = '⏸ Pause';
        playTimer = setInterval(() => {
          if (!box.isConnected) return stopPlay();
          idx++; slider.value = idx; draw({ fit: false });
          if (idx >= days.length - 1) { stopPlay(); playBtn.textContent = '▶ Play'; }
        }, 700);
        slider.value = idx; draw({ fit: false });
      });
      tilesCb.addEventListener('change', () => {
        try { localStorage.setItem('dcn-map-tiles', tilesCb.checked ? '1' : '0'); } catch { /* not critical */ }
        if (mapApi) mapApi.setTiles(tilesCb.checked, st => { tileNote.textContent = st === 'unreachable' ? 'Street tiles are unreachable (no internet?). The offline map is still shown.' : st === 'online' ? 'Street tiles loaded.' : ''; if (st === 'unreachable') { tilesCb.checked = false; mapApi.setTiles(false); } });
      });
      const go = async () => {
        const v = place.value.trim(); if (!v) return;
        try {
          const r = await api('/gazetteer/search', { params: { q: v, limit: 1 } });
          const p = r.places[0]; if (!p) { placeOut.textContent = 'Not in the offline place list.'; return; }
          placeOut.textContent = `${p.name}${p.state ? ', ' + p.state : ''} (approximate)`; mapApi.pin(p.lat, p.lon, p.name); mapApi.flyTo(p.lat, p.lon, 10);
        } catch (ex) { toast(errMsg(ex), 'bad'); }
      };
      place.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
      const el = h('div', null,
        h('details', { class: 'whatis' }, h('summary', null, 'What am I looking at?'), h('p', { class: 'muted small' }, 'Where events happened. Coloured dots are single events; numbered circles group nearby events (click to zoom in). Dots come from the coordinates recorded with the event or its linked location; faint dots are placed from a place name using the built-in list of Indian places, so treat them as approximate. The map works without internet.')),
        filterHolder,
        h('div', { class: 'row center' }, h('button', { class: 'sm', onclick: () => mapApi && mapApi.fit() }, 'Fit to events'), h('button', { class: 'sm', onclick: () => mapApi && mapApi.reset() }, 'Reset view'), place, h('button', { class: 'sm', onclick: go }, 'Go'), placeOut,
          h('span', { style: 'flex:1' }), h('label', { style: 'flex-direction:row;align-items:center;gap:.4rem' }, tilesCb, 'Show street tiles (needs internet; contacts openstreetmap.org)'), tileNote),
        h('div', { class: 'row center' }, playBtn, slider, sliderLabel, h('label', { style: 'flex-direction:row;align-items:center;gap:.4rem' }, cum, 'Keep earlier events')),
        box, note, mapLegend);
      return { el, box, start() {
        mapApi = createEventMap(box, { onDetail: showDetail, onEntity: focusEntity });
        if (!mapApi) { mount(box, notice('The map library failed to load.', 'bad')); return; }
        let wantTiles = false; try { wantTiles = localStorage.getItem('dcn-map-tiles') === '1'; } catch { /* ignore */ }
        if (wantTiles) { tilesCb.checked = true; tilesCb.dispatchEvent(new Event('change')); }
        mapApi.ready.then(ok => { if (!ok) note.textContent = 'The offline outline could not be loaded; markers are still shown on a plain grid. ' + note.textContent; });
        draw({ fit: true });
        requestAnimationFrame(() => { mapApi && mapApi.invalidate(); if (pendingFocus) { const e = pendingFocus; pendingFocus = null; mapApi.flyTo(e.map_lat, e.map_lon, 13); mapApi.pin(e.map_lat, e.map_lon, e.label + ' · ' + fmtT(e.event_time)); } });
      } };
    }
    let mapTab = null;

    // ---------------------------------------------------------------- COMPARE tab
    function buildCompare() {
      const eligible = firs.filter(x => x !== 'Unassigned');
      const out = h('div');
      if (!ctx.caseNumber) return h('div', null, notice('Comparing FIRs works inside one case. Pick a case in the top bar first.', 'warn'));
      if (eligible.length < 2) return h('div', null, notice(`Only ${eligible.length} FIR with dated events was found in ${ctx.caseNumber}. Comparing needs at least two verified FIRs. Upload and verify them under Ingestion & Review.`, 'warn'));
      const checks = eligible.map(x => { const cb = h('input', { type: 'checkbox', value: x, id: 'tm-cmp-' + x }); return { x, cb, el: h('label', { style: 'flex-direction:row;align-items:center;gap:.5rem;font-size:.85rem;color:var(--text)' }, cb, `${x} `, h('span', { class: 'muted small' }, `(${t.fir_summary[x].events} events)`)) }; });
      const run = h('button', { class: 'primary' }, 'Compare selected FIRs');
      run.addEventListener('click', async () => {
        const sel = checks.filter(c => c.cb.checked).map(c => c.x);
        if (sel.length < 2) return toast('Tick at least two FIRs', 'bad');
        run.disabled = true; mount(out, h('div', { class: 'skel', style: 'height:120px' }));
        try {
          const r = await api('/timeline/compare', { params: { case_number: ctx.caseNumber, fir_numbers: sel.join(',') } });
          const shared = r.interlinks || [];
          mount(out,
            shared.length ? notice('Shared identifiers found: ' + shared.map(l => `${l.source_fir} ↔ ${l.target_fir} (${l.shared_keys.map(k => k.type + ': ' + k.values.join(', ')).join('; ')})`).join(' | '), 'good')
              : notice('No shared phone numbers, vehicles, accounts or other identifiers between the selected FIRs. Only verified FIRs can be matched.'),
            h('div', { class: 'grid g3', style: 'margin-bottom:1rem' }, sel.map(x => { const s = (r.summaries || {})[x] || {}; return h('div', { class: 'card' }, h('h4', null, x), h('div', { style: 'font-size:1.6rem;font-weight:600' }, fmtNum(s.events || 0), h('span', { class: 'muted small' }, ' events')), h('div', { class: 'muted small' }, s.first_event ? `${fmtT(s.first_event)} → ${fmtT(s.last_event)}` : 'no dated events'), h('div', { style: 'margin-top:.3rem' }, chip(s.verified ? 'verified' : 'not verified', s.verified ? 'good' : 'warn'))); })),
            (r.overlap_days || []).length ? notice(`Days with activity in more than one selected FIR: ${(r.overlap_days || []).slice(0, 12).map(o => fmtD(o.date)).join(', ')}${r.overlap_days.length > 12 ? ' …' : ''}`) : null,
            h('div', { class: 'card', style: 'margin-bottom:1rem' }, h('h4', null, 'Side by side'), lanesChart(r.events || [], 'fir'), legend(t.categories || [])),
            h('div', { class: 'grid g2' }, sel.map(x => h('div', { class: 'card' }, h('h4', null, x), eventsMini((r.by_fir || {})[x] || [])))));
        } catch (ex) { mount(out, notice('Comparison failed: ' + errMsg(ex), 'bad')); } finally { run.disabled = false; }
      });
      return h('div', null, h('details', { class: 'whatis' }, h('summary', null, 'What am I looking at?'), h('p', { class: 'muted small' }, 'Two or more FIRs from this case laid next to each other: when their events happened, days they overlap and any phone number, vehicle, account or other identifier they share (a shared identifier is a lead, not proof).')),
        h('div', { class: 'card' }, h('h4', null, 'Choose FIRs'), h('div', { class: 'row', style: 'align-items:center' }, checks.map(c => c.el)), run), h('div', { style: 'margin-top:1rem' }, out));
    }
    function eventsMini(evs) {
      if (!evs.length) return empty('No events for this FIR.');
      const rows = evs.slice(0, 40);
      return h('div', null, h('div', { class: 'tw' }, h('table', null, h('thead', null, h('tr', null, h('th', null, 'Time'), h('th', null, 'Event'))), h('tbody', null, rows.map(e => h('tr', { style: 'cursor:pointer', onclick: () => showDetail(e) }, h('td', null, fmtT(e.event_time)), h('td', null, e.label)))))),
        evs.length > 40 ? h('p', { class: 'muted small' }, `Showing 40 of ${evs.length}.`) : null);
    }

    // ---------------------------------------------------------------- wiring
    function changed() {
      const n = filtered().length;
      summary.textContent = `${fmtNum(n)} of ${fmtNum(t.event_count)} events` + (t.truncated ? ` (first ${fmtNum(t.returned_events)} loaded)` : '');
      tbl.page = 0;
      if (active === 'tl') drawTimeline(); else if (active === 'map') refreshMap();
    }
    function pick(id) {
      stopPlay();
      if (mapApi) { mapApi.destroy(); mapApi = null; }
      active = id;
      const holder = { tl: timelineTab, map: null, cmp: null };
      if (id === 'map') { mapTab = buildMapTab(); holder.map = mapTab.el; }
      if (id === 'cmp') holder.cmp = buildCompare();
      mount(view, holder[id]);
      if (id !== 'cmp') { mountFilters(); }
      if (id === 'tl') drawTimeline();
      if (id === 'map') mapTab.start();
      changedSummary();
    }
    function mountFilters() { const holders = view.querySelectorAll('[data-tm-filters]'); holders.forEach(x => mount(x, filterBar)); }
    function changedSummary() { const n = filtered().length; summary.textContent = `${fmtNum(n)} of ${fmtNum(t.event_count)} events` + (t.truncated ? ` (first ${fmtNum(t.returned_events)} loaded)` : ''); }
    filterHolder.setAttribute('data-tm-filters', '');

    const view = h('div');
    const tabsEl = tabs([['tl', 'Timeline'], ['map', 'Map'], ['cmp', 'Compare FIRs']], 'tl', pick);
    const chips = h('div', { class: 'row center', style: 'margin:0 0 .8rem' },
      chip(`${fmtNum(t.event_count)} events`), chip(`${fmtNum(t.dated_event_count)} dated`), chip(`${fmtNum(t.mapped_event_count)} with a map position`), chip(`${Math.max(0, firs.filter(x => x !== 'Unassigned').length)} FIRs`),
      t.masked ? chip('identifiers masked for your role', 'warn') : null);
    mount(body, t.truncated ? notice(`Only the first ${fmtNum(t.returned_events)} of ${fmtNum(t.event_count)} events are loaded. Pick a single case in the top bar to see everything.`, 'warn') : null, chips, tabsEl, view);
    pick('tl');
  }

  await load();
}
