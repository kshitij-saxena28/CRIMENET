// Social media intelligence, investigator tools: priority queue, watch list, look-alike accounts, how a hashtag or link spread, map of places,
// account profile sheet, English view of a post, evidence preservation sheet and analyst notes.
// Wording rule (same as f_social.js): scores order a reading list. They never describe a person's intent, mood or guilt.
import { h, api, mount, notice, empty, chip, toast, table, field, modal, fmtNum, fmtTime, download, columnChart, svg } from './lib.js';
import { createEventMap } from './mapview.js';

const errBox = ex => notice(ex.message || String(ex), 'bad');
const LEVEL_KIND = { high: 'bad', medium: 'warn', low: '' };
const STRENGTH_KIND = { strong: 'bad', high: 'bad', medium: 'warn', weak: '', low: '' };
const KIND_LABEL = { handle: 'Account handle', hashtag: 'Hashtag', keyword: 'Word or phrase', phone: 'Phone number', email: 'E-mail', upi: 'UPI ID', url: 'Link', place: 'Place' };
const KIND_HINT = { handle: 'e.g. quickcash_deals', hashtag: 'e.g. DoubleMoney', keyword: 'e.g. guaranteed returns', phone: 'e.g. +91 98765 43210', email: 'e.g. name@example.com',
  upi: 'e.g. name@okaxis', url: 'e.g. bit.ly/abc123', place: 'e.g. Noida' };
const oneLine = t => h('p', { class: 'muted so-line' }, t);
const explain = t => h('details', { class: 'so-explain' }, h('summary', null, 'What am I looking at?'), h('p', { class: 'muted' }, t));
const scoreBar = (score, level) => h('span', { class: 'si-score si-' + level, title: `${score} of 100` }, h('i', { style: `width:${Math.max(4, Math.min(100, score))}%` }), h('b', null, String(Math.round(score))));

