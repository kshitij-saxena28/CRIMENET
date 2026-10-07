// Document ingestion: upload with progress, per-document summary, entity/relationship review, original | English view.
// All text is set through h() (textContent) - nothing here builds HTML strings.
import { h, api, mount, field, table, tabs, notice, empty, chip, toast, fmtTime, pct, session, modal } from './lib.js';

const ENTITY_TYPES = ['PERSON', 'PHONE', 'VEHICLE', 'ACCOUNT', 'ORGANIZATION', 'LOCATION', 'EMAIL', 'DEVICE', 'CASE', 'DOCUMENT'];
const ALLOWED = ['.txt', '.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.docx', '.webp'];
const HIGH = 0.9;   // "high confidence" for bulk accept
const LANGS = [['auto', 'Detect automatically'], ['en', 'English'], ['hi', 'Hindi'], ['mr', 'Marathi'], ['bn', 'Bengali'], ['ta', 'Tamil'], ['te', 'Telugu'], ['gu', 'Gujarati'], ['kn', 'Kannada'], ['pa', 'Punjabi'], ['ml', 'Malayalam'], ['ur', 'Urdu']];
const QUALITY_KIND = { glossary: 'good', 'rule-based': 'info', model: 'good', identity: '', unknown: 'bad' };
const QUALITY_TEXT = { glossary: 'Glossary match', 'rule-based': 'Rule-based', model: 'Neural model', identity: 'Already English', unknown: 'Not understood' };

/** Turn a server or network error into something an officer can act on. */
export function friendlyError(msg, status, name = '') {
  const m = String(msg || '');
  if (status === 0) return 'The server could not be reached. Check that it is still running, then try again.';
  if (status === 401) return 'Your session has expired. Please sign in again.';
  if (status === 403) return 'You do not have permission to do this. Ask a supervisor to upload or review it.';
  if (status === 413 || /too large|exceeds|size/i.test(m)) return `${name || 'This file'} is too large. Split it into smaller files or scan at a lower resolution.`;
  if (/unsupported|not allowed|file type|extension/i.test(m)) return `${name || 'This file'} is not a supported type. Use PDF, a photo or scan (PNG, JPG, TIFF, WEBP), Word (.docx) or plain text (.txt).`;
  if (/empty/i.test(m)) return `${name || 'This file'} is empty. Check that the file was saved or scanned properly.`;
  if (/no (readable )?text|could not read|ocr/i.test(m)) return `No text could be read from ${name || 'this file'}. The scan may be too faint or blurred. Scan it again at 300 dpi or higher.`;
  return m || 'Something went wrong. Please try again.';
}

function uploadWithProgress(file, fields, onProgress, onSent) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/documents/extract');
    if (session.token) xhr.setRequestHeader('Authorization', 'Bearer ' + session.token);
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.upload.onload = () => onSent();
    xhr.onerror = () => reject(Object.assign(new Error('network'), { status: 0 }));
    xhr.onload = () => {
      let data = null; try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
      const d = data && data.detail;
      reject(Object.assign(new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : `Request failed (${xhr.status})`), { status: xhr.status }));
    };
    const fd = new FormData(); fd.append('file', file);
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    xhr.send(fd);
  });
}

// ------------------------------------------------------------------------------------------------ page
/** Pop-up asking for the details of a new case. Resolves to the new case number, or null if cancelled. */
function askNewCase(ctx, hint = '') {
  return new Promise(resolve => {
    const base = String(hint || '').replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').toUpperCase();
    const auto = base.length >= 3 ? base.slice(0, 60) : `CASE-${new Date().getFullYear()}-${String(Math.floor(Math.random() * 900) + 100)}`;
    const num = h('input', { value: auto, required: true, minlength: 3, maxlength: 100, autocomplete: 'off', 'aria-label': 'Case number' });
    const title = h('input', { placeholder: 'e.g. UPI fraud, Sector 14', maxlength: 255, value: base && base.length >= 3 ? '' : '', 'aria-label': 'Case title' });
    const summary = h('textarea', { rows: 3, maxlength: 5000, placeholder: 'Optional. A line or two on what this case is about.', 'aria-label': 'Summary' });
    const err = h('div', { class: 'err', role: 'alert' });
    let done = false;
    const finish = v => { if (done) return; done = true; m.close(); resolve(v); };
    const save = h('button', { class: 'primary', type: 'submit' }, 'Create case and continue');
    const form = h('form', { class: 'grid', onsubmit: async e => {
      e.preventDefault(); err.textContent = ''; save.disabled = true;
      try {
        const r = await api('/workbench/new', { method: 'POST', body: { case_number: num.value.trim(), title: title.value.trim() || 'Fresh FIR Investigation', summary: summary.value.trim() } });
        const cnew = (r && (r.case_number || r.case?.case_number)) || num.value.trim().toUpperCase();
        if (ctx.adoptCase) await ctx.adoptCase(cnew);
        toast(`Case ${cnew} created`, 'good'); finish(cnew);
      } catch (ex) { err.textContent = ex.status === 409 ? 'That case number is already in use. Choose another, or select that case in the top bar.' : (ex.message || 'Could not create the case'); save.disabled = false; }
    } },
    h('p', { class: 'muted small', style: 'margin:0' }, 'Documents must belong to a case. Fill in the details and the upload continues straight after.'),
    field('Case number', num), field('Title', title), field('Summary', summary), err,
    h('div', { class: 'row', style: 'margin:0;justify-content:flex-end' }, h('button', { type: 'button', onclick: () => finish(null) }, 'Cancel'), save));
    const m = modal('Create a case for this FIR', form);
    m.el.addEventListener('click', e => { if (e.target === m.el) finish(null); });
    m.el.querySelector('.modal .row button.sm')?.addEventListener('click', () => finish(null));
    setTimeout(() => title.focus(), 40);
  });
}

