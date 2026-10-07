// Social media intelligence: a lawful OSINT workbench. Officers IMPORT public material they collected lawfully; nothing is scraped.
// Wording rule: flags and hints are prompts for a human look. They never describe a person's intent, mood or guilt.
import { h, api, mount, clear, svg, notice, empty, chip, toast, kpi, table, tabs, field, modal, columnChart, fmtNum, fmtTime, download } from './lib.js';
import { intelTools } from './f_social_intel.js';

const errBox = ex => notice(ex.message || String(ex), 'bad');
const SEV_KIND = { high: 'bad', medium: 'warn', welfare: 'info' };
const STRENGTH_KIND = { strong: 'bad', high: 'bad', medium: 'warn', weak: '', low: '' };
const STATUS_TEXT = { unreviewed: 'Not yet reviewed', relevant: 'Marked relevant', false_positive: 'Marked false positive', reviewed: 'Reviewed, no decision' };
const ZONES = [['IST', 'India (IST, UTC+5:30)'], ['UTC', 'UTC'], ['GST', 'Gulf (UTC+4)'], ['PKT', 'Pakistan (UTC+5)'], ['BST', 'Bangladesh (UTC+6)'], ['SGT', 'Singapore (UTC+8)'], ['EST', 'US Eastern (UTC-5)']];
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const LAWFUL = 'Use only public material you are authorised to hold for this case. This tool never scrapes platforms or logs in to accounts; automatic collection uses official APIs and public feeds only. '
  + 'Record your authority: it is stored with every import and collection and shown in reports.';

const sel = (opts, value, onchange, label) => h('select', { 'aria-label': label, onchange: e => onchange(e.target.value) }, opts.map(([v, l]) => h('option', { value: v, selected: v === value }, l)));
const explain = text => h('details', { class: 'so-explain' }, h('summary', null, 'What am I looking at?'), h('p', { class: 'muted' }, text));
const oneLine = text => h('p', { class: 'muted so-line' }, text);

function highlight(text, phrase) {
  if (!text || !phrase) return text || '';
  const i = text.toLowerCase().indexOf(phrase.toLowerCase());
  if (i < 0) return text;
  return [text.slice(0, i), h('mark', { class: 'so-mark' }, text.slice(i, i + phrase.length)), text.slice(i + phrase.length)];
}
const idChips = e => {
  const out = [];
  (e.phones || []).forEach(p => out.push(chip('phone ' + p, 'info')));
  (e.emails || []).forEach(p => out.push(chip('email ' + p, 'info')));
  (e.upi_ids || []).forEach(p => out.push(chip('UPI ' + p, 'info')));
  (e.wallets || []).forEach(w => out.push(chip(`wallet ${w.value.slice(0, 8)}...${w.checksum_valid ? ' (checksum ok)' : ''}`, 'warn')));
  return out;
};

