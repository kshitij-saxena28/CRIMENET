// Surveillance reports: an authorised operation, an append-only observation log with a tamper-evident hash chain,
// movement analysis and printable reports. Entries are never edited: a correction is a new linked version with a reason.
import { h, api, mount, notice, empty, chip, toast, kpi, table, tabs, field, modal, svg, fmtNum, download } from './lib.js';
import { createEventMap } from './mapview.js';

const errBox = ex => notice(ex.message || String(ex), 'bad');
const ZONES = [['IST', 'India (IST, UTC+5:30)'], ['UTC', 'UTC'], ['GST', 'Gulf (UTC+4)'], ['PKT', 'Pakistan (UTC+5)'], ['BST', 'Bangladesh (UTC+6)'], ['SGT', 'Singapore (UTC+8)'], ['EST', 'US Eastern (UTC-5)']];
const ROLES = [['associate', 'Associate'], ['contact', 'Contact'], ['unknown', 'Unknown'], ['other', 'Other']];
const RATING_KIND = { A: 'good', B: 'good', C: '', D: 'warn', E: 'bad', F: 'warn' };
const sel = (opts, value, onchange, label) => h('select', { 'aria-label': label, onchange: e => onchange(e.target.value) }, opts.map(([v, l]) => h('option', { value: v, selected: v === value }, l)));
const oneLine = text => h('p', { class: 'muted sv-line' }, text);
const short = s => (s ? String(s).slice(0, 12) + '...' : '–');
const nowLocal = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
const ratingChip = (r, scales) => {
  const tip = scales ? `${r[0]}: ${scales.source[r[0]] || ''}\n${r[1]}: ${scales.information[r[1]] || ''}` : r;
  const c = chip(r, RATING_KIND[r[0]] || ''); c.title = tip; return c;
};
// A datetime-local value has no zone. The backend interprets it in the zone the officer picks next to it.
const localToIso = v => (v ? v + ':00' : '');