export async function render(root, ctx) {
  let cn = ctx.caseNumber;
  const listBox = h('div'), detail = h('div', { class: 'ig-detail' }), queueBox = h('div', { class: 'ig-queue' });
  const lang = h('select', { 'aria-label': 'Document language' }, LANGS.map(([v, t]) => h('option', { value: v }, t)));
  const files = h('input', { type: 'file', multiple: true, accept: ALLOWED.join(','), 'aria-label': 'Choose files' });
  const drop = h('div', { class: 'ig-drop', tabindex: 0, role: 'button', 'aria-label': 'Drop files here or press Enter to choose' }, 'Drop FIRs and complaints here, or choose files below');
  let picked = [];
  const setPicked = list => { picked = [...list]; drop.textContent = picked.length ? `${picked.length} file(s) ready: ${picked.map(f => f.name).join(', ')}` : 'Drop FIRs and complaints here, or choose files below'; };
  files.addEventListener('change', () => setPicked(files.files));
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); setPicked(e.dataTransfer.files); });
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); files.click(); } });

  const caseNote = h('span', { class: 'muted small' });
  const upBtn = h('button', { class: 'primary', onclick: () => startUpload() }, 'Upload and analyse');

  function queueRow(file) {
    const state = chip('Waiting', ''), bar = h('progress', { max: 100, value: 0, 'aria-label': 'Upload progress ' + file.name }), msg = h('div', { class: 'small muted' });
    const open = h('button', { class: 'sm', style: 'display:none' }, 'Open');
    const row = h('div', { class: 'ig-qrow' }, h('div', { class: 'ig-qname' }, file.name), state, bar, open, msg);
    const set = (text, kind, note) => { state.textContent = text; state.className = 'chip ' + kind; if (note != null) msg.textContent = note; };
    return { row, set, bar, open, msg };
  }

  async function startUpload() {
    if (!picked.length) return toast('Choose one or more files first', 'bad');
    if (!cn) {
      const made = await askNewCase(ctx, picked[0].name);
      if (!made) return toast('Upload cancelled: no case was created', 'bad');
      cn = made; caseNote.textContent = `attached to case ${cn}`;
    }
    upBtn.disabled = true;
    const group = 'GRP-' + Math.random().toString(16).slice(2, 12).toUpperCase();
    const batch = picked; picked = []; files.value = ''; setPicked([]);
    const rows = batch.map(f => { const q = queueRow(f); queueBox.prepend(q.row); return q; });
    let ok = 0, last = null;
    for (let i = 0; i < batch.length; i++) {
      const f = batch[i], q = rows[i], ext = '.' + (f.name.split('.').pop() || '').toLowerCase();
      if (!ALLOWED.includes(ext)) { q.set('Failed', 'bad', friendlyError('unsupported', 400, f.name)); continue; }
      if (!f.size) { q.set('Failed', 'bad', friendlyError('empty', 400, f.name)); continue; }
      q.set('Uploading', 'info', 'Sending the file to the evidence store…');
      const t0 = Date.now();
      try {
        const d = await uploadWithProgress(f, { language: lang.value, case_number: cn, group_id: group },
          p => { q.bar.value = Math.round(p * 100); },
          () => { q.bar.removeAttribute('value'); q.set('Analysing', 'info', 'Reading the text, finding names, numbers and sections, and preparing the English view…'); });
        ok++; last = d;
        const problems = (d.quality?.problems || []).filter(p => p.severity === 'error').length;
        const made = d.created_at ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(d.created_at) ? d.created_at : d.created_at + 'Z').getTime() : NaN;
        const already = !isNaN(made) && (t0 - made > 5000);
        q.bar.value = 100;
        q.set(problems ? 'Needs attention' : 'Ready', problems ? 'warn' : 'good', already ? 'This exact file was already uploaded to this case; showing the existing document.' : `${d.entity_count || 0} item(s) found. Open it to review.`);
        q.open.style.display = ''; q.open.onclick = () => openDoc(d.document_id);
      } catch (ex) {
        q.bar.value = 0; q.set('Failed', 'bad', friendlyError(ex.message, ex.status, f.name));
      }
    }
    toast(`${ok} of ${batch.length} file(s) analysed. Review each document before verifying.`, ok ? 'good' : 'bad');
    upBtn.disabled = false;
    await loadList();
    if (ok === 1 && batch.length === 1 && last) await openDoc(last.document_id);
  }

  const tblFile = h('input', { type: 'file', accept: '.csv,.xlsx', 'aria-label': 'Table file' });
  const tblOut = h('div', { class: 'small' });
  const tblBtn = h('button', { onclick: async () => {
    if (!tblFile.files.length) return toast('Choose a CSV or XLSX file', 'bad');
    if (!cn) {
      const made = await askNewCase(ctx, tblFile.files[0].name);
      if (!made) return;
      cn = made;
    }
    const fd = new FormData(); fd.append('file', tblFile.files[0]); fd.append('case_number', cn);
    try {
      const r = await api('/ingest/table', { method: 'POST', form: fd });
      mount(tblOut, notice(Object.entries(r).filter(([, v]) => typeof v !== 'object').map(([k, v]) => `${k}: ${v}`).join(' · '), 'good'));
    } catch (ex) { toast(friendlyError(ex.message, ex.status, tblFile.files[0].name), 'bad'); }
  } }, 'Ingest table');

  async function loadList() {
    let r;
    if (!cn && ctx.user.role !== 'admin') return mount(listBox, empty('No case yet. Upload a FIR above and you will be asked to create one.'));
    try { r = await api('/documents', { params: { case_number: cn } }); } catch (ex) { return mount(listBox, notice(friendlyError(ex.message, ex.status), 'bad')); }
    mount(listBox, table([
      { k: 'filename', label: 'File' },
      { label: 'FIR', render: d => d.structured?.fir_number || d.fir_number || '–' },
      { label: 'Language', render: d => d.structured?.language?.primary || '–' },
      { label: 'Status', render: d => chip(d.status, d.status === 'Verified' ? 'good' : d.status === 'Rejected' ? 'bad' : 'warn') },
      { label: 'Items', num: true, render: d => d.entity_count },
      { label: 'To check', num: true, render: d => (d.quality?.summary?.entities_needing_review ?? '–') },
    ], r.documents, { onRow: d => openDoc(d.document_id), emptyText: 'No documents in this case yet. Upload a FIR or complaint above.' }));
  }

  async function openDoc(id) {
    let d;
    try { d = await api('/documents/' + encodeURIComponent(id)); } catch (ex) { return toast(friendlyError(ex.message, ex.status), 'bad'); }
    mount(detail, reviewPanel(d, ctx, async () => { await loadList(); await openDoc(id); }, () => { mount(detail); loadList(); }));
    detail.scrollIntoView({ behavior: 'smooth' });
  }

  mount(root,
    ctx.can('write') ? h('div', { class: 'grid g2' },
      h('div', { class: 'card ig-upload' }, h('h3', null, 'Upload FIRs and documents'),
        h('p', { class: 'muted small' }, `Each file is stored as evidence with a SHA-256 fingerprint. Nothing enters the graph until you verify it. ${cn ? 'It will be attached to case ' + cn + '.' : 'No case is selected, so you will be asked to create one.'}`),
        drop, h('div', { class: 'row' }, field('Files', files), field('Language', lang), upBtn, h('button', { type: 'button', onclick: async () => { const made = await askNewCase(ctx, ''); if (made) { cn = made; loadList(); } } }, 'New case…')), caseNote, queueBox),
      h('div', { class: 'card' }, h('h3', null, 'Structured data (CSV / XLSX)'), h('p', { class: 'muted small' }, 'Call records, bank statements and other tables are added as candidate records.'),
        h('div', { class: 'row' }, field('Table file', tblFile), tblBtn), tblOut))
      : notice('You can read documents but not upload them. Ask a supervisor if you need to add one.', ''),
    h('div', { class: 'card', style: 'margin-top:1rem' }, h('h3', null, 'Documents'), listBox),
    detail);
  await loadList();
}