export function intelTools(env) {
  const { cn, ctx, openAccount, gotoTab } = env;
  const canWrite = ctx.can('write'), canNote = ctx.can('note'), canReport = ctx.can('report');

  // -------------------------------------------------------------------------------------------- post helpers (used under every post)
  async function englishView(p, box, btn) {
    btn.disabled = true;
    try {
      const r = await api(`/social/${cn}/intel/translate`, { method: 'POST', body: { post_uid: p.uid } });
      mount(box, h('div', { class: 'si-eng' }, h('b', null, r.translated ? 'English view (machine translation)' : 'English'), h('p', { class: 'so-text' }, r.text),
        h('p', { class: 'muted small' }, [r.note, r.warning].filter(Boolean).join(' '))));
    } catch (ex) { mount(box, errBox(ex)); btn.disabled = false; }
  }
  async function sheetFor(uids, purpose, format) {
    const res = await api(`/social/${cn}/intel/sheet`, { method: 'POST', body: { post_uids: uids, format, purpose: purpose || '' }, raw: true });
    const blob = await res.blob();
    download(blob, `evidence_sheet_${ctx.caseNumber}.${format === 'json' ? 'json' : 'html'}`);
  }
  function sheetDialog(uids) {
    const purpose = h('input', { maxlength: 300, placeholder: 'e.g. chargesheet annexure, notice to intermediary', 'aria-label': 'Purpose' });
    const fmt = h('select', { 'aria-label': 'Format' }, h('option', { value: 'html' }, 'Printable page (HTML)'), h('option', { value: 'json' }, 'Data file (JSON)'));
    const m = modal('Evidence preservation sheet', h('div', null,
      h('p', null, `The sheet covers ${uids.length} post(s): the text, its SHA-256 fingerprint (re-checked now against the value taken at import), where it came from, who imported it and the legal basis recorded.`),
      h('p', { class: 'muted small' }, 'It is watermarked with your name and the time, and each issue is recorded in the tamper-evident ledger. It shows how the material entered the case; it does not prove the post is genuine.'),
      field('Purpose (optional)', purpose), field('Format', fmt),
      h('div', { class: 'row', style: 'margin-top:.7rem' }, h('button', { class: 'primary', onclick: async e => {
        e.target.disabled = true;
        try { await sheetFor(uids, purpose.value.trim(), fmt.value); toast('Sheet created and recorded in the ledger', 'good'); m.close(); } catch (ex) { toast(ex.message, 'bad'); e.target.disabled = false; }
      } }, 'Create sheet'), h('button', { onclick: () => m.close() }, 'Cancel'))));
  }
  function noteDialog(type, ref, label, done) {
    const body = h('textarea', { rows: 4, maxlength: 1000, placeholder: 'What should the next reader know? (not shown outside this case)', 'aria-label': 'Note' });
    const pin = h('input', { type: 'checkbox' });
    const m = modal('Add a note to ' + label, h('div', null, body, h('label', { class: 'so-check small' }, pin, 'Pin to the top'),
      h('div', { class: 'row', style: 'margin-top:.7rem' }, h('button', { class: 'primary', onclick: async () => {
        if (body.value.trim().length < 2) return toast('Write a short note first', 'bad');
        try { await api(`/social/${cn}/intel/notes`, { method: 'POST', body: { item_type: type, item_ref: String(ref), body: body.value.trim(), pinned: pin.checked } }); toast('Note saved', 'good'); m.close(); if (done) done(); }
        catch (ex) { toast(ex.message, 'bad'); }
      } }, 'Save note'), h('button', { onclick: () => m.close() }, 'Cancel'))));
  }
  function postTools(p) {
    const box = h('div');
    const eng = h('button', { class: 'sm', title: 'Show an English rendering next to the original' }, 'English view');
    eng.onclick = () => englishView(p, box, eng);
    return h('div', null, h('div', { class: 'row so-actions' }, ctx.can('analyze') ? eng : null,
      canReport ? h('button', { class: 'sm', title: 'Printable preservation sheet with fingerprint and source', onclick: () => sheetDialog([p.uid]) }, 'Evidence sheet') : null,
      canNote ? h('button', { class: 'sm', onclick: () => noteDialog('post', p.uid, 'this post') }, 'Add note') : null), box);
  }

  // -------------------------------------------------------------------------------------------- priority queue
  async function priority(host) {
    const r = await api(`/social/${cn}/intel/triage`);
    const hits = r.posts;
    mount(host, oneLine('Where to start reading. Accounts and posts are ordered by a short list of visible rules; every line shows the points it added.'),
      h('p', { class: 'so-headline' }, r.headline), explain(r.explain),
      r.accounts.length ? h('div', { class: 'grid g2 si-queue' },
        h('section', null, h('h3', null, 'Accounts to read first'), r.accounts.map(a => h('article', { class: 'card si-item si-l-' + a.level },
          h('div', { class: 'si-top' }, h('button', { class: 'link', onclick: () => openAccount(a.account_id) }, a.account), a.display_name ? h('span', { class: 'muted small' }, a.display_name) : null,
            h('span', { class: 'si-spacer' }), chip(a.level, LEVEL_KIND[a.level]), scoreBar(a.score, a.level)),
          h('ul', { class: 'si-why' }, a.reasons.map(x => h('li', null, x))),
          h('div', { class: 'row so-actions' }, h('button', { class: 'sm', onclick: () => openAccount(a.account_id) }, 'Open profile sheet'),
            h('span', { class: 'muted small' }, Object.entries(a.breakdown).map(([k, v]) => `${k} +${v}`).join(' · ')))))),
        h('section', null, h('h3', null, 'Posts to read first'), hits.length ? hits.map(p => h('article', { class: 'card si-item si-l-' + p.level },
          h('div', { class: 'si-top' }, h('b', null, p.account), h('span', { class: 'muted small' }, p.posted_at ? fmtTime(p.posted_at) : 'no timestamp'), h('span', { class: 'si-spacer' }), scoreBar(p.score, p.level)),
          h('p', { class: 'so-text' }, p.text || ''), h('ul', { class: 'si-why' }, p.reasons.map(x => h('li', null, x))),
          h('div', { class: 'row so-actions' }, canReport ? h('button', { class: 'sm', onclick: () => sheetDialog([p.post_uid]) }, 'Evidence sheet') : null,
            canNote ? h('button', { class: 'sm', onclick: () => noteDialog('post', p.post_uid, 'this post') }, 'Add note') : null))) : empty('No individual post stands out.')))
        : empty('Nothing needs priority attention yet. Import material, or add watch-list terms.'),
      r.accounts.length ? h('details', { class: 'so-explain' }, h('summary', null, 'The rules and their weights'),
        h('table', { class: 'si-weights' }, h('tbody', null, Object.entries(r.weights).map(([k, v]) => h('tr', null, h('td', null, k.replace(/_/g, ' ')), h('td', { class: 'num' }, String(v))))))) : null);
  }

  // -------------------------------------------------------------------------------------------- watch list
  async function watch(host) {
    const [wl, hits] = await Promise.all([api(`/social/${cn}/intel/watch`), ctx.can('analyze') ? api(`/social/${cn}/intel/watch/hits`) : Promise.resolve(null)]);
    const kind = h('select', { 'aria-label': 'Kind of term', onchange: () => { val.placeholder = KIND_HINT[kind.value]; } }, wl.kinds.map(k => h('option', { value: k }, KIND_LABEL[k] || k)));
    const val = h('input', { placeholder: KIND_HINT.handle, maxlength: 200, 'aria-label': 'Value to watch' });
    const note = h('input', { placeholder: 'Why (optional)', maxlength: 300, 'aria-label': 'Reason' });
    const prio = h('select', { 'aria-label': 'Priority' }, h('option', { value: 'normal' }, 'Normal'), h('option', { value: 'high' }, 'High priority'));
    const form = canWrite ? h('form', { class: 'row si-form', onsubmit: async e => {
      e.preventDefault();
      try { const r = await api(`/social/${cn}/intel/watch`, { method: 'POST', body: { kind: kind.value, value: val.value, note: note.value, priority: prio.value } }); toast(r.message, 'good'); watch(host).catch(x => mount(host, errBox(x))); }
      catch (ex) { toast(ex.message, 'bad'); }
    } }, field('Kind', kind), field('Value', val), field('Why', note), field('Priority', prio), h('button', { class: 'primary', type: 'submit' }, 'Add to watch list')) : notice('Your role can see the watch list but not change it.', 'info');
    const by = hits ? hits.by_term : {};
    mount(host, oneLine('Things this case is looking for. New terms are checked against everything already imported, and every new import is checked when you open this page.'),
      explain(wl.explain), form,
      h('section', { class: 'card' }, h('h3', null, `Watch list (${wl.terms.length})`), wl.terms.length ? table([
        { label: 'Kind', render: t => KIND_LABEL[t.kind] || t.kind }, { label: 'Value', render: t => h('span', { class: 'mono' }, t.value) },
        { label: 'Priority', render: t => chip(t.priority, t.priority === 'high' ? 'bad' : '') }, { label: 'Why', render: t => t.note || '–' },
        { label: 'Matches', num: true, render: t => by[t.id] || 0 }, { label: 'Added by', render: t => t.created_by },
        { label: '', render: t => canWrite ? h('button', { class: 'sm', onclick: async () => {
          try { await api(`/social/${cn}/intel/watch/${t.id}`, { method: 'DELETE' }); toast('Removed', 'good'); watch(host).catch(x => mount(host, errBox(x))); } catch (ex) { toast(ex.message, 'bad'); }
        } }, 'Remove') : null }], wl.terms) : empty('The watch list is empty. Add an account, hashtag, phone number or phrase to be told when it appears in imported material.')),
      hits ? h('section', { class: 'card' }, h('h3', null, 'Matches'), h('p', { class: 'so-headline' }, hits.headline),
        hits.hits.length ? table([{ label: 'Term', render: x => h('span', null, chip(x.priority, x.priority === 'high' ? 'bad' : ''), ' ', h('span', { class: 'mono' }, x.term)) },
          { label: 'Account', render: x => x.account }, { label: 'Where', render: x => (x.where === 'profile' ? 'Profile' : 'Post') },
          { label: 'Context', render: x => h('span', { class: 'small so-snippet' }, x.snippet) }], hits.hits.slice(0, 200)) : empty('No matches yet.'), explain(hits.explain)) : null);
  }

  // -------------------------------------------------------------------------------------------- look-alikes
  async function lookalikes(host) {
    const r = await api(`/social/${cn}/intel/lookalikes`);
    mount(host, h('p', { class: 'so-headline' }, r.headline), explain(r.explain),
      r.pairs.length ? r.pairs.map(p => h('article', { class: 'card si-item' },
        h('div', { class: 'si-top' }, chip(p.strength, STRENGTH_KIND[p.strength]), h('b', null, p.a), h('span', { class: 'muted' }, 'and'), h('b', null, p.b), h('span', { class: 'si-spacer' }), h('span', { class: 'muted small' }, `similarity ${Math.round(p.score * 100)}%`)),
        h('ul', { class: 'si-why' }, p.reasons.map(x => h('li', null, x))), h('p', { class: 'muted small' }, p.caveat))) : empty('No look-alike accounts.'));
  }

  // -------------------------------------------------------------------------------------------- narratives
  function spark(daily) {
    if (!daily.length) return null;
    return columnChart(daily.map(d => ({ x: d.date, y: d.count })), { w: 320, hgt: 70 });
  }
  async function narratives(host) {
    const r = await api(`/social/${cn}/intel/narratives`);
    const card = n => h('article', { class: 'card si-item' + (n.burst ? ' si-l-high' : '') },
      h('div', { class: 'si-top' }, h('b', null, n.kind === 'hashtag' ? '#' + n.key : n.key), n.burst ? chip('sudden burst', 'warn') : null, chip(`${n.accounts} accounts`), chip(`${n.posts} posts`),
        n.engagement ? chip(`${fmtNum(n.engagement)} likes+shares`) : null),
      h('p', { class: 'small' }, n.reason), spark(n.daily),
      h('p', { class: 'small muted' }, 'Pushed most by ', n.top_amplifiers.map(a => `${a.account} (${a.posts})`).join(', '), n.first_seen ? ` · first imported use ${fmtTime(n.first_seen)}` : ''));
    mount(host, h('p', { class: 'so-headline' }, r.headline), explain(r.explain),
      r.hashtags.length ? h('div', null, h('h4', null, 'Hashtags'), r.hashtags.map(card)) : null,
      r.links.length ? h('div', null, h('h4', null, 'Links'), r.links.map(card)) : null,
      !r.hashtags.length && !r.links.length ? empty('Nothing is repeated often enough yet.') : null);
  }

  // -------------------------------------------------------------------------------------------- places
  async function places(host) {
    const r = await api(`/social/${cn}/intel/places`);
    const box = h('div', { class: 'si-map', role: 'region', 'aria-label': 'Map of places named in the material' });
    mount(host, h('p', { class: 'so-headline' }, r.headline), explain(r.explain), r.places.length ? box : empty('No known place is named in the imported material.'),
      r.places.length ? table([{ label: 'Place', render: p => `${p.name}${p.state ? ', ' + p.state : ''}` }, { label: 'In posts', num: true, render: p => p.posts },
        { label: 'In profiles', num: true, render: p => p.profiles }, { label: 'Accounts', render: p => p.accounts.slice(0, 4).join(', ') + (p.accounts.length > 4 ? ` +${p.accounts.length - 4}` : '') },
        { label: 'First', render: p => p.first ? fmtTime(p.first) : '–' }], r.places) : null);
    if (r.places.length) requestAnimationFrame(() => {
      if (!box.isConnected) return;
      const m = typeof L !== 'undefined' ? createEventMap(box, {}) : null;
      if (!m) { box.replaceWith(notice('The map library could not be loaded; the table below has the same places.', 'info')); return; }
      m.update(r.places.map(p => ({ map_lat: p.lat, map_lon: p.lon, category: 'Location', label: `${p.name} (${p.posts + p.profiles})`, event_type: 'Named place', geo_precision: 'approximate', geo_place: p.name })), { fit: true });
      requestAnimationFrame(() => m.invalidate());
    });
  }

  // -------------------------------------------------------------------------------------------- profile sheet
  async function dossier(id) {
    const d = await api(`/social/${cn}/intel/account/${id}`);
    const a = d.account;
    const heat = (d.activity.hours || []);
    const maxH = Math.max(1, ...heat);
    const hoursSvg = heat.length ? (() => { const s = svg('svg', { viewBox: '0 0 240 40', class: 'si-hours', role: 'img', 'aria-label': 'Posts by hour of day' });
      heat.forEach((v, i) => s.append(svg('rect', { x: i * 10, y: 36 - (v / maxH) * 34, width: 8, height: Math.max(1, (v / maxH) * 34), rx: 1, class: 'so-cell' }, svg('title', null, `${String(i).padStart(2, '0')}:00, ${v} post(s)`)))); return s; })() : null;
    const m = modal(`${a.ref || a.handle} · profile sheet`, h('div', { class: 'si-dossier' },
      d.priority ? h('div', { class: 'si-top' }, chip('priority ' + d.priority.level, LEVEL_KIND[d.priority.level]), scoreBar(d.priority.score, d.priority.level), h('span', { class: 'muted small' }, d.priority.reasons.slice(0, 2).join(' · '))) : null,
      h('dl', { class: 'kv' }, h('dt', null, 'Display name'), h('dd', null, a.display_name || '–'), h('dt', null, 'Bio'), h('dd', null, a.bio || '–'),
        h('dt', null, 'Followers / following'), h('dd', null, `${fmtNum(a.followers)} / ${fmtNum(a.following)}`), h('dt', null, 'First / last post'), h('dd', null, `${d.first_post ? fmtTime(d.first_post) : '–'} / ${d.last_post ? fmtTime(d.last_post) : '–'}`),
        h('dt', null, 'Posts imported'), h('dd', null, String(d.posts)), h('dt', null, 'Posting rhythm'),
        h('dd', null, d.cadence.median_gap_hours == null ? '–' : `typically every ${d.cadence.median_gap_hours} h; longest quiet spell ${d.cadence.longest_silence_hours} h; ${d.cadence.posts_per_active_day} posts per active day`),
        h('dt', null, 'Identifiers'), h('dd', null, d.identifiers.length ? h('div', { class: 'so-chips' }, d.identifiers.map(i => chip(`${i.kind} ${i.value}`, 'info'))) : 'none found'),
        h('dt', null, 'Engagement'), h('dd', null, `${fmtNum(d.engagement.total)} likes and shares in total, ${d.engagement.average_per_post} per post`)),
      d.activity.headline ? h('p', { class: 'small' }, d.activity.headline) : null, hoursSvg,
      h('div', { class: 'grid g2' },
        h('div', null, h('h4', null, 'Uses most'), h('div', { class: 'so-chips' }, [...d.top_hashtags.map(x => chip(`#${x.tag} ${x.count}`)), ...d.top_mentions.map(x => chip(`@${x.handle} ${x.count}`))].concat(!d.top_hashtags.length && !d.top_mentions.length ? [chip('nothing repeated')] : []))),
        h('div', null, h('h4', null, 'Wording flags'), h('p', { class: 'small' }, `${d.flags.total} raised: ${d.flags.unreviewed} unreviewed, ${d.flags.relevant} relevant, ${d.flags.false_positive} false positive.`),
          h('h4', null, 'Watch-list matches'), d.watch_hits.length ? h('ul', { class: 'si-why' }, d.watch_hits.slice(0, 5).map(x => h('li', null, `${x.term} (${x.kind}) in ${x.where}`))) : h('p', { class: 'small muted' }, 'None.'))),
      d.interactions.length ? [h('h4', null, 'Interacts with'), table([{ label: 'From', render: e => e.source }, { label: 'To', render: e => e.target }, { label: 'Times', num: true, render: e => e.weight },
        { label: 'How', render: e => Object.entries(e.kinds).map(([k, v]) => `${k} ${v}`).join(', ') }], d.interactions)] : null,
      d.lookalikes.length ? [h('h4', null, 'Look-alike accounts'), d.lookalikes.map(p => h('p', { class: 'small' }, chip(p.strength, STRENGTH_KIND[p.strength]), ' ', `${p.a} and ${p.b}: ${p.reasons[0]}`))] : null,
      d.attribution.length ? [h('h4', null, 'Shares something with other accounts'), d.attribution.map(x => h('p', { class: 'small' }, chip(x.strength, STRENGTH_KIND[x.strength]), ' ', x.reason, ' ', h('span', { class: 'muted' }, x.accounts.join(', '))))] : null,
      d.linked_entities.length ? [h('h4', null, 'Linked to case entities (unverified until reviewed)'), d.linked_entities.map(l => h('p', { class: 'small' }, `${l.entity_id} via ${l.match_type}, accepted by ${l.accepted_by}`))] : null,
      h('h4', null, 'Analyst notes'), d.notes.length ? d.notes.map(n => h('div', { class: 'si-note' }, n.pinned ? chip('pinned', 'info') : null, h('p', null, n.body), h('span', { class: 'muted small' }, `${n.created_by}, ${fmtTime(n.created_at)}`),
        canNote ? h('button', { class: 'sm', onclick: async () => { try { await api(`/social/${cn}/intel/notes/${n.id}`, { method: 'DELETE' }); m.close(); dossier(id); } catch (ex) { toast(ex.message, 'bad'); } } }, 'Delete') : null)) : h('p', { class: 'small muted' }, 'No notes yet.'),
      h('div', { class: 'row so-actions' }, canNote ? h('button', { onclick: () => noteDialog('account', id, a.ref || a.handle, () => { m.close(); dossier(id); }) }, 'Add note') : null, h('button', { onclick: () => m.close() }, 'Close')),
      h('p', { class: 'muted small' }, d.caveat)), { wide: true });
  }


  // -------------------------------------------------------------------------------------------- monitor one account (official APIs / public feeds)
  const EVERY = [[0, 'Check once, now'], [15, 'Keep monitoring: every 15 minutes'], [60, 'Keep monitoring: every hour'], [360, 'Keep monitoring: every 6 hours'], [1440, 'Keep monitoring: every day']];
  async function collect(body) {
    const [pv, jb, ac] = await Promise.all([api('/social/collect/providers'), api(`/social/${cn}/collect/jobs`), api(`/social/${cn}/accounts`, { params: { limit: 500 } }).catch(() => ({ accounts: [] }))]);
    const provs = pv.providers.filter(p => p.kinds.includes('user')), byId = Object.fromEntries(provs.map(p => [p.id, p]));
    const out = h('div'), jobsBox = h('div');
    const prov = h('select', { 'aria-label': 'Where to look' }, provs.map(p => h('option', { value: p.id, disabled: !p.configured }, p.label + (p.configured ? '' : ' (not set up)'))));
    const seen = [...new Set((ac.accounts || []).map(a => a.handle))];
    const query = h('input', { list: 'so-handles', maxlength: 300, placeholder: 'Account handle, e.g. quickcash_deals', 'aria-label': 'Account to monitor' });
    const dl = h('datalist', { id: 'so-handles' }, seen.map(x => h('option', { value: x })));
    const max = h('input', { type: 'number', min: 5, max: 100, value: 50, style: 'width:90px', 'aria-label': 'Most posts per check' });
    const every = h('select', { 'aria-label': 'How often' }, EVERY.filter(([m]) => !m || m >= pv.min_minutes).map(([v, l]) => h('option', { value: v }, l)));
    const basis = h('textarea', { rows: 2, maxlength: 1000, placeholder: 'Required. Who authorised this and under what provision, e.g. "Public-source enquiry under order ref 12/2026".' });
    try { basis.value = localStorage.getItem('dcn-collect-basis') || ''; } catch { /* ignore */ }
    const help = h('p', { class: 'muted small' });
    const syncHelp = () => { const p = byId[prov.value] || provs[0]; help.textContent = p ? p.help + (p.configured ? '' : ' Not set up: the administrator must set ' + p.missing.join(', ') + '.') : ''; };
    prov.addEventListener('change', syncHelp);
    const firstOk = provs.find(p => p.configured && !p.synthetic) || provs.find(p => p.configured); if (firstOk) prov.value = firstOk.id; syncHelp();
    const result = r => notice(r.fetched ? `Checked: ${r.fetched} post(s) found, ${r.posts_added} new, ${r.duplicates_skipped} already held, ${r.flags_raised} wording flag(s) raised.` : 'No posts found for that account.', r.posts_added ? 'good' : 'info');
    const go = h('button', { class: 'primary', onclick: async e => {
      const b = { provider: prov.value, kind: 'user', query: query.value.trim(), max_posts: +max.value || 50, every_minutes: +every.value, legal_basis: basis.value.trim(), run_now: true };
      if (b.query.length < 2) return toast('Enter the account to monitor', 'bad');
      if (b.legal_basis.split(/\s+/).length < 3) return toast('State the legal basis or authority (a short sentence)', 'bad');
      try { localStorage.setItem('dcn-collect-basis', b.legal_basis); } catch { /* ignore */ }
      e.target.disabled = true; mount(out, empty('Checking…'));
      try { const r = await api(`/social/${cn}/collect/jobs`, { method: 'POST', body: b }); mount(out, r.result ? result(r.result) : notice('Saved. It has not run yet.', 'info')); await loadJobs(); }
      catch (ex) { mount(out, errBox(ex)); }
      e.target.disabled = false;
    } }, 'Check now / start monitoring');

    async function loadJobs() {
      const r = await api(`/social/${cn}/collect/jobs`);
      const act = (label, fn) => h('button', { class: 'sm', onclick: async ev => { ev.target.disabled = true; try { await fn(); } catch (ex) { toast(ex.message, 'bad'); } await loadJobs(); } }, label);
      mount(jobsBox, h('h3', null, 'Monitored accounts'), oneLine('Each one is checked on its schedule using the authority recorded when it was set up. New posts land in this case like any import. Pause or stop monitoring at any time.'),
        r.jobs.length ? table([
          { label: 'Account', render: j => h('span', null, h('b', null, j.query), ' ', h('span', { class: 'muted small' }, j.provider_label)) },
          { label: 'Checked', render: j => j.every_minutes ? (EVERY.find(x => x[0] === j.every_minutes)?.[1] || `every ${j.every_minutes} min`).replace('Keep monitoring: ', '') : 'Only when run' },
          { label: 'Last check', render: j => j.last_run_at ? fmtTime(j.last_run_at) : 'Never' },
          { label: 'Result', render: j => h('span', { class: j.last_status.startsWith('Failed') ? 'bad' : 'muted small' }, j.last_status + (j.active ? '' : ' (paused)')) },
          { label: 'New posts', num: true, render: j => fmtNum(j.total_added) },
          { label: '', render: j => canWrite ? h('span', { class: 'row', style: 'gap:.3rem' }, act('Check now', async () => { const x = await api(`/social/${cn}/collect/jobs/${j.id}/run`, { method: 'POST' }); toast(`${x.result.posts_added} new post(s)`, 'good'); }),
            act(j.active ? 'Pause' : 'Resume', () => api(`/social/${cn}/collect/jobs/${j.id}/toggle`, { method: 'POST' })),
            act('Stop monitoring', () => api(`/social/${cn}/collect/jobs/${j.id}`, { method: 'DELETE' }))) : null }], r.jobs) : empty('No accounts are being monitored yet. Enter one above and choose how often to check.'));
    }

    mount(body, oneLine('Follow one account: the server fetches its posts through the platform\'s official interface, once or on a schedule, and adds only the new ones to this case. They are read and flagged like any import.'),
      notice(pv.notice, 'warn'),
      canWrite ? h('section', { class: 'card' }, h('h3', null, 'Monitor an account'),
        h('div', { class: 'row so-cta' }, field('Where to look', prov), field('Account', query), field('Most posts per check', max), field('How often', every)), dl,
        help, field('Legal basis / authority (required)', basis),
        h('div', { class: 'row', style: 'margin-top:.7rem' }, go), out)
        : notice('Your role can read collected material but cannot start monitoring.', 'info'),
      jobsBox);
    await loadJobs();
  }

  return { priority, watch, lookalikes, narratives, places, dossier, postTools, sheetDialog, collect };
}