export async function render(root, ctx) {
  if (!ctx.caseNumber) { mount(root, notice('Select a case to work with its social-media material.', 'warn')); return; }
  const cn = encodeURIComponent(ctx.caseNumber);
  const canWrite = ctx.can('write'), canAnalyze = ctx.can('analyze'), canReview = ctx.can('review'), canReport = ctx.can('report');
  const state = { tab: 'overview', summary: null, postFilters: { q: '', handle: '', platform: '', hashtag: '', flagged: false, category: '', offset: 0 }, anTab: 'activity', tz: 'IST' };
  const body = h('div', { class: 'so-body' });
  const top = h('div', { class: 'so-head' },
    h('div', null, h('h2', null, 'Social media intelligence'),
      h('p', { class: 'muted' }, 'Bring lawfully collected public posts and profiles into the case, see who is connected to whom, and get a reviewed shortlist of wording that deserves a human look. Posts can be imported by hand, or collected through official APIs and public feeds.')));
  const tabItems = [['overview', 'Overview']];
  if (canAnalyze) tabItems.push(['priority', 'Priority queue']);
  tabItems.push(['accounts', 'Accounts'], ['posts', 'Posts']);
  if (canAnalyze) tabItems.push(['analysis', 'Analysis']);
  tabItems.push(['watch', 'Watch list']);
  if (canWrite) tabItems.push(['collect', 'Monitor']);
  tabItems.push(['import', 'Import']);
  const tabBar = tabs(tabItems, 'overview', id => { state.tab = id; draw(); });
  mount(root, h('div', { class: 'so-page' }, top, notice(LAWFUL, 'warn'), tabBar, body));

  const gotoTab = id => { state.tab = id; tabBar.select(id); draw(); };
  const intel = intelTools({ cn, ctx, openAccount: id => accountDialog(id), gotoTab: id => gotoTab(id) });
  const priority = () => intel.priority(body);
  const watch = () => intel.watch(body);
  const collect = () => intel.collect(body);
  async function loadSummary() { state.summary = await api(`/social/${cn}/summary`); return state.summary; }

  function draw() {
    mount(body, empty('Loading...'));
    ({ overview, priority, accounts, posts, analysis, watch, collect, import: importTab }[state.tab])().catch(ex => mount(body, errBox(ex)));
  }

  // ------------------------------------------------------------------------------------------ shared pieces
  function reviewDialog(f, status, done) {
    const note = h('textarea', { rows: 3, maxlength: 500, placeholder: status === 'false_positive' ? 'Required: why is this not a real concern? (quotation, joke, news, song ...)' : 'Optional note for the record', 'aria-label': 'Review note' });
    const m = modal(STATUS_TEXT[status] || status, h('div', null,
      h('p', { class: 'muted' }, 'Your decision is stored with your name and the time and audit-logged. It changes how the item is triaged, not any fact in the case.'), note,
      h('div', { class: 'row', style: 'margin-top:.7rem' }, h('button', { class: 'primary', onclick: async () => {
        if (status === 'false_positive' && note.value.trim().length < 3) return toast('Add a short note saying why', 'bad');
        try { await api(`/social/${cn}/flags/${f.id}/review`, { method: 'POST', body: { status, note: note.value.trim() } }); toast('Review recorded', 'good'); m.close(); done(); } catch (ex) { toast(ex.message, 'bad'); }
      } }, 'Save decision'))));
  }

  function flagCard(f, done) {
    const decided = f.review_status !== 'unreviewed';
    return h('article', { class: 'card so-flag so-sev-' + f.severity },
      h('div', { class: 'so-flag-top' },
        chip(f.label, SEV_KIND[f.severity] || ''), chip(`match strength ${Math.round(f.score * 100)}%`, f.level === 'high' ? 'bad' : f.level === 'medium' ? 'warn' : ''),
        chip(f.lang === 'hinglish' ? 'Hinglish' : f.lang === 'hi' ? 'Hindi' : 'English'), chip(STATUS_TEXT[f.review_status] || f.review_status, decided ? (f.review_status === 'relevant' ? 'good' : '') : 'warn'),
        h('span', { class: 'muted small' }, [f.platform ? `${f.platform} @${f.handle}` : '', f.posted_at ? fmtTime(f.posted_at) : ''].filter(Boolean).join(' · '))),
      h('p', { class: 'so-text' }, highlight(f.text || '', f.phrase)),
      f.context_notes && f.context_notes.length ? h('p', { class: 'muted small' }, 'Downgraded because it ' + f.context_notes.join('; ') + '.') : null,
      h('p', { class: 'small' }, h('b', null, 'Matched wording: '), '"' + f.phrase + '"', h('span', { class: 'muted' }, '  · rule ' + f.rule)),
      h('details', { class: 'so-explain' }, h('summary', null, 'Why this might matter, and why it might be wrong'), h('p', null, f.why), h('p', { class: 'muted' }, f.caveat)),
      f.review_note ? h('p', { class: 'small' }, h('b', null, `${f.reviewed_by || 'Reviewer'}: `), f.review_note) : null,
      canReview ? h('div', { class: 'row so-actions' },
        h('button', { class: 'sm', onclick: () => reviewDialog(f, 'relevant', done) }, 'Relevant to the case'),
        h('button', { class: 'sm', onclick: () => reviewDialog(f, 'false_positive', done) }, 'False positive'),
        h('button', { class: 'sm', onclick: () => reviewDialog(f, 'reviewed', done) }, 'Reviewed, no decision')) : null);
  }

  function postCard(p, done) {
    const e = p.extracted || {};
    const tags = [
      ...(e.hashtags || []).map(t => chip('#' + t)), ...(e.mentions || []).map(t => chip('@' + t)), ...idChips(e),
      ...(e.urls || []).map(u => chip(u.domain + (u.is_shortener ? ' (short link, destination unknown)' : ''), u.is_shortener ? 'warn' : '')),
      ...(e.places || []).map(pl => chip('place: ' + pl.name, 'good')),
    ];
    return h('article', { class: 'card so-post' },
      h('div', { class: 'so-post-top' }, h('b', null, `@${p.handle}`), chip(p.platform), h('span', { class: 'muted small' }, p.posted_at_ist || 'no timestamp'),
        h('span', { class: 'muted small' }, (e.language && e.language.name) || ''), p.url ? h('span', { class: 'muted small mono' }, p.url) : null),
      h('p', { class: 'so-text' }, p.text), tags.length ? h('div', { class: 'so-chips' }, tags) : null,
      intel.postTools(p),
      (p.flags || []).map(f => flagCard({ ...f, text: p.text, handle: p.handle, platform: p.platform, posted_at: p.posted_at }, done)));
  }

  // ------------------------------------------------------------------------------------------ Overview
  async function overview() {
    const s = await loadSummary();
    const c = s.counts;
    if (!c.posts && !c.accounts) {
      mount(body, oneLine('A summary of what has been imported into this case and what needs a human look.'),
        empty('Nothing has been imported into this case yet.'),
        h('div', { class: 'so-cta' }, canWrite ? h('button', { class: 'primary', onclick: () => gotoTab('import') }, 'Import material') : null, canWrite ? sampleButton() : null),
        canWrite ? null : notice('Your role can view and analyse social-media material but not import it. Ask a supervisor to import.', 'info'));
      return;
    }
    mount(body, oneLine('What has been imported into this case, who posts most, and which flagged items still need a human look.'),
      h('div', { class: 'grid g4' }, kpi('Posts', fmtNum(c.posts), `${fmtNum(c.imports)} import(s)`), kpi('Accounts', fmtNum(c.accounts), Object.entries(s.platforms).map(([k, v]) => `${k} ${v}`).join(', ')),
        kpi('Flags to review', fmtNum(c.flags_unreviewed), `${fmtNum(c.flags)} raised, ${fmtNum(c.flags_relevant)} relevant, ${fmtNum(c.flags_false_positive)} false positive`),
        kpi('Date range', s.date_range.first ? fmtTime(s.date_range.first).split(',')[0] : '–', s.date_range.last ? 'to ' + fmtTime(s.date_range.last).split(',')[0] : 'no timestamps')),
      s.masked ? notice('Phone numbers, e-mail addresses, UPI IDs and wallets are masked for your role.', 'info') : null,
      h('div', { class: 'grid g2' },
        h('section', { class: 'card' }, h('h3', null, 'Most active accounts'), oneLine('Click an account to see its profile, identifiers and posts.'),
          table([{ label: 'Account', render: a => h('button', { class: 'link', onclick: () => accountDialog(a.id) }, a.ref) }, { label: 'Name', render: a => a.display_name },
            { label: 'Posts', num: true, render: a => a.posts }, { label: 'Flagged', num: true, render: a => a.flagged_posts }, { label: 'Followers', num: true, render: a => fmtNum(a.followers) }], s.top_accounts)),
        h('section', { class: 'card' }, h('h3', null, 'Flags by category'), oneLine('Counts of wording matches. A count is not a measure of danger.'),
          s.flags_by_category.length ? h('div', { class: 'so-cats' }, s.flags_by_category.map(x => h('div', { class: 'so-cat' }, h('span', null, x.label), h('b', null, x.count)))) : empty('No wording flags.'),
          h('h4', null, 'Places and hashtags'), h('div', { class: 'so-chips' }, [...s.places.map(x => chip(`${x.name} ${x.count}`, 'good')), ...s.top_hashtags.map(x => chip(`#${x.tag} ${x.count}`))]))),
      h('section', null, h('h3', null, 'Needs a human look'), oneLine('Unreviewed flags, strongest first. Read the whole post, then mark it relevant, false positive or reviewed.'),
        s.recent_flags.length ? s.recent_flags.map(f => flagCard(f, () => overview().catch(ex => mount(body, errBox(ex))))) : empty('No unreviewed flags. Good.')),
      h('details', { class: 'so-explain' }, h('summary', null, 'Limits of this tool'), h('ul', null, s.limits.map(l => h('li', null, l)))));
  }

  function sampleButton() {
    const seed = h('input', { type: 'checkbox', id: 'so-seed' });
    return h('div', { class: 'so-sample' },
      h('button', { onclick: async e => {
        e.target.disabled = true;
        try { const r = await api(`/social/${cn}/import/sample`, { method: 'POST', body: { seed_entities: seed.checked } }); toast(r.message, 'good'); await loadSummary(); gotoTab('overview'); }
        catch (ex) { toast(ex.message, 'bad'); e.target.disabled = false; }
      } }, 'Try a sample'),
      h('label', { class: 'so-check small', for: 'so-seed' }, seed, 'Also add two synthetic case entities (a person and a phone) so link suggestions can be demonstrated'),
      h('span', { class: 'muted small' }, 'Synthetic people only. Uses no network.'));
  }

  // ------------------------------------------------------------------------------------------ Accounts
  async function accounts() {
    const q = h('input', { type: 'search', placeholder: 'Search handle, name or bio', 'aria-label': 'Search accounts' });
    const list = h('div'), links = h('div');
    mount(body, oneLine('Every account in the imported material, with the phone numbers, e-mails and other identifiers found in its bio and posts.'), links,
      h('div', { class: 'row' }, field('Search', q), h('button', { onclick: load }, 'Search')), list);
    async function load() {
      mount(list, empty('Loading...'));
      const r = await api(`/social/${cn}/accounts`, { params: { q: q.value, limit: 200 } });
      mount(list, r.masked ? notice('Identifiers are masked for your role.', 'info') : null,
        table([{ label: 'Account', render: a => h('button', { class: 'link', onclick: () => accountDialog(a.id) }, a.ref) }, { label: 'Display name', render: a => a.display_name },
          { label: 'Bio', render: a => (a.bio || '').slice(0, 90) }, { label: 'Identifiers', render: a => h('div', { class: 'so-chips' }, idChips(a.extracted)) },
          { label: 'Posts', num: true, render: a => a.posts }, { label: 'Flagged', num: true, render: a => a.flags }, { label: 'Followers', num: true, render: a => fmtNum(a.followers) }], r.accounts,
        { emptyText: 'No accounts yet. Import material on the Import tab, or try the sample.' }));
    }
    load().catch(ex => mount(list, errBox(ex)));
    if (canAnalyze) loadSuggestions(links).catch(ex => mount(links, errBox(ex)));
  }

  async function loadSuggestions(host, accountId) {
    const r = await api(`/social/${cn}/links/suggestions`, { params: { account_id: accountId } });
    const head = h('h3', null, 'Possible links to case entities');
    const intro = oneLine('Where a phone number, e-mail, UPI ID, handle or name in an account matches something already in the case. These are suggestions: accepting one adds an UNVERIFIED candidate link that a reviewer must still verify.');
    if (!r.suggestions.length) { mount(host, h('section', { class: 'card' }, head, intro, empty('No matches between these accounts and the case entities.'))); return; }
    mount(host, h('section', { class: 'card' }, head, intro,
      table([{ label: 'Account', render: s => s.account }, { label: 'Case entity', render: s => `${s.entity_name} (${String(s.entity_type || '').toLowerCase()})` },
        { label: 'Matched', render: s => h('div', null, chip(s.match_type, STRENGTH_KIND[s.strength]), ' ', h('span', { class: 'mono' }, s.matched_text)) },
        { label: 'Where and why', render: s => h('div', { class: 'small' }, `${s.where === 'bio' ? 'In the profile' : 'In a post'}: `, h('span', { class: 'muted' }, s.reason), s.snippet ? h('div', { class: 'muted so-snippet' }, '"' + s.snippet + '"') : null) },
        { label: '', render: s => (s.status === 'linked' ? chip('linked (unverified)', 'good') : canWrite ? h('button', { class: 'sm', onclick: () => acceptLink(s, () => loadSuggestions(host, accountId)) }, 'Accept as candidate') : chip('suggested')) }], r.suggestions)));
  }
  function acceptLink(s, done) {
    const note = h('textarea', { rows: 2, maxlength: 500, placeholder: 'Optional note' });
    const m = modal('Accept link as an unverified candidate', h('div', null,
      h('p', null, `${s.account} will be linked to ${s.entity_name} through the ${s.match_type} match. A social-account entity and a candidate relationship are created, pointing back to the ${s.post_uid ? 'post' : 'profile'} as their source.`),
      h('p', { class: 'muted' }, 'The relationship stays out of the verified graph until a reviewer verifies it in the normal flow.'), note,
      h('div', { class: 'row', style: 'margin-top:.7rem' }, h('button', { class: 'primary', onclick: async () => {
        try { const r = await api(`/social/${cn}/links/accept`, { method: 'POST', body: { account_id: s.account_id, entity_id: s.entity_id, match_type: s.match_type, note: note.value.trim() } }); toast(r.message, 'good'); m.close(); done(); }
        catch (ex) { toast(ex.message, 'bad'); }
      } }, 'Create candidate link'))));
  }

  async function accountDialog(id) {
    const a = await api(`/social/${cn}/accounts/${id}`);
    const sug = h('div');
    const m = modal(a.ref, h('div', { class: 'so-acct' },
      h('dl', { class: 'kv' }, h('dt', null, 'Display name'), h('dd', null, a.display_name || '–'), h('dt', null, 'Bio'), h('dd', null, a.bio || '–'), h('dt', null, 'Followers / following'), h('dd', null, `${fmtNum(a.followers)} / ${fmtNum(a.following)}`),
        h('dt', null, 'Profile URL'), h('dd', { class: 'mono' }, a.profile_url || '–'), h('dt', null, 'Identifiers'), h('dd', null, h('div', { class: 'so-chips' }, idChips(a.extracted).length ? idChips(a.extracted) : ['none found'])),
        h('dt', null, 'Places'), h('dd', null, (a.extracted.places || []).map(p => p.name).join(', ') || '–'), h('dt', null, 'Posts in case'), h('dd', null, `${a.posts} (${a.flags} flagged)`)),
      (a.bio_flags || []).map(f => flagCard({ ...f, text: a.bio, handle: a.handle, platform: a.platform }, () => { m.close(); accountDialog(id); })),
      canAnalyze ? h('div', { class: 'row so-actions' }, h('button', { onclick: () => { m.close(); intel.dossier(id).catch(ex => toast(ex.message, 'bad')); } }, 'Open full profile sheet')) : null,
      canAnalyze ? sug : null, h('h4', null, 'Recent posts'), a.recent_posts.length ? a.recent_posts.map(p => postCard(p, () => { m.close(); accountDialog(id); })) : empty('No posts imported for this account.')), { wide: true });
    if (canAnalyze) loadSuggestions(sug, id).catch(() => {});
  }

  // ------------------------------------------------------------------------------------------ Posts
  async function posts() {
    const f = state.postFilters;
    const cats = await api('/social/lexicon').then(l => l.categories.map(c => [c.key, c.label])).catch(() => []);
    const list = h('div');
    const q = h('input', { type: 'search', value: f.q, placeholder: 'Words in the post', 'aria-label': 'Search text' });
    const handle = h('input', { value: f.handle, placeholder: 'handle', 'aria-label': 'Handle' });
    const tag = h('input', { value: f.hashtag, placeholder: '#hashtag', 'aria-label': 'Hashtag' });
    const flagged = h('input', { type: 'checkbox', checked: f.flagged });
    const cat = sel([['', 'Any category'], ...cats], f.category, v => { f.category = v; }, 'Category');
    const apply = () => { Object.assign(f, { q: q.value, handle: handle.value, hashtag: tag.value, flagged: flagged.checked, offset: 0 }); load(); };
    mount(body, oneLine('Search and read the imported posts. Extracted handles, links, phone numbers, places and language are shown under each post.'),
      h('div', { class: 'row' }, field('Search', q), field('Handle', handle), field('Hashtag', tag), field('Category', cat), h('label', { class: 'so-check' }, flagged, 'Flagged only'), h('button', { onclick: apply }, 'Filter')), list);
    async function load() {
      mount(list, empty('Loading...'));
      const r = await api(`/social/${cn}/posts`, { params: { q: f.q, handle: f.handle, hashtag: f.hashtag, flagged: f.flagged ? 'true' : '', category: f.category, limit: 25, offset: f.offset } });
      mount(list, r.masked ? notice('Identifiers inside posts are masked for your role.', 'info') : null,
        h('p', { class: 'muted small' }, `${fmtNum(r.total)} post(s) match. Showing ${r.posts.length ? f.offset + 1 : 0} to ${f.offset + r.posts.length}.`),
        r.posts.length ? r.posts.map(p => postCard(p, load)) : empty(r.total ? 'No more posts.' : 'No posts match. Clear the filters, or import material first.'),
        h('div', { class: 'row' }, h('button', { class: 'sm', disabled: f.offset === 0, onclick: () => { f.offset = Math.max(0, f.offset - 25); load(); } }, 'Previous'),
          h('button', { class: 'sm', disabled: f.offset + 25 >= r.total, onclick: () => { f.offset += 25; load(); } }, 'Next')));
    }
    load().catch(ex => mount(list, errBox(ex)));
  }

  // ------------------------------------------------------------------------------------------ Analysis
  async function analysis() {
    if (!canAnalyze) { mount(body, notice('Your role can read the imported material but not run analyses.', 'warn')); return; }
    const host = h('div');
    const bar = tabs([['activity', 'Activity'], ['network', 'Interactions'], ['coordinated', 'Coordinated activity'], ['flags', 'Content flags'], ['attribution', 'Cross-account hints'], ['lookalikes', 'Look-alikes'], ['narratives', 'Spread'], ['places', 'Places map']], state.anTab, id => { state.anTab = id; run(); });
    mount(body, oneLine('Descriptive analyses computed from what you imported. They point at patterns worth reading; none of them says who someone is or what they meant.'), bar, host);
    async function run() {
      mount(host, empty('Computing...'));
      try { await ({ activity, network, coordinated, flags, attribution, lookalikes: intel.lookalikes, narratives: intel.narratives, places: intel.places }[state.anTab])(host); } catch (ex) { mount(host, errBox(ex)); }
    }
    run();
  }

  function heatmap(matrix) {
    const max = Math.max(1, ...matrix.flat()), cw = 22, ch = 20, left = 34, top = 18;
    const s = svg('svg', { viewBox: `0 0 ${left + 24 * cw} ${top + 7 * ch + 4}`, class: 'so-heat', role: 'img', 'aria-label': 'Posting hours by weekday' });
    for (let hr = 0; hr < 24; hr += 3) s.append(svg('text', { x: left + hr * cw + 2, y: 12, class: 'so-axis' }, String(hr).padStart(2, '0')));
    matrix.forEach((row, d) => {
      s.append(svg('text', { x: 2, y: top + d * ch + 14, class: 'so-axis' }, DAYS[d]));
      row.forEach((v, hr) => s.append(svg('rect', { x: left + hr * cw, y: top + d * ch, width: cw - 2, height: ch - 2, rx: 3, class: 'so-cell', 'fill-opacity': v ? (0.18 + 0.82 * v / max).toFixed(2) : 0.06 },
        svg('title', null, `${DAYS[d]} ${String(hr).padStart(2, '0')}:00, ${v} post(s)`))));
    });
    return s;
  }

  async function activity(host) {
    const r = await api(`/social/${cn}/analysis/timeline`, { params: { tz: state.tz } });
    const accSel = sel([['', 'All accounts'], ...r.accounts.map(a => [a.account, `${a.account} (${a.posts})`])], state.acct || '', v => { state.acct = v; show(); }, 'Account');
    const zone = sel(ZONES, state.tz, v => { state.tz = v; run2(); }, 'Time zone');
    const out = h('div');
    const run2 = () => activity(host).catch(ex => mount(host, errBox(ex)));
    function show() {
      const a = state.acct ? r.accounts.find(x => x.account === state.acct) : r.overall;
      mount(out, a.dated ? h('div', null, h('p', { class: 'so-headline' }, a.headline), heatmap(a.heatmap), h('h4', null, 'Posts per day'),
        columnChart(a.daily.map(d => ({ x: d.date, y: d.count }))), a.undated ? h('p', { class: 'muted small' }, `${a.undated} post(s) have no timestamp and are left out.`) : null)
        : empty('No timestamped posts to chart.'));
    }
    mount(host, h('div', { class: 'row' }, field('Time zone', zone), field('Account', accSel)), explain(r.explain), out,
      r.accounts.length ? [h('h4', null, 'Busiest window per account'), table([{ label: 'Account', render: a => a.account }, { label: 'Posts', num: true, render: a => a.posts },
        { label: 'Busiest 3 hours', render: a => a.busiest_window ? `${a.busiest_window} (${Math.round(a.busiest_share * 100)}%)` : '–' }, { label: 'Night posts', render: a => a.night_share != null ? Math.round(a.night_share * 100) + '%' : '–' }], r.accounts)] : null);
    show();
  }

  function networkGraph(r) {
    const nodes = r.nodes.slice(0, 40).map((n, i, arr) => ({ ...n, x: 300 + 220 * Math.cos((2 * Math.PI * i) / arr.length), y: 190 + 150 * Math.sin((2 * Math.PI * i) / arr.length), dx: 0, dy: 0 }));
    const idx = new Map(nodes.map(n => [n.id, n]));
    const edges = r.edges.filter(e => idx.has(e.source) && idx.has(e.target));
    for (let it = 0; it < 160; it++) {
      for (const a of nodes) { a.dx = 0; a.dy = 0; }
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j]; let dx = a.x - b.x, dy = a.y - b.y; const d2 = Math.max(dx * dx + dy * dy, 25), f = 5200 / d2, d = Math.sqrt(d2);
        dx = (dx / d) * f; dy = (dy / d) * f; a.dx += dx; a.dy += dy; b.dx -= dx; b.dy -= dy;
      }
      for (const e of edges) {
        const a = idx.get(e.source), b = idx.get(e.target), dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = (d - 80) * 0.02;
        a.dx += (dx / d) * f * d * 0.05; a.dy += (dy / d) * f * d * 0.05; b.dx -= (dx / d) * f * d * 0.05; b.dy -= (dy / d) * f * d * 0.05;
      }
      for (const n of nodes) { n.x = Math.min(580, Math.max(20, n.x + Math.max(-12, Math.min(12, n.dx + (300 - n.x) * 0.004)))); n.y = Math.min(370, Math.max(20, n.y + Math.max(-12, Math.min(12, n.dy + (190 - n.y) * 0.004)))); }
    }
    const s = svg('svg', { viewBox: '0 0 600 390', class: 'so-net', role: 'img', 'aria-label': 'Interaction network of accounts' });
    edges.forEach(e => { const a = idx.get(e.source), b = idx.get(e.target); s.append(svg('line', { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: 'so-edge', 'stroke-width': Math.min(4, 1 + e.weight * 0.5) }, svg('title', null, `${e.source} to ${e.target}: ` + Object.entries(e.kinds).map(([k, v]) => `${k} ${v}`).join(', ')))); });
    nodes.forEach(n => {
      s.append(svg('circle', { cx: n.x, cy: n.y, r: 5 + Math.min(11, n.weighted_degree * 1.4), class: 'so-node so-comm-' + (n.community % 5) + (n.in_import ? '' : ' so-ref') }, svg('title', null, `${n.id}: ${n.degree} links`)));
      s.append(svg('text', { x: n.x + 9, y: n.y + 3, class: 'so-nlabel' }, n.id.split(':')[1] || n.id));
    });
    return s;
  }
  async function network(host) {
    const r = await api(`/social/${cn}/analysis/network`);
    mount(host, h('p', { class: 'so-headline' }, r.headline), explain(r.explain),
      r.nodes.length ? h('div', { class: 'grid g2' }, h('div', { class: 'card' }, networkGraph(r), h('p', { class: 'muted small' }, 'Solid dots are accounts you imported; dashed dots were only mentioned. Colour shows the group the accounts fall into.')),
        h('div', null, h('h4', null, 'Most-connected accounts'), table([{ label: 'Account', render: n => n.id }, { label: 'Links', num: true, render: n => n.degree }, { label: 'Interactions', num: true, render: n => n.weighted_degree },
          { label: 'Kind', render: n => n.kind }], r.top))) : empty('No mentions, replies or reposts between accounts were found.'));
  }

  async function coordinated(host) {
    const w = state.win || 60, sim = state.sim || 0.7;
    const r = await api(`/social/${cn}/analysis/coordinated`, { params: { window_minutes: w, similarity: sim } });
    const ctl = h('div', { class: 'row' }, field('Time window', sel([['10', '10 minutes'], ['30', '30 minutes'], ['60', '1 hour'], ['240', '4 hours'], ['1440', '1 day']], String(w), v => { state.win = +v; coordinated(host).catch(ex => mount(host, errBox(ex))); }, 'Window')),
      field('Text similarity', sel([['0.6', 'loose (60%)'], ['0.7', 'normal (70%)'], ['0.85', 'strict (85%)']], String(sim), v => { state.sim = +v; coordinated(host).catch(ex => mount(host, errBox(ex))); }, 'Similarity')));
    mount(host, ctl, h('p', { class: 'so-headline' }, r.headline), explain(r.explain),
      r.clusters.length ? h('div', null, h('h4', null, 'Near-identical posts by different accounts'), r.clusters.map(c => h('article', { class: 'card so-coord' },
        h('div', { class: 'so-flag-top' }, chip(c.strength + ' signal', STRENGTH_KIND[c.strength]), chip(`${c.accounts.length} accounts`), chip(`${c.posts} posts`), chip(`within ${c.span_minutes} min`), chip(`${Math.round(c.min_similarity * 100)}%+ similar`)),
        h('p', { class: 'so-text' }, c.sample), h('p', { class: 'small' }, 'Accounts: ', c.accounts.join(', ')), h('p', { class: 'muted small' }, c.reason + ' Real campaigns, news outlets and fan groups also copy text.')))) : null,
      r.bursts.length ? h('div', null, h('h4', null, 'Shared links and hashtags'), table([{ label: 'Type', render: b => (b.kind === 'link_burst' ? 'link' : 'hashtag') }, { label: 'What', render: b => h('span', { class: 'mono' }, b.key) },
        { label: 'Accounts', num: true, render: b => b.accounts.length }, { label: 'Within', render: b => b.span_minutes + ' min' }, { label: 'Signal', render: b => chip(b.strength, STRENGTH_KIND[b.strength]) },
        { label: 'Who', render: b => b.accounts.join(', ') }], r.bursts)) : null,
      !r.clusters.length && !r.bursts.length ? empty('Nothing coordinated found at these settings. Try a longer window or looser similarity.') : null);
  }

  async function flags(host) {
    const [r, list, ev] = await Promise.all([api(`/social/${cn}/analysis/flags`), api(`/social/${cn}/flags`, { params: { limit: 60 } }), api('/social/evaluation').catch(() => null)]);
    const listHost = h('div');
    const reload = () => flags(host).catch(ex => mount(host, errBox(ex)));
    mount(host, h('p', { class: 'so-headline' }, r.headline), explain(r.explain), notice(r.caveat, 'warn'),
      table([{ label: 'Category', render: c => c.label }, { label: 'Flags', num: true, render: c => c.count }, { label: 'To review', num: true, render: c => c.unreviewed },
        { label: 'Relevant', num: true, render: c => c.relevant }, { label: 'False positive', num: true, render: c => c.false_positive }, { label: 'What it means', render: c => h('span', { class: 'muted small' }, c.why) }], r.categories),
      h('h4', null, 'Flagged items'), listHost, ev ? accuracyPanel(ev) : null);
    mount(listHost, list.flags.length ? list.flags.map(f => flagCard(f, reload)) : empty('No flags.'));
  }
  function accuracyPanel(ev) {
    const row = (name, d) => [name, `${d.posts} posts`, d.micro.precision != null ? Math.round(d.micro.precision * 100) + '%' : '–', d.micro.recall != null ? Math.round(d.micro.recall * 100) + '%' : '–'];
    return h('details', { class: 'so-explain' }, h('summary', null, 'How accurate is the flagging? (measured on a small synthetic test set)'),
      table([{ label: 'Test set', render: r => r[0] }, { label: 'Size', render: r => r[1] }, { label: 'Precision', render: r => r[2] }, { label: 'Recall', render: r => r[3] }], [row('Used while writing the rules', ev.dev), row('Written afterwards, never tuned on', ev.heldout)]),
      h('p', { class: 'muted small' }, ev.note));
  }

  async function attribution(host) {
    const r = await api(`/social/${cn}/analysis/attribution`);
    mount(host, h('p', { class: 'so-headline' }, r.headline), explain(r.explain),
      r.hints.length ? r.hints.map(x => h('article', { class: 'card so-coord' },
        h('div', { class: 'so-flag-top' }, chip(x.strength, STRENGTH_KIND[x.strength]), chip(x.type === 'upi' ? 'UPI ID' : x.type.replace('_', ' ')), h('span', { class: 'mono' }, x.value)),
        h('p', { class: 'small' }, 'Accounts: ', x.accounts.join(', ')), h('p', null, x.reason + '.'), h('p', { class: 'muted small' }, x.caveat))) : empty('No shared identifiers, links or bio wording between different accounts.'));
  }

  // ------------------------------------------------------------------------------------------ Import
  async function importTab() {
    if (!canWrite) {
      const r = await api(`/social/${cn}/imports`);
      mount(body, notice('Your role can view and analyse imported material but not import it.', 'info'), importHistory(r));
      return;
    }
    const source = h('input', { placeholder: 'e.g. Public profile viewed on 30 Sep 2026 by SI Rao, screenshots saved as evidence E-123', maxlength: 300, 'aria-label': 'Source' });
    const basis = h('textarea', { rows: 3, maxlength: 1000, placeholder: 'Required. Who authorised this collection and under what provision, e.g. "Public-source enquiry under order ref 12/2026 of the SP; only publicly visible content".', 'aria-label': 'Legal basis' });
    const tz = sel(ZONES, 'IST', () => {}, 'Time zone of timestamps without a zone');
    const note = h('input', { placeholder: 'Optional note', maxlength: 500 });
    const mode = { v: 'paste' };
    const paste = h('textarea', { rows: 8, placeholder: 'Paste posts here. Separate posts with a blank line. JSON or CSV pasted here is detected automatically. Chat lines like "2026-09-01 22:15 - handle: text" also work.', 'aria-label': 'Pasted text' });
    const plat = h('input', { placeholder: 'platform (x, instagram, telegram...)', 'aria-label': 'Platform for pasted posts' });
    const hand = h('input', { placeholder: 'handle for pasted posts', 'aria-label': 'Handle for pasted posts' });
    const file = h('input', { type: 'file', accept: '.json,.csv,.txt,application/json,text/csv', 'aria-label': 'Export file' });
    const fileInfo = h('p', { class: 'muted small' }, 'Accepted: JSON or CSV exports with columns like platform, handle, display_name, post_id, url, timestamp, text, likes, shares, mentions, media, location, bio, followers. Up to 5,000 rows.');
    let fileText = '', fileName = '';
    file.addEventListener('change', async () => {
      const f = file.files[0]; if (!f) return;
      if (f.size > 2.5 * 1024 * 1024) { toast('That file is over 2.5 MB. Split it and import in parts.', 'bad'); file.value = ''; return; }
      fileText = await f.text(); fileName = f.name; mount(fileInfo, `${f.name}: ${fmtNum(f.size)} bytes read. Nothing has been sent yet.`);
    });
    const man = {};
    const mf = (k, label, opts = {}) => { man[k] = h(opts.area ? 'textarea' : 'input', { rows: 3, placeholder: opts.ph || '', type: opts.type || 'text', 'aria-label': label }); return field(label, man[k]); };
    const manualForm = h('div', { class: 'grid g3' }, mf('platform', 'Platform', { ph: 'x, instagram, ...' }), mf('handle', 'Handle'), mf('display_name', 'Display name'), mf('timestamp', 'Posted at', { type: 'datetime-local' }),
      mf('url', 'Post URL'), mf('likes', 'Likes'), mf('shares', 'Shares'), mf('mentions', 'Mentions (comma separated)'), mf('location', 'Location tag'), mf('text', 'Post text', { area: true }), mf('bio', 'Bio (if adding a profile)', { area: true }));
    const panes = { paste: h('div', null, h('div', { class: 'row' }, field('Platform', plat), field('Handle', hand)), paste), file: h('div', null, file, fileInfo), manual: manualForm };
    const paneHost = h('div');
    const modeBar = tabs([['paste', 'Paste text'], ['file', 'Upload JSON / CSV'], ['manual', 'Add one item']], 'paste', id => { mode.v = id; mount(paneHost, panes[id]); });
    mount(paneHost, panes.paste);
    const result = h('div');
    const go = h('button', { class: 'primary', onclick: async () => {
      if (source.value.trim().length < 2) return toast('Say where this material came from', 'bad');
      if (basis.value.trim().split(/\s+/).length < 3) return toast('State the legal basis or authority (a short sentence)', 'bad');
      const b = { source: source.value.trim(), legal_basis: basis.value.trim(), assumed_timezone: tz.value, note: note.value.trim() };
      if (mode.v === 'paste') Object.assign(b, { content: paste.value, source_type: 'paste', default_platform: plat.value.trim(), default_handle: hand.value.trim() });
      else if (mode.v === 'file') Object.assign(b, { content: fileText, source_type: 'upload', filename: fileName });
      else {
        const rec = {}; for (const [k, el] of Object.entries(man)) if (el.value) rec[k] = k === 'timestamp' ? el.value.replace('T', ' ') : el.value;
        Object.assign(b, { records: [rec], source_type: 'manual' });
      }
      go.disabled = true;
      try {
        const r = await api(`/social/${cn}/import`, { method: 'POST', body: b });
        toast(r.message, 'good');
        mount(result, notice(`${r.message} SHA-256 of what you supplied: ${r.sha256}.` + (r.duplicates_skipped ? ` ${r.duplicates_skipped} duplicate(s) skipped.` : ''), 'good'), (r.warnings || []).map(w => notice(w, 'warn')));
        await loadSummary(); importTab().catch(() => {});
      } catch (ex) { mount(result, errBox(ex)); } finally { go.disabled = false; }
    } }, 'Import into this case');
    const hist = await api(`/social/${cn}/imports`);
    mount(body, oneLine('Add lawfully collected public material. Each import stores its source, who collected it, when, your legal basis, and a SHA-256 of exactly what you supplied.'),
      h('div', { class: 'card' }, h('h3', null, 'New import'), h('div', { class: 'grid g2' }, field('Where did this come from? (required)', source), field('Time zone of timestamps that carry none', tz)),
        field('Legal basis / authority for collecting it (required)', basis), field('Note', note), modeBar, paneHost, h('div', { class: 'row', style: 'margin-top:.8rem' }, go), result),
      h('div', { class: 'card' }, h('h3', null, 'Or try a sample'), oneLine('Loads about 40 synthetic posts from 12 invented accounts, including a copied scam post, so every tab has something to show.'), sampleButton()),
      importHistory(hist));
  }
  function importHistory(r) {
    return h('div', { class: 'card' }, h('h3', null, 'Import history'), oneLine('Every import, with its integrity hash and the authority recorded for it.'),
      table([{ label: 'When', render: i => fmtTime(i.imported_at) }, { label: 'By', render: i => i.collected_by }, { label: 'Source', render: i => i.source }, { label: 'Legal basis', render: i => i.legal_basis },
        { label: 'Posts', num: true, render: i => i.posts }, { label: 'Accounts', num: true, render: i => i.accounts }, { label: 'Flags', num: true, render: i => i.flags },
        { label: 'SHA-256', render: i => h('span', { class: 'mono', title: i.sha256 }, i.sha256.slice(0, 16) + '...') }], r.imports, { emptyText: 'Nothing imported yet.' }),
      canReport ? h('div', { class: 'row', style: 'margin-top:.8rem' }, h('button', { class: 'sm', onclick: () => exportReport('csv') }, 'Export flagged items (CSV)'), h('button', { class: 'sm', onclick: () => exportReport('json') }, 'Export findings (JSON)')) : null);
  }
  async function exportReport(fmt) {
    try {
      const res = await api(`/social/${cn}/report`, { params: { format: fmt }, raw: true });
      const blob = await res.blob();
      download(blob, `social_${ctx.caseNumber}.${fmt}`);
    } catch (ex) { toast(ex.message, 'bad'); }
  }

  draw();
}