// ------------------------------------------------------------------------------------------------ helpers
function kv(rows) {
  rows = rows.filter(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length));
  return h('dl', { class: 'kv' }, rows.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, Array.isArray(v) ? v.map(x => typeof x === 'object' ? JSON.stringify(x) : x).join(', ') : String(v))]));
}
const confKind = c => c == null ? '' : c >= HIGH ? 'good' : c >= 0.7 ? 'info' : 'warn';

/** Text with the matched surface wrapped in <mark>. */
function highlight(text, needle) {
  text = String(text || '');
  const i = needle ? text.toLowerCase().indexOf(String(needle).toLowerCase()) : -1;
  if (i < 0) return [text];
  return [text.slice(0, i), h('mark', { class: 'ig-hit' }, text.slice(i, i + needle.length)), text.slice(i + needle.length)];
}
/** English text with ⟦unknown⟧ words shown highlighted. */
function markUnknown(text) {
  return String(text || '').split(/(⟦[^⟧]*⟧)/).filter(Boolean).map(p => p.startsWith('⟦') ? h('mark', { class: 'ig-unk', title: 'This word was not understood; its spelling is shown as transliteration' }, p.slice(1, -1)) : p);
}

function summaryTiles(d) {
  const st = d.structured || {}, q = d.quality || {}, sm = q.summary || {}, ev = d.english_view || {}, lg = st.language || {};
  const tile = (label, value, sub, kind) => h('div', { class: 'ig-tile' + (kind ? ' ' + kind : '') }, h('div', { class: 'ig-tl' }, label), h('div', { class: 'ig-tv' }, value), sub ? h('div', { class: 'ig-ts' }, sub) : null);
  const ocr = q.ocr_confidence;
  return h('div', { class: 'ig-tiles' },
    tile('Language', lg.primary || q.language || 'Unknown', lg.confidence != null ? `${pct(lg.confidence)} sure${lg.mixed ? ', mixed languages' : ''}` : '', lg.confidence != null && lg.confidence < 0.6 ? 'warn' : ''),
    tile('Text reading', ocr != null ? pct(ocr) : 'Typed text', q.ocr_method && q.ocr_method !== 'unknown' ? q.ocr_method : 'no scanning needed', ocr != null && ocr < 0.6 ? 'warn' : ''),
    tile('FIR fields found', `${sm.fields_found ?? '–'} of ${sm.fields_total ?? '–'}`, (sm.critical_fields_missing || []).length ? 'Missing: ' + sm.critical_fields_missing.join(', ') : 'All key fields present', (sm.critical_fields_missing || []).length ? 'warn' : ''),
    tile('Items found', String(sm.entities_total ?? d.entity_count ?? 0), `${sm.entities_needing_review ?? 0} need your check`, (sm.entities_needing_review || 0) ? 'warn' : ''),
    tile('Relationships', String(sm.relationships_total ?? (d.relationship_hints || []).length), `${sm.relationships_needing_review ?? 0} need your check`),
    tile('English view', ev.neural ? 'Neural model' : 'Rule engine', ev.coverage != null ? `${pct(ev.coverage)} of words understood` : (ev.warning ? 'see warning' : ''), ev.coverage != null && ev.coverage < 0.6 ? 'warn' : ''));
}