export async function render(root, ctx) {
  if (!ctx.caseNumber) { mount(root, notice('Select a case to work with its surveillance operations.', 'warn')); return; }
  const cn = encodeURIComponent(ctx.caseNumber);
  const canWrite = ctx.can('write'), canAnalyze = ctx.can('analyze'), canReport = ctx.can('report'), canApprove = ctx.can('approve');
  const host = h('div', { class: 'sv-page' });
  mount(root, host);
  let entities = null;
  async function loadEntities() {
    if (entities) return entities;
    try { const ws = await api(`/cases/${cn}/workspace`); entities = (ws.entities || []).map(e => ({ id: e.external_id || e.id, name: e.name || e.external_id || e.id })); } catch { entities = []; }
    return entities;
  }

  // ------------------------------------------------------------------ list
  async function showList() {
    mount(host, empty('Loading...'));
    let r;
    try { r = await api(`/surveillance/operations`, { params: { case_number: ctx.caseNumber } }); } catch (ex) { mount(host, errBox(ex)); return; }
    const cta = canWrite ? h('div', { class: 'row sv-cta' }, h('button', { class: 'primary', onclick: createDialog }, 'New operation'),
      h('button', { onclick: loadSample }, 'Load a sample operation')) : null;
    mount(host, h('div', { class: 'sv-head' }, h('h2', null, 'Surveillance operations'),
      h('p', { class: 'muted' }, 'Each operation records the authority it runs under and an observation log that cannot be edited or deleted. Corrections are added as amendments with a reason. Reports show the authority and an integrity footer.')),
      notice(r.notice, 'warn'),
      h('div', { class: 'card' }, cta,
        table([{ label: 'Operation', render: o => h('button', { class: 'link', onclick: () => showDetail(o.op_number) }, o.op_number + (o.codename ? ` (${o.codename})` : '')) },
          { label: 'Subject', render: o => o.subject }, { label: 'Status', render: o => chip(o.status, o.status === 'closed' ? '' : 'good') },
          { label: 'Authority', render: o => o.authority_ref }, { label: 'Supervising officer', render: o => o.supervising_officer },
          { label: 'Entries', num: true, render: o => o.entry_count }, { label: 'Started', render: o => (o.start_at || '').slice(0, 10) }],
        r.operations, { emptyText: canWrite ? 'No operations for this case yet. Create one, or load a sample operation to see how it works.' : 'No operations for this case yet.' })),
      ratingsHelp(r.scales),
      h('details', { class: 'sv-explain' }, h('summary', null, 'Limits of this tool'), h('ul', null, r.limits.map(l => h('li', null, l)))));
  }
  function ratingsHelp(scales) {
    return h('details', { class: 'sv-explain' }, h('summary', null, 'How to read the A to F and 1 to 6 ratings'),
      h('p', { class: 'muted' }, 'Each entry carries two ratings: how reliable the observer or source is (A to F) and how far the information is confirmed (1 to 6). "F6" means the reliability and the truth of the information cannot yet be judged. The ratings are the observer\'s assessment, not a finding of this tool.'),
      h('div', { class: 'grid g2' },
        h('div', null, h('h4', null, 'Source reliability'), h('dl', { class: 'kv' }, Object.entries(scales.source).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)]))),
        h('div', null, h('h4', null, 'Information rating'), h('dl', { class: 'kv' }, Object.entries(scales.information).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])))));
  }
  async function loadSample() {
    try { const r = await api('/surveillance/sample', { method: 'POST', body: { case_number: ctx.caseNumber } }); toast('Sample operation loaded (synthetic data).', 'good'); showDetail(r.operation.op_number); } catch (ex) { toast(ex.message, 'bad'); }
  }
  async function createDialog() {
    const ents = await loadEntities();
    const f = {
      codename: h('input', { placeholder: 'optional' }), subjectEnt: h('select', null, h('option', { value: '' }, '(none, describe below)'), ents.map(e => h('option', { value: e.id }, `${e.name} (${e.id})`))),
      subjectText: h('input', { placeholder: 'e.g. person known as "Bunty", or vehicle DL3C...' }), objective: h('textarea', { rows: 2, placeholder: 'What the operation is meant to establish' }),
      authText: h('textarea', { rows: 2, placeholder: 'Who authorised it and under what provision' }), authRef: h('input', { placeholder: 'Order or permission number' }),
      start: h('input', { type: 'datetime-local', value: nowLocal() }), end: h('input', { type: 'datetime-local' }), tz: sel(ZONES, 'IST', () => {}, 'Time zone'),
      sup: h('input', { placeholder: 'Rank and name' }), team: h('input', { placeholder: 'Names, comma separated' }),
    };
    const err = h('div');
    const m = modal('New surveillance operation', h('div', null,
      notice('You must record the authority before an operation can start. It is shown on every report.', 'warn'), err,
      h('div', { class: 'grid g2' }, field('Codename', f.codename), field('Supervising officer *', f.sup), field('Subject (case entity)', f.subjectEnt), field('Or subject in words', f.subjectText)),
      field('Objective *', f.objective), field('Authority (text) *', f.authText), field('Authority reference *', f.authRef),
      h('div', { class: 'grid g3' }, field('Start *', f.start), field('End (optional)', f.end), field('Time zone of these times', f.tz)),
      field('Team', f.team),
      h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async () => {
        try {
          const tz = f.tz.value;
          const r = await api('/surveillance/operations', { method: 'POST', body: {
            case_number: ctx.caseNumber, codename: f.codename.value, subject_entity_id: f.subjectEnt.value, subject_text: f.subjectText.value, objective: f.objective.value,
            authority_text: f.authText.value, authority_ref: f.authRef.value, start_at: localToIso(f.start.value), end_at: localToIso(f.end.value), timezone: tz,
            supervising_officer: f.sup.value, team: f.team.value.split(',').map(s => s.trim()).filter(Boolean) } });
          m.close(); toast('Operation created.', 'good'); showDetail(r.operation.op_number);
        } catch (ex) { mount(err, errBox(ex)); }
      } }, 'Create operation'))), { wide: true });
  }

  // ------------------------------------------------------------------ detail
  async function showDetail(op, tabId = 'log', tz = 'IST') {
    mount(host, empty('Loading...'));
    let d;
    try { d = await api(`/surveillance/${encodeURIComponent(op)}`, { params: { tz } }); } catch (ex) { mount(host, h('div', null, h('button', { onclick: showList }, 'Back to operations'), errBox(ex))); return; }
    const o = d.operation, st = { tab: tabId, tz, integrity: d.integrity };
    const body = h('div', { class: 'sv-body' });
    const badge = h('span');
    const setBadge = i => {
      const ok = i.status === 'verified';
      mount(badge, h('span', { class: 'sv-badge ' + (ok ? 'sv-ok' : 'sv-broken'), role: 'status' }, ok ? `Chain verified (${i.entries_checked} entries)` : 'Chain BROKEN: see details'));
    };
    setBadge(d.integrity);
    const verifyBtn = h('button', { class: 'sm', onclick: async () => {
      try { const i = await api(`/surveillance/${encodeURIComponent(op)}/verify`); st.integrity = i; setBadge(i); integrityDialog(i); } catch (ex) { toast(ex.message, 'bad'); }
    } }, 'Verify now');
    const items = [['log', 'Log']];
    if (canWrite && d.can_add) items.push(['add', 'Add entry']);
    if (canAnalyze) items.push(['move', 'Movement']);
    if (canReport) items.push(['report', 'Reports']);
    items.push(['info', 'Operation']);
    const bar = tabs(items, items.some(i => i[0] === tabId) ? tabId : 'log', id => { st.tab = id; draw(); });
    const tzSel = sel(ZONES, tz, v => { st.tz = v; refresh(); }, 'Display time zone');
    mount(host, h('div', { class: 'sv-head' }, h('button', { class: 'sm', onclick: showList }, 'Back to operations'),
      h('div', { class: 'row sv-title' }, h('h2', null, `${o.op_number}${o.codename ? ' (' + o.codename + ')' : ''}`), chip(o.status, o.status === 'closed' ? '' : 'good'), badge, verifyBtn),
      h('p', { class: 'muted' }, `Subject: ${o.subject || '–'}. Authority: ${o.authority_ref}. Times shown in `, tzSel)),
      d.masked ? notice('Details are hidden for your role: observation text, names, registrations and coordinates are masked.', 'warn') : null,
      o.status === 'closed' ? notice(`Closed by ${o.closed_by} on ${(o.closed_at || '').slice(0, 10)}. A closed operation accepts no further entries.`) : null,
      bar, body);
    const refresh = () => showDetail(op, st.tab, st.tz);

    function integrityDialog(i) {
      modal('Integrity check', h('div', null, h('p', null, i.explain),
        h('dl', { class: 'kv' }, h('dt', null, 'Result'), h('dd', null, i.status), h('dt', null, 'Entries checked'), h('dd', null, i.entries_checked), h('dt', null, 'Head hash'), h('dd', { class: 'mono sv-hash' }, i.head_hash),
          h('dt', null, 'Audit anchors'), h('dd', null, `${i.audit_anchor.anchored} matched, ${i.audit_anchor.missing} missing, ${i.audit_anchor.mismatched} mismatched`), h('dt', null, 'Checked at'), h('dd', null, i.checked_at)),
        i.problems.length ? h('div', null, h('h4', null, 'Problems found'), h('ul', null, i.problems.map(p => h('li', null, `${p.type}${p.seq ? ' at entry ' + p.seq : ''}${p.detail ? ': ' + p.detail : ''}`)))) : h('p', { class: 'muted' }, 'No problems found.')));
    }

    function draw() {
      mount(body, empty('Loading...'));
      ({ log: logTab, add: addTab, move: moveTab, report: reportTab, info: infoTab }[st.tab])().catch(ex => mount(body, errBox(ex)));
    }

    async function logTab() {
      const cur = d.entries.filter(e => e.current);
      mount(body, h('div', { class: 'card' }, oneLine('Entries in time order of recording. An amended entry stays visible, marked as replaced, together with the reason.'),
        d.entries.length ? h('div', { class: 'sv-log' }, d.entries.map(entryCard)) : empty(canWrite && d.can_add ? 'No entries yet. Use "Add entry" to record the first observation.' : 'No entries recorded.'),
        h('p', { class: 'muted small' }, `${cur.length} current entr${cur.length === 1 ? 'y' : 'ies'}, ${d.entries.length} row(s) including replaced versions.`)),
        ratingsHelp(d.scales));
    }
    function entryCard(e) {
      return h('article', { class: 'sv-entry' + (e.current ? '' : ' sv-old') + (e.outside_authorised_period ? ' sv-outside' : '') },
        h('div', { class: 'sv-entry-top' }, h('b', null, `#${e.seq}`), h('span', null, e.observed_local), ratingChip(e.rating, d.scales),
          e.version > 1 ? chip(`version ${e.version}`, 'info') : null, e.current ? null : chip(`replaced by #${e.superseded_by_seq}`, 'warn'),
          e.outside_authorised_period ? chip('outside authorised period', 'bad') : null, e.subject_seen ? null : chip('subject not seen')),
        h('p', { class: 'sv-place' }, e.location_text, e.lat != null ? h('span', { class: 'muted small' }, `  (${e.lat.toFixed(4)}, ${e.lon.toFixed(4)})`) : (e.place_matched && e.place_matched.name ? h('span', { class: 'muted small' }, '  (approximate position from place name)') : null)),
        h('p', { class: 'sv-text' }, e.observation),
        (e.vehicles.length || e.persons.length || e.evidence_ids.length) ? h('div', { class: 'sv-chips' },
          e.vehicles.map(v => chip('vehicle ' + v.reg + (v.valid === false ? ' (format unusual)' : ''), 'info')), e.persons.map(p => chip(`${p.name} (${p.role})${p.entity_id ? ' linked' : ''}`)),
          e.evidence_ids.map(x => chip('evidence ' + x, 'good'))) : null,
        e.amendment_reason ? h('p', { class: 'small' }, h('b', null, 'Reason for amendment: '), e.amendment_reason) : null,
        h('div', { class: 'sv-entry-foot muted small' }, `Observer: ${e.observer || '–'}. Recorded by ${e.recorded_by} at ${e.recorded_at}.`, ' ',
          h('span', { class: 'mono', title: `hash ${e.entry_hash}\nprevious ${e.prev_hash}` }, 'hash ' + short(e.entry_hash)),
          canWrite && d.can_add && e.current && !d.masked ? h('button', { class: 'sm', onclick: () => amendDialog(e) }, 'Amend') : null));
    }

    // -------- entry form (shared by add and amend)
    function entryFields(e) {
      const f = {
        at: h('input', { type: 'datetime-local', value: nowLocal() }), tz: sel(ZONES, tz, () => {}, 'Time zone'), loc: h('input', { placeholder: 'Place description' }),
        lat: h('input', { type: 'number', step: 'any', placeholder: 'optional' }), lon: h('input', { type: 'number', step: 'any', placeholder: 'optional' }),
        observer: h('input', { placeholder: 'Name and rank' }), obs: h('textarea', { rows: 3, placeholder: 'What was seen, in plain factual words' }),
        seen: h('input', { type: 'checkbox', checked: true, id: 'sv-seen' }),
        veh: h('input', { placeholder: 'Registrations, comma separated (e.g. DL3CAB1234)' }), pers: h('textarea', { rows: 2, placeholder: 'One per line: Name | role (associate, contact, unknown, other) | case entity ID (optional)' }),
        ev: h('input', { placeholder: 'Evidence IDs from the vault, comma separated' }),
        src: sel(Object.keys(d.scales.source).map(k => [k, k]), 'F', () => {}, 'Source reliability'), inf: sel(Object.keys(d.scales.information).map(k => [k, k]), '6', () => {}, 'Information rating'),
      };
      if (e) {
        f.loc.value = e.location_text; f.observer.value = e.observer; f.obs.value = e.observation; f.seen.checked = e.subject_seen;
        if (e.lat != null) { f.lat.value = e.lat; f.lon.value = e.lon; }
        f.veh.value = e.vehicles.map(v => v.reg).join(', '); f.pers.value = e.persons.map(p => `${p.name} | ${p.role}${p.entity_id ? ' | ' + p.entity_id : ''}`).join('\n');
        f.ev.value = e.evidence_ids.join(', '); f.src.value = e.source_rating; f.inf.value = e.info_rating;
        f.at.value = (e.observed_local || '').length ? '' : nowLocal();
      }
      return f;
    }
    const fieldsBody = f => h('div', null,
      h('div', { class: 'grid g3' }, field('Date and time observed *', f.at), field('Time zone of that time', f.tz), h('label', { class: 'sv-check' }, f.seen, 'Subject was seen')),
      h('div', { class: 'grid g3' }, field('Location *', f.loc), field('Latitude', f.lat), field('Longitude', f.lon)),
      field('Observation *', f.obs), h('div', { class: 'grid g2' }, field('Observer', f.observer), field('Vehicles', f.veh)),
      field('Persons seen', f.pers), field('Evidence IDs', f.ev),
      h('div', { class: 'grid g2' }, field('Source reliability (A to F)', f.src), field('Information rating (1 to 6)', f.inf)),
      h('p', { class: 'muted small' }, 'Use F and 6 if you cannot yet judge. Hover over a rating in the log to see what it means.'));
    const collect = f => ({
      location_text: f.loc.value, lat: f.lat.value === '' ? null : Number(f.lat.value), lon: f.lon.value === '' ? null : Number(f.lon.value), observer: f.observer.value, observation: f.obs.value,
      subject_seen: f.seen.checked, vehicles: f.veh.value.split(',').map(s => s.trim()).filter(Boolean).map(reg => ({ reg, description: '' })),
      persons: f.pers.value.split('\n').map(l => l.trim()).filter(Boolean).map(l => { const [name, role, entity_id] = l.split('|').map(s => s.trim()); return { name, role: (role || 'unknown').toLowerCase(), entity_id: entity_id || '' }; }),
      evidence_ids: f.ev.value.split(',').map(s => s.trim()).filter(Boolean), source_rating: f.src.value, info_rating: f.inf.value });

    async function addTab() {
      const f = entryFields(null), err = h('div');
      mount(body, h('div', { class: 'card' }, h('h3', null, 'Add an observation'),
        oneLine('Once saved, an entry cannot be changed or removed. If you make a mistake, amend it and give the reason.'), err, fieldsBody(f),
        h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async () => {
          try {
            await api(`/surveillance/${encodeURIComponent(op)}/entries`, { method: 'POST', body: { ...collect(f), observed_at: localToIso(f.at.value), timezone: f.tz.value } });
            toast('Entry recorded and added to the chain.', 'good'); st.tab = 'log'; refresh();
          } catch (ex) { mount(err, errBox(ex)); }
        } }, 'Record entry'))));
    }
    function amendDialog(e) {
      const f = entryFields(e), reason = h('textarea', { rows: 2, placeholder: 'Why is this being corrected?' }), err = h('div');
      f.at.value = '';
      const m = modal(`Amend entry #${e.seq}`, h('div', null,
        notice('The original stays in the log. A new version is added that points back to it, with your reason. Leave the date empty to keep the original time.', 'warn'), err,
        field('Reason for amendment *', reason), fieldsBody(f),
        h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async () => {
          try {
            const c = collect(f);
            const b = { reason: reason.value, ...c, timezone: f.tz.value, clear_coordinates: c.lat == null && c.lon == null && e.lat != null };
            if (f.at.value) b.observed_at = localToIso(f.at.value);
            await api(`/surveillance/${encodeURIComponent(op)}/entries/${encodeURIComponent(e.uid)}/amend`, { method: 'POST', body: b });
            m.close(); toast('Amendment recorded.', 'good'); refresh();
          } catch (ex) { mount(err, errBox(ex)); }
        } }, 'Record amendment'))), { wide: true });
    }

    // -------- movement
    async function moveTab() {
      const win = h('input', { type: 'number', min: 1, max: 720, value: 30, 'aria-label': 'Meeting window in minutes' }), rad = h('input', { type: 'number', min: 20, max: 5000, value: 300, 'aria-label': 'Same-place radius in metres' });
      const out = h('div');
      async function run() {
        mount(out, empty('Analysing...'));
        const r = await api(`/surveillance/${encodeURIComponent(op)}/movement`, { params: { tz: st.tz, window_minutes: win.value, radius_m: rad.value } });
        mount(out, movementView(r));
      }
      mount(body, h('div', { class: 'card' }, oneLine('Where the subject was seen, how long they stayed, places that recur, and who was with them. Built only from entries that have a position.'),
        h('div', { class: 'row' }, field('Count as together within (minutes)', win), field('Count as same place within (metres)', rad), h('button', { onclick: () => run().catch(ex => mount(out, errBox(ex))) }, 'Update'))), out);
      await run();
    }
    function movementView(r) {
      const list = h('div');
      const box = h('div', { class: 'sv-map', role: 'img', 'aria-label': 'Map of observed positions' });
      const sentences = r.pattern_of_life.length ? h('ul', { class: 'sv-sent' }, r.pattern_of_life.map(s => h('li', null, s))) : empty('Not enough located entries to describe a pattern yet.');
      const wrap = h('div', { class: 'sv-move' }, h('div', { class: 'grid g4' }, kpi('Located points', fmtNum(r.points.length)), kpi('Places', fmtNum(r.dwell_locations.length)), kpi('Meetings', fmtNum(r.meetings.length)), kpi('Vehicles', fmtNum(r.vehicles.length))),
        h('div', { class: 'card' }, h('h3', null, 'Route'), oneLine('Points are joined in time order. Dashed markers are approximate (taken from a place name). A straight line is not the route actually taken.'), box,
          r.unplaced_entries.length ? h('p', { class: 'muted small' }, `${r.unplaced_entries.length} entr${r.unplaced_entries.length === 1 ? 'y has' : 'ies have'} no position and are not on the map.`) : null),
        h('div', { class: 'card' }, h('h3', null, 'What the log shows'), sentences, notice(r.caveats.join(' '), 'warn')), list);
      mount(list, h('div', { class: 'card' }, h('h3', null, 'Places and time spent'), oneLine('Dwell is the time between the first and last sighting at a place, so it is a minimum.'),
        table([{ label: 'Place', render: p => p.label }, { label: 'Visits', num: true, render: p => p.visits }, { label: 'Days', num: true, render: p => p.distinct_days },
          { label: 'Minutes seen', num: true, render: p => fmtNum(p.observed_minutes) }, { label: 'Usual hours', render: p => p.typical_hour_window }, { label: 'Days of week', render: p => (p.weekdays || []).join(', ') }], r.dwell_locations, { emptyText: 'No located places.' })),
        h('div', { class: 'card' }, h('h3', null, 'Meetings'), oneLine('Two people recorded at the same place within the time window. It shows co-presence, not that they knew each other.'),
          table([{ label: 'People', render: m => m.persons.join(' and ') }, { label: 'Place', render: m => m.place }, { label: 'First', render: m => m.first.replace('T', ' ') }, { label: 'Last', render: m => m.last.replace('T', ' ') }, { label: 'Entries', render: m => m.entries.map(x => '#' + x).join(', ') }], r.meetings, { emptyText: 'No meetings found.' })),
        h('div', { class: 'card' }, h('h3', null, 'Vehicles'),
          table([{ label: 'Registration', render: v => h('span', { class: 'mono' }, v.reg) }, { label: 'Format', render: v => (v.valid_format ? 'valid' : chip('unusual', 'warn')) }, { label: 'Seen', num: true, render: v => v.seen },
            { label: 'Places', render: v => v.places.map(p => `${p.place} x${p.count}`).join(', ') }, { label: 'With', render: v => v.seen_with.map(p => `${p.person} x${p.count}`).join(', ') }], r.vehicles, { emptyText: 'No vehicles recorded.' })),
        h('div', { class: 'card' }, h('h3', null, 'Movement between sightings'), oneLine('Speeds are straight-line and only a sanity check. Implausible ones point to a wrong time or place in an entry.'),
          table([{ label: 'From', render: l => l.from }, { label: 'To', render: l => l.to }, { label: 'km', num: true, render: l => fmtNum(l.km, 1) }, { label: 'min', num: true, render: l => fmtNum(l.minutes) },
            { label: 'km/h', num: true, render: l => (l.implausible ? chip(fmtNum(l.kmh, 0) + ' implausible', 'bad') : fmtNum(l.kmh, 0)) }], r.legs, { emptyText: 'Fewer than two located sightings.' })),
        h('details', { class: 'sv-explain' }, h('summary', null, 'How this is worked out'), h('p', { class: 'muted' }, r.explain)));
      requestAnimationFrame(() => drawMap(box, r));
      return wrap;
    }
    function drawMap(box, r) {
      if (!box.isConnected) return;
      const m = typeof L !== 'undefined' ? createEventMap(box, {}) : null;
      if (!m) { mount(box, fallbackRoute(r.points)); return; }
      const ev = r.points.map(p => ({ map_lat: p.lat, map_lon: p.lon, category: 'Location', label: `#${p.seq} ${p.label}`, event_type: 'Sighting', event_time: p.t, geo_precision: p.precision === 'recorded' ? 'exact' : 'approximate', geo_place: p.label }));
      m.update(ev, { fit: true });
      if (r.points.length > 1) {
        const line = L.polyline(r.points.map(p => [p.lat, p.lon]), { color: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#4f8bf7', weight: 2, opacity: 0.7, dashArray: '6 4', interactive: false }).addTo(m.map);
        m.map.on('zoomend', () => line.bringToBack());
      }
      requestAnimationFrame(() => m.invalidate());
    }
    function fallbackRoute(points) {
      if (!points.length) return empty('No located entries.');
      const lats = points.map(p => p.lat), lons = points.map(p => p.lon), w = 600, ht = 320, pad = 30;
      const minA = Math.min(...lats), maxA = Math.max(...lats), minO = Math.min(...lons), maxO = Math.max(...lons);
      const x = lo => pad + (maxO === minO ? 0.5 : (lo - minO) / (maxO - minO)) * (w - 2 * pad), y = la => ht - pad - (maxA === minA ? 0.5 : (la - minA) / (maxA - minA)) * (ht - 2 * pad);
      const s = svg('svg', { viewBox: `0 0 ${w} ${ht}`, class: 'sv-route', role: 'img', 'aria-label': 'Route of sightings (map library unavailable)' });
      s.append(svg('polyline', { points: points.map(p => `${x(p.lon)},${y(p.lat)}`).join(' '), class: 'sv-routeline' }));
      points.forEach(p => s.append(svg('circle', { cx: x(p.lon), cy: y(p.lat), r: 5, class: 'sv-pt' }, svg('title', null, `#${p.seq} ${p.label}`))));
      return s;
    }

    // -------- reports
    async function reportTab() {
      const one = (fmt, label, what) => h('div', { class: 'sv-rep' }, h('b', null, label), h('p', { class: 'muted small' }, what),
        h('button', { onclick: () => getReport(fmt) }, 'Download ' + label));
      mount(body, h('div', { class: 'card' }, h('h3', null, 'Generate a report'),
        oneLine('The report lists the authority, the full chronological log with amendments, subjects, vehicles, places, evidence references, limitations and a sign-off block. Its footer carries the chain head hash, entry count and the report hash.'),
        h('div', { class: 'grid g4' }, one('pdf', 'PDF', 'For printing and signing.'), one('docx', 'Word', 'To edit before filing.'), one('html', 'HTML', 'For viewing in a browser.'), one('json', 'JSON', 'Machine-readable, with all hashes.')),
        h('p', { class: 'muted small' }, 'Every report you generate is written to the audit log. Times are shown in ', st.tz, '.')));
    }
    async function getReport(fmt) {
      try {
        const res = await api(`/surveillance/${encodeURIComponent(op)}/report`, { params: { format: fmt, tz: st.tz }, raw: true });
        const blob = await res.blob(), sha = res.headers.get('X-Report-File-SHA256');
        download(blob, `${op}.${fmt}`);
        toast(`Report downloaded${sha ? '. File SHA-256 ' + sha.slice(0, 16) + '...' : ''}`, 'good');
      } catch (ex) { toast(ex.message, 'bad'); }
    }

    // -------- operation details and closing
    async function infoTab() {
      const closeBox = canApprove && o.status !== 'closed' ? h('div', { class: 'card' }, h('h3', null, 'Close this operation'),
        oneLine('Closing seals the log: no further entries or amendments are accepted. Only a supervisor can do this, and it cannot be undone.'),
        h('button', { class: 'danger', onclick: closeDialog }, 'Close operation')) : null;
      mount(body, h('div', { class: 'card' }, h('dl', { class: 'kv' },
        h('dt', null, 'Objective'), h('dd', null, o.objective), h('dt', null, 'Authority'), h('dd', null, o.authority_text), h('dt', null, 'Authority reference'), h('dd', null, o.authority_ref),
        h('dt', null, 'Period'), h('dd', null, `${o.start_at || '–'} to ${o.end_at || 'open'}`), h('dt', null, 'Supervising officer'), h('dd', null, o.supervising_officer),
        h('dt', null, 'Team'), h('dd', null, (o.team || []).join(', ') || '–'), h('dt', null, 'Created'), h('dd', null, `${o.created_by}, ${o.created_at}`),
        h('dt', null, 'Chain head hash'), h('dd', { class: 'mono sv-hash' }, o.head_hash || '–'), h('dt', null, 'Header hash'), h('dd', { class: 'mono sv-hash' }, o.header_hash),
        o.closure_hash ? [h('dt', null, 'Closure'), h('dd', null, `${o.closed_by}: ${o.closing_remarks}`), h('dt', null, 'Closure hash'), h('dd', { class: 'mono sv-hash' }, o.closure_hash)] : null)),
        closeBox, notice(d.notice));
    }
    function closeDialog() {
      const rem = h('textarea', { rows: 3, placeholder: 'Reason for closing and a short outcome note' }), err = h('div');
      const m = modal('Close operation ' + op, h('div', null, err, field('Closing remarks *', rem),
        h('div', { class: 'row' }, h('button', { class: 'danger', onclick: async () => {
          try { await api(`/surveillance/${encodeURIComponent(op)}/close`, { method: 'POST', body: { remarks: rem.value } }); m.close(); toast('Operation closed.', 'good'); st.tab = 'info'; refresh(); } catch (ex) { mount(err, errBox(ex)); }
        } }, 'Close operation'))));
    }
    draw();
  }

  showList();
}
