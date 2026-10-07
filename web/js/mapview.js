import { h } from './lib.js';

// Offline-first event map (Leaflet). The base map is a bundled outline of India and its neighbours plus a lat/long grid,
// so it works on a workstation with no internet. Street tiles are an opt-in extra layer (they contact openstreetmap.org).
// Event markers are grouped into clusters by screen distance; a cluster that cannot be split (identical coordinates) lists its events.

export const CAT_COLORS = {
  Communication: '#4f8bf7', Financial: '#e8a317', Meeting: '#a970f0', Vehicle: '#2fbf71', Location: '#e5647a',
  Incident: '#f97316', 'FIR / Legal': '#7b8aa6', Investigation: '#22b8cf', Evidence: '#84b813', Other: '#8b93a8',
};
export const catColor = c => CAT_COLORS[c] || CAT_COLORS.Other;

const css = (name, fallback) => (getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback);
let outlinePromise = null;
export function loadOutline() {
  if (!outlinePromise) {
    outlinePromise = fetch('/static/data/south_asia_outline.json', { credentials: 'same-origin' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .catch(() => { outlinePromise = null; return null; });
  }
  return outlinePromise;
}

const TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';

export function createEventMap(container, { onDetail, onEntity } = {}) {
  if (typeof L === 'undefined') return null;
  const map = L.map(container, { zoomControl: true, minZoom: 3, maxZoom: 18, worldCopyJump: false, zoomSnap: 0.5 }).setView([22.5, 79], 4.5);
  const state = { pts: [], outline: null, base: null, labels: null, tiles: null, tileErrors: 0, onTiles: null, destroyed: false };
  const renderer = L.canvas({ padding: 0.4 });
  const basePane = map.createPane('basePane'); basePane.style.zIndex = 150; // under the tile pane, so street tiles cover it when they load
  const layer = L.layerGroup().addTo(map);
  L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);
  map.attributionControl.setPrefix(false);
  map.attributionControl.addAttribution('Outline: Natural Earth (orientation only, not an official boundary)');

  // ---------- base map (theme aware: colours are read from CSS variables at draw time)
  function paintBase() {
    const land = css('--panel3', '#1a2440'), state_ = css('--panel2', '#141c31'), line = css('--line2', '#334'), muted = css('--muted', '#889');
    container.style.background = css('--bg2', '#080b14');
    if (state.base) {
      const each = fn => state.base.eachLayer(g => (g.eachLayer ? g.eachLayer(fn) : fn(g)));
      each(l => {
        const kind = l.feature && l.feature.properties && l.feature.properties.kind;
        if (kind === 'country') l.setStyle({ fillColor: land, fillOpacity: 0.55, color: line, weight: 0.8 });
        else if (kind === 'state') l.setStyle({ fillColor: state_, fillOpacity: 0.95, color: muted, weight: 0.6, opacity: 0.7 });
        else if (l.setStyle) l.setStyle({ color: line, weight: 0.5, opacity: 0.35 });
      });
    }
    if (state.labels) state.labels.eachLayer(l => { const el = l.getElement && l.getElement(); if (el && el.firstChild) el.firstChild.style.color = muted; });
  }
  function buildBase(outline) {
    state.base = L.featureGroup().addTo(map);
    const noClick = { interactive: false, pane: 'basePane' };
    if (outline) {
      const tag = (fc, kind) => (fc.features || []).forEach(f => { f.properties = { ...f.properties, kind }; });
      tag(outline.countries, 'country'); tag(outline.states, 'state');
      L.geoJSON(outline.countries, { ...noClick, style: () => ({ fill: true }) }).addTo(state.base);
      L.geoJSON(outline.states, { ...noClick, style: () => ({ fill: true }) }).addTo(state.base);
    }
    for (let lat = 0; lat <= 40; lat += 5) L.polyline([[lat, 55], [lat, 105]], { ...noClick, dashArray: '2 6', weight: 0.5 }).addTo(state.base);
    for (let lon = 55; lon <= 105; lon += 5) L.polyline([[0, lon], [40, lon]], { ...noClick, dashArray: '2 6', weight: 0.5 }).addTo(state.base);
    state.labels = L.layerGroup();
    (outline && outline.cities || []).forEach(([name, lat, lon]) => {
      const el = h('div', { style: 'font-size:10px;white-space:nowrap;pointer-events:none;transform:translate(6px,-6px);color:' + css('--muted', '#889') }, '· ' + name);
      state.labels.addLayer(L.marker([lat, lon], { interactive: false, keyboard: false, icon: L.divIcon({ html: el, className: '', iconSize: [0, 0] }) }));
    });
    const syncLabels = () => { if (map.getZoom() >= 5.5) { if (!map.hasLayer(state.labels)) state.labels.addTo(map); } else if (map.hasLayer(state.labels)) map.removeLayer(state.labels); };
    map.on('zoomend', syncLabels); syncLabels();
    paintBase();
  }
  const ready = loadOutline().then(o => { if (!state.destroyed) { state.outline = o; buildBase(o); state.pts.length && redraw(); } return !!o; });

  const mo = new MutationObserver(() => { if (!container.isConnected) { mo.disconnect(); return; } paintBase(); redraw(); });
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  // ---------- markers
  const fmt = s => { const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(s || ''); return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}` : (s || 'undated'); };
  function popupFor(list) {
    const box = h('div', { style: 'max-width:270px;max-height:240px;overflow:auto;font-size:12px' });
    if (list.length > 1) box.append(h('div', { style: 'font-weight:600;margin-bottom:4px' }, `${list.length} events at this spot`));
    list.slice(0, 12).forEach(e => {
      box.append(h('div', { style: 'padding:4px 0;border-top:1px solid ' + css('--line', '#ccc') },
        h('div', { style: 'font-weight:600' }, e.label || e.event_type),
        h('div', null, fmt(e.event_time) + (e.geo_precision === 'approximate' ? '  ·  approximate (place: ' + (e.geo_place || '?') + ')' : '')),
        h('div', null, [e.entity_name, e.related_entity_name].filter(Boolean).join(' → ')),
        e.amount != null ? h('div', null, 'Amount: ' + Number(e.amount).toLocaleString()) : null,
        e.source_ref ? h('div', { style: 'opacity:.75' }, 'Source: ' + e.source_ref) : null,
        h('div', { style: 'margin-top:3px;display:flex;gap:6px;flex-wrap:wrap' },
          onDetail ? h('button', { class: 'sm', onclick: () => onDetail(e) }, 'Details') : null,
          onEntity && e.entity_id ? h('button', { class: 'sm', onclick: () => onEntity(e.entity_id) }, 'Open in graph') : null)));
    });
    if (list.length > 12) box.append(h('div', { style: 'opacity:.75;padding-top:4px' }, `+ ${list.length - 12} more (use the filters or zoom the timeline to narrow down)`));
    return box;
  }
  function redraw() {
    layer.clearLayers();
    if (!state.pts.length) return;
    const z = map.getZoom(), cell = 42, bounds = map.getBounds().pad(0.3), groups = new Map();
    for (const e of state.pts) {
      if (!bounds.contains([e.map_lat, e.map_lon])) continue;
      const p = map.project([e.map_lat, e.map_lon], z), key = Math.floor(p.x / cell) + ':' + Math.floor(p.y / cell);
      const g = groups.get(key); if (g) g.push(e); else groups.set(key, [e]);
    }
    const stroke = css('--panel', '#0d1322');
    for (const list of groups.values()) {
      if (list.length === 1) {
        const e = list[0], approx = e.geo_precision === 'approximate';
        L.circleMarker([e.map_lat, e.map_lon], { renderer, radius: 6, color: stroke, weight: 1.2, fillColor: catColor(e.category), fillOpacity: approx ? 0.55 : 0.92, dashArray: approx ? '2 2' : null })
          .bindPopup(() => popupFor([e])).bindTooltip(e.label || e.event_type, { direction: 'top' }).addTo(layer);
        continue;
      }
      const lat = list.reduce((a, e) => a + e.map_lat, 0) / list.length, lon = list.reduce((a, e) => a + e.map_lon, 0) / list.length;
      const counts = {}; list.forEach(e => { counts[e.category] = (counts[e.category] || 0) + 1; });
      const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
      const size = Math.min(56, 22 + Math.log2(list.length) * 6);
      const el = h('div', { style: `width:${size}px;height:${size}px;border-radius:50%;background:${catColor(top)};opacity:.9;color:#fff;display:flex;align-items:center;justify-content:center;font:700 11px sans-serif;border:2px solid ${stroke};box-shadow:0 1px 6px rgba(0,0,0,.4);cursor:pointer` }, String(list.length));
      const m = L.marker([lat, lon], { icon: L.divIcon({ html: el, className: '', iconSize: [size, size] }), keyboard: true, title: `${list.length} events` }).addTo(layer);
      const same = list.every(e => e.map_lat === list[0].map_lat && e.map_lon === list[0].map_lon);
      const breakdown = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(', ');
      m.bindTooltip(breakdown, { direction: 'top' });
      if (same || z >= 16) m.bindPopup(() => popupFor(list));
      else m.on('click', () => map.fitBounds(L.latLngBounds(list.map(e => [e.map_lat, e.map_lon])), { padding: [40, 40], maxZoom: 17 }));
    }
  }
  map.on('moveend zoomend', redraw);

  const api_ = {
    map, ready,
    update(events, { fit = true } = {}) {
      state.pts = (events || []).filter(e => e.map_lat != null && e.map_lon != null);
      if (fit && state.pts.length) {
        const b = L.latLngBounds(state.pts.map(e => [e.map_lat, e.map_lon]));
        map.fitBounds(b, { padding: [30, 30], maxZoom: b.getNorthEast().equals(b.getSouthWest()) ? 12 : 13, animate: false });
      }
      redraw();
    },
    invalidate() { map.invalidateSize(); redraw(); },
    reset() { map.setView([22.5, 79], 4.5, { animate: false }); },
    fit() { if (state.pts.length) api_.update(state.pts, { fit: true }); else api_.reset(); },
    flyTo(lat, lon, zoom = 11) { map.flyTo([lat, lon], zoom, { duration: 0.6 }); },
    pin(lat, lon, label) { if (state.pin) state.pin.remove(); state.pin = L.marker([lat, lon], { title: label }).addTo(map).bindPopup(label).openPopup(); },
    // Optional street tiles. Off by default: it needs internet and tells openstreetmap.org which area is being viewed.
    setTiles(on, onStatus) {
      state.onTiles = onStatus || null;
      if (!on) { if (state.tiles) { state.tiles.remove(); state.tiles = null; } onStatus && onStatus('off'); return; }
      if (state.tiles) return;
      state.tileErrors = 0;
      state.tiles = L.tileLayer(TILE_URL, { maxZoom: 18, attribution: '© OpenStreetMap contributors' }).addTo(map);
      state.tiles.bringToBack();
      state.tiles.on('tileerror', () => { if (++state.tileErrors === 3) onStatus && onStatus('unreachable'); });
      state.tiles.on('tileload', () => { if (!state.tileOk) { state.tileOk = true; onStatus && onStatus('online'); } });
    },
    destroy() { state.destroyed = true; mo.disconnect(); try { map.remove(); } catch { /* already gone */ } },
  };
  return api_;
}