function problemNotices(d) {
  const q = d.quality || {};
  const out = (q.problems || []).map(p => h('div', { class: 'notice ' + (p.severity === 'error' ? 'bad' : p.severity === 'info' ? '' : 'warn') }, h('b', null, p.message), p.action ? ' ' + p.action : ''));
  (q.warnings || []).filter(w => w !== d.english_view?.warning).forEach(w => out.push(notice(w, 'warn')));   // the translation warning is shown on the English tab
  if (q.verification_required) out.push(notice((q.critical_fields || []).length ? 'Some critical fields could not be read. Check the original before verifying: ' + q.critical_fields.join(', ') + '.' : 'A person should check this document against the original before verifying it.', 'warn'));
  return out;
}

// ------------------------------------------------------------------------------------------------ review
function reviewPanel(d, ctx, refresh, close) {
  const canReview = ctx.can('review') && d.status !== 'Verified' && d.status !== 'Rejected';
  const canWrite = ctx.can('write') && d.status !== 'Verified';
  const ents = d.entities || [], rels = d.relationship_hints || [];
  const dec = new Map(ents.map(e => [e.candidate_id, 'pending']));       // pending | accepted | rejected
  const edits = new Map();                                                // candidate_id -> {name, type}
  const rdec = rels.map(() => 'pending');
  const flt = { type: '', review: false, q: '', state: '' };
  const isGraph = e => e.graph_worthy !== false;
  const graphEnts = ents.filter(isGraph), infoEnts = ents.filter(e => !isGraph(e));
  const nameOf = e => edits.get(e.candidate_id)?.name ?? e.text;
  const typeOf = e => edits.get(e.candidate_id)?.type ?? e.type;

  const counter = h('div', { class: 'ig-counter', 'aria-live': 'polite' });
  const entList = h('div', { class: 'ig-list' }), relList = h('div', { class: 'ig-list' });
  const body = h('div', { class: 'ig-body' });

  function updateCounter() {
    const acc = graphEnts.filter(e => dec.get(e.candidate_id) === 'accepted').length, rej = graphEnts.filter(e => dec.get(e.candidate_id) === 'rejected').length;
    const racc = rdec.filter(x => x === 'accepted').length;
    counter.textContent = `${acc} accepted, ${rej} rejected, ${graphEnts.length - acc - rej} undecided. ${racc} of ${rels.length} relationships accepted.`;
  }
  const relOk = r => dec.get(r.source_candidate_id) === 'accepted' && dec.get(r.target_candidate_id) === 'accepted';

  function decide(e, v) { dec.set(e.candidate_id, dec.get(e.candidate_id) === v ? 'pending' : v); if (dec.get(e.candidate_id) !== 'accepted') rels.forEach((r, i) => { if (rdec[i] === 'accepted' && !relOk(r)) rdec[i] = 'pending'; }); drawEnts(); drawRels(); }

  function entCard(e) {
    const state = dec.get(e.candidate_id), ed = edits.get(e.candidate_id);
    const editBox = h('div', { class: 'ig-edit', style: 'display:none' });
    const nameIn = h('input', { value: nameOf(e), 'aria-label': 'Corrected text' });
    const typeIn = h('select', { 'aria-label': 'Corrected type' }, [...new Set([typeOf(e), ...ENTITY_TYPES])].map(t => h('option', { value: t, selected: t === typeOf(e) }, t)));
    mount(editBox, field('Text', nameIn), field('Type', typeIn),
      h('button', { class: 'sm primary', onclick: () => {
        const v = nameIn.value.trim(); if (!v) return toast('The text cannot be empty', 'bad');
        if (v !== e.text || typeIn.value !== e.type) edits.set(e.candidate_id, { name: v, type: typeIn.value }); else edits.delete(e.candidate_id);
        dec.set(e.candidate_id, 'accepted'); drawEnts(); drawRels();
      } }, 'Save and accept'),
      h('button', { class: 'sm', onclick: () => { editBox.style.display = 'none'; } }, 'Cancel'));
    return h('div', { class: `ig-item ig-${state}${e.needs_review ? ' ig-review' : ''}`, 'data-cid': e.candidate_id },
      h('div', { class: 'ig-head' },
        h('b', { class: 'ig-name' }, nameOf(e)), ed ? chip('edited', 'info') : null,
        chip(e.plain_type || typeOf(e), ''), e.role && e.role !== 'FIR_NUMBER' ? chip(String(e.role).replace(/_/g, ' ').toLowerCase(), 'info') : null,
        chip(pct(e.confidence) + ' sure', confKind(e.confidence)), e.needs_review ? chip('please check', 'warn') : null,
        (e.flags || []).map(f => chip(String(f).replace(/_/g, ' '), 'warn'))),
      e.normalized && e.normalized !== e.text ? h('div', { class: 'small muted' }, 'Stored as: ', h('code', null, e.normalized)) : null,
      h('div', { class: 'ig-why small' }, h('b', null, 'Why: '), e.reason || 'Found in the document.'),
      e.evidence ? h('blockquote', { class: 'ig-ev small' }, ...highlight(e.evidence, e.surface || e.text)) : null,
      canReview ? h('div', { class: 'row ig-actions' },
        h('button', { class: 'sm' + (state === 'accepted' ? ' primary' : ''), 'aria-pressed': state === 'accepted', onclick: () => decide(e, 'accepted') }, state === 'accepted' ? 'Accepted' : 'Accept'),
        h('button', { class: 'sm' + (state === 'rejected' ? ' danger' : ''), 'aria-pressed': state === 'rejected', onclick: () => decide(e, 'rejected') }, state === 'rejected' ? 'Rejected' : 'Reject'),
        h('button', { class: 'sm', onclick: () => { editBox.style.display = editBox.style.display === 'none' ? '' : 'none'; } }, 'Edit')) : null,
      editBox);
  }

  function visible(e) {
    if (flt.type && (e.plain_type || e.type) !== flt.type) return false;
    if (flt.review && !e.needs_review) return false;
    if (flt.state && dec.get(e.candidate_id) !== flt.state) return false;
    if (flt.q && !(`${nameOf(e)} ${e.text} ${e.role || ''} ${e.normalized || ''}`).toLowerCase().includes(flt.q.toLowerCase())) return false;
    return true;
  }
  function drawEnts() {
    const shown = graphEnts.filter(visible);
    mount(entList, shown.length ? shown.map(entCard) : empty(graphEnts.length ? 'No items match these filters.' : 'No entities were found in this document.'));
    updateCounter();
  }

  function relCard(r, i) {
    const ok = relOk(r), state = rdec[i];
    const missing = !r.source_candidate_id || !r.target_candidate_id;
    return h('div', { class: `ig-item ig-${state}${r.needs_review ? ' ig-review' : ''}`, 'data-ridx': i },
      h('div', { class: 'ig-head' }, h('b', null, r.source), chip(String(r.relation).replace(/_/g, ' ').toLowerCase(), 'info'), h('b', null, r.target), chip(pct(r.confidence) + ' sure', confKind(r.confidence)), r.needs_review ? chip('please check', 'warn') : null),
      r.rationale ? h('div', { class: 'ig-why small' }, h('b', null, 'Why: '), r.rationale) : null,
      r.evidence ? h('blockquote', { class: 'ig-ev small' }, r.evidence) : null,
      canReview ? h('div', { class: 'row ig-actions' },
        h('button', { class: 'sm' + (state === 'accepted' ? ' primary' : ''), disabled: !ok && state !== 'accepted', title: ok ? '' : (missing ? 'One end of this link is not an item in this document' : 'Accept both items first'), onclick: () => { rdec[i] = state === 'accepted' ? 'pending' : 'accepted'; drawRels(); } }, state === 'accepted' ? 'Accepted' : 'Accept'),
        h('button', { class: 'sm' + (state === 'rejected' ? ' danger' : ''), onclick: () => { rdec[i] = state === 'rejected' ? 'pending' : 'rejected'; drawRels(); } }, state === 'rejected' ? 'Rejected' : 'Reject'),
        !ok && state !== 'accepted' ? h('span', { class: 'small muted' }, missing ? 'Cannot be accepted: an end is not an item here.' : 'Accept both items to enable.') : null) : null);
  }
  function drawRels() { mount(relList, rels.length ? rels.map(relCard) : empty('No relationships were suggested.')); updateCounter(); }

  // ---- filters + bulk
  const types = [...new Set(graphEnts.map(e => e.plain_type || e.type))].sort();
  const typeSel = h('select', { 'aria-label': 'Filter by type', onchange: ev => { flt.type = ev.target.value; drawEnts(); } }, h('option', { value: '' }, 'All types'), types.map(t => h('option', { value: t }, t)));
  const stateSel = h('select', { 'aria-label': 'Filter by decision', onchange: ev => { flt.state = ev.target.value; drawEnts(); } }, [['', 'Any decision'], ['pending', 'Undecided'], ['accepted', 'Accepted'], ['rejected', 'Rejected']].map(([v, t]) => h('option', { value: v }, t)));
  const revChk = h('input', { type: 'checkbox', 'aria-label': 'Only items needing a check', onchange: ev => { flt.review = ev.target.checked; drawEnts(); } });
  const search = h('input', { type: 'search', placeholder: 'Search items', 'aria-label': 'Search items', oninput: ev => { flt.q = ev.target.value; drawEnts(); } });
  const highItems = () => graphEnts.filter(e => (e.confidence || 0) >= HIGH && !e.needs_review && dec.get(e.candidate_id) === 'pending');
  const bulkHigh = h('button', { onclick: () => {
    const list = highItems(); if (!list.length) return toast('No undecided high-confidence items left', 'bad');
    list.forEach(e => dec.set(e.candidate_id, 'accepted'));
    rels.forEach((r, i) => { if (rdec[i] === 'pending' && relOk(r) && (r.confidence || 0) >= HIGH && !r.needs_review) rdec[i] = 'accepted'; });
    toast(`${list.length} high-confidence item(s) accepted. The rest still need your decision.`, 'good'); drawEnts(); drawRels();
  } }, `Accept high-confidence items (${highItems().length})`);
  const bulkAll = h('button', { onclick: () => {
    graphEnts.forEach(e => { if (dec.get(e.candidate_id) === 'pending') dec.set(e.candidate_id, 'accepted'); });
    rels.forEach((r, i) => { if (rdec[i] === 'pending' && relOk(r)) rdec[i] = 'accepted'; });
    drawEnts(); drawRels();
  } }, 'Accept everything shown');
  const clearAll = h('button', { onclick: () => { graphEnts.forEach(e => dec.set(e.candidate_id, 'pending')); rdec.fill('pending'); edits.clear(); drawEnts(); drawRels(); } }, 'Clear decisions');

  const reviewView = h('div', null,
    canReview ? notice('Nothing goes into the case graph until you accept it and press "Verify document". Items you leave undecided are not added.', '') : null,
    infoEnts.length ? h('details', { class: 'ig-info' }, h('summary', null, `${infoEnts.length} amount/date/section item(s) read from the text (kept with the document, not added to the graph)`),
      h('ul', null, infoEnts.map(e => h('li', { class: 'small' }, h('b', null, e.text), ` (${e.plain_type || e.type}${e.normalized && e.normalized !== e.text ? ', stored as ' + e.normalized : ''}) `, h('span', { class: 'muted' }, e.reason || ''))))) : null,
    h('div', { class: 'row ig-filters center' }, field('Type', typeSel), field('Decision', stateSel), field('Search', search), h('label', { class: 'ig-check' }, revChk, 'Only items to check')),
    canReview ? h('div', { class: 'row ig-bulk' }, bulkHigh, bulkAll, clearAll) : null,
    h('h4', null, `Entities (${graphEnts.length})`), entList,
    h('h4', null, `Relationships (${rels.length})`), relList);

  // ---- other views
  const st = d.structured || {}, q = d.quality || {}, fs = q.field_status || {}, ev = d.english_view || {};
  const fieldsView = () => {
    const rows = Object.entries(fs);
    if (!rows.length) return kv([['FIR number', st.fir_number], ['District', st.district], ['Police station', st.police_station], ['FIR date', st.fir_date], ['Complainant', st.complainant]]);
    return h('div', { class: 'tw' }, h('table', null, h('thead', null, h('tr', null, ['Field', 'Value', 'Status', 'Sure', 'Where it came from'].map(x => h('th', null, x)))),
      h('tbody', null, rows.map(([k, f]) => h('tr', null, h('td', null, f.label || k), h('td', null, f.value || '–'),
        h('td', null, chip(f.status === 'recovered' ? 'read' : f.status === 'needs_verification' ? 'not found' : String(f.status).replace(/_/g, ' '), f.status === 'recovered' ? 'good' : 'warn')),
        h('td', null, pct(f.confidence)), h('td', { class: 'small muted' }, f.evidence ? `"${f.evidence}"` : '–'))))));
  };
  const englishView = () => {
    const sents = ev.sentences || [];
    if (!sents.length && !ev.text) return empty('No English view is available for this document.');
    const isEn = (st.language?.code === 'en') || (ev.source_language === 'English' && !(ev.quality_counts?.['rule-based'] || ev.quality_counts?.glossary));
    const unk = ev.unknown_words || [];
    return h('div', null,
      isEn ? notice('This document is already in English, so no translation was needed.', 'good') : null,
      h('div', { class: 'row center small' }, chip(ev.neural ? 'Neural model' : 'Rule engine (not a neural model)', ev.neural ? 'good' : 'info'), ev.engine ? h('span', { class: 'muted' }, ev.engine) : null,
        ev.confidence != null ? chip(`${pct(ev.confidence)} overall confidence`, confKind(ev.confidence)) : null, ev.coverage != null ? chip(`${pct(ev.coverage)} of words understood`, confKind(ev.coverage)) : null),
      ev.warning ? notice(ev.warning, 'warn') : null,
      unk.length ? h('div', { class: 'ig-unklist small' }, h('b', null, 'Words not understood: '), unk.map(u => h('mark', { class: 'ig-unk' }, `${u.word}${u.count > 1 ? ' ×' + u.count : ''}`)), ' Please read these in the original.') : null,
      h('div', { class: 'ig-side' },
        h('div', { class: 'ig-side-h' }, h('div', null, 'Original'), h('div', null, 'English')),
        sents.map(s => h('div', { class: 'ig-srow ig-q-' + s.quality },
          h('div', { class: 'ig-src', lang: s.language_code || undefined }, s.source),
          h('div', { class: 'ig-en' }, h('div', null, ...markUnknown(s.english)), h('div', { class: 'ig-qm small' }, chip(QUALITY_TEXT[s.quality] || s.quality, QUALITY_KIND[s.quality] ?? ''), ` ${pct(s.confidence)} sure`, s.unknown?.length ? ` · ${s.unknown.length} unknown word(s)` : ''))))));
  };
  const sections = { review: () => reviewView, fields: fieldsView, en: englishView, text: () => h('pre', { class: 'txt' }, d.text_preview || '(no text)') };
  const t = tabs([['review', `Review (${graphEnts.length + rels.length})`], ['fields', 'FIR fields'], ['en', 'Original | English'], ['text', 'Source text']], 'review', id => mount(body, sections[id]()));
  mount(body, sections.review()); drawEnts(); drawRels();

  // ---- submit
  async function verify() {
    const accepted = graphEnts.filter(e => dec.get(e.candidate_id) === 'accepted');
    if (!accepted.length) return toast('Accept at least one item before verifying', 'bad');
    const entities = accepted.map(e => {
      const ed = edits.get(e.candidate_id);
      const a = { candidate_id: e.candidate_id, candidate_external_id: e.candidate_external_id, action: 'create', entity_type: ed?.type || e.type, name: ed?.name || e.text, role: e.role || '', confidence: e.confidence };
      if (ed) { a.candidate_external_id = ''; a.attributes = { edited: true, original_text: e.text }; }
      return a;
    });
    const relationships = rels.map((r, i) => rdec[i] === 'accepted' && relOk(r)
      ? { action: 'create', source_candidate_id: r.source_candidate_id, target_candidate_id: r.target_candidate_id, relation_type: r.relation, confidence: r.confidence, evidence: r.evidence || '', rationale: r.rationale || '', source_ref: r.source_ref || d.document_id }
      : { action: 'ignore', source_candidate_id: r.source_candidate_id, target_candidate_id: r.target_candidate_id, relation_type: r.relation });
    try {
      const r = await api(`/documents/${encodeURIComponent(d.document_id)}/review`, { method: 'POST', body: { status: 'Verified', entities, relationships } });
      toast(`Verified. ${r.accepted_entities} item(s) and ${r.relationships_created} relationship(s) added to the graph.`, 'good');
      if (ctx.refresh) await ctx.refresh(); await refresh();
    } catch (ex) { toast(friendlyError(ex.message, ex.status), 'bad'); }
  }
  async function reject() {
    try { await api(`/documents/${encodeURIComponent(d.document_id)}/review`, { method: 'POST', body: { status: 'Rejected', entities: [], relationships: [] } }); toast('Document rejected. Nothing was added to the graph.', 'good'); if (ctx.refresh) await ctx.refresh(); await refresh(); }
    catch (ex) { toast(friendlyError(ex.message, ex.status), 'bad'); }
  }
  async function reprocess() {
    try { await api(`/documents/${encodeURIComponent(d.document_id)}/reprocess`, { method: 'POST' }); toast('Document analysed again. Earlier decisions were cleared.', 'good'); await refresh(); }
    catch (ex) { toast(friendlyError(ex.message, ex.status), 'bad'); }
  }

  const related = h('div');
  if (ctx.can('analyze')) api(`/documents/${encodeURIComponent(d.document_id)}/related`).then(r => {
    if (r.related_firs?.length) mount(related, h('h4', { style: 'margin-top:1rem' }, 'Related FIRs (shared identifiers)'), table([{ k: 'fir_number', label: 'FIR' }, { k: 'match_strength', label: 'Strength' }, { label: 'Shared', render: x => (x.reasons || []).map(y => `${y.type}: ${(y.shared || []).join(', ')}`).join(' | ') }], r.related_firs));
  }).catch(() => {});

  return h('div', { class: 'card ig-panel', style: 'margin-top:1rem' },
    h('div', { class: 'row center' }, h('h3', { style: 'margin:0;flex:1' }, `${d.filename} `, chip(d.status, d.status === 'Verified' ? 'good' : d.status === 'Rejected' ? 'bad' : 'warn')), h('button', { class: 'sm', onclick: close }, 'Close')),
    h('div', { class: 'small muted' }, 'SHA-256 ', (d.sha256 || '').slice(0, 16) + '…', d.evidence_id ? ` · evidence ${d.evidence_id}` : ''),
    summaryTiles(d), problemNotices(d), t, body,
    h('div', { class: 'row ig-foot' }, counter),
    canReview ? h('div', { class: 'row ig-submit' },
      h('button', { class: 'primary', onclick: verify }, 'Verify document'),
      h('button', { class: 'danger', onclick: reject }, 'Reject document'),
      canWrite ? h('button', { onclick: reprocess }, 'Analyse again') : null)
      : notice(d.status === 'Verified' ? `Verified${d.review_actor ? ' by ' + d.review_actor : ''}${d.reviewed_at ? ' on ' + fmtTime(d.reviewed_at) : ''}.` : d.status === 'Rejected' ? 'This document was rejected.' : 'You can view this document but not review it.', d.status === 'Verified' ? 'good' : ''),
    related);
}
