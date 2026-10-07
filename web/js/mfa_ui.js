// Two-step sign-in (authenticator app) UI pieces.
//   mfaPanel(ctx)        -> DOM node for the Account dialog: status, turn on (wizard), turn off, new recovery codes.
//   mfaPolicyCard(ctx)   -> DOM node for the Admin portal: which roles must use it.
// The QR code is an SVG made by the server (no third-party code, no network); it is inserted as DOM, never as HTML.
import { h, api, mount, session, toast, chip, notice, download, fmtTime } from './lib.js';

const ROLE_LABEL = {
  admin: 'Admin', supervisor: 'Supervisor', investigator: 'Investigator', auditor: 'Auditor', demo: 'Demo',
};
const ROLE_ORDER = ['admin', 'supervisor', 'investigator', 'auditor', 'demo'];

// Parse the server's SVG and import the element. Scripts, foreign content and on* handlers are dropped.
export function svgNode(text) {
  if (!text) return null;
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  const root = doc.documentElement;
  if (!root || root.nodeName.toLowerCase() !== 'svg' || doc.querySelector('parsererror')) return null;
  for (const bad of Array.from(root.querySelectorAll('script, foreignObject, image, a'))) bad.remove();
  for (const el of [root, ...root.querySelectorAll('*')]) for (const a of Array.from(el.attributes)) if (/^on/i.test(a.name)) el.removeAttribute(a.name);
  const node = document.importNode(root, true);
  node.setAttribute('role', 'img');
  node.setAttribute('aria-label', 'QR code to scan with your authenticator app');
  if (!node.getAttribute('viewBox') && node.getAttribute('width') && node.getAttribute('height')) node.setAttribute('viewBox', `0 0 ${parseFloat(node.getAttribute('width'))} ${parseFloat(node.getAttribute('height'))}`);
  node.removeAttribute('width'); node.removeAttribute('height');
  node.classList.add('mf-qr-svg');
  return node;
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied', 'good'); }
  catch { toast('Could not copy. Select the text and copy it by hand.', 'bad'); }
}

export const groups = s => (s || '').replace(/(.{4})/g, '$1 ').trim();

// Show ten one-time codes with copy / download and a "saved" checkbox that unlocks the Done button.
export function recoveryBlock(codes, onDone, doneLabel = 'Done') {
  const text = codes.join('\n');
  const saved = h('input', { type: 'checkbox', id: 'mf-saved-' + Math.random().toString(36).slice(2, 7) });
  const done = h('button', { class: 'primary', disabled: true, onclick: () => onDone() }, doneLabel);
  saved.addEventListener('change', () => { done.disabled = !saved.checked; });
  return h('div', { class: 'mf-recovery' },
    notice('Save these recovery codes now. They are shown only once. Each works one time if you lose your phone.', 'warn'),
    h('ul', { class: 'mf-codes', 'aria-label': 'Recovery codes' }, codes.map(c => h('li', { class: 'mf-code' }, c))),
    h('div', { class: 'mf-row' },
      h('button', { type: 'button', onclick: () => copyText(text) }, 'Copy codes'),
      h('button', { type: 'button', onclick: () => download(new Blob([`DARK CRIMENET recovery codes\nEach code works once.\n\n${text}\n`], { type: 'text/plain' }), 'recovery-codes.txt') }, 'Download as file')),
    h('label', { class: 'mf-check', for: saved.id }, saved, ' I have saved these codes somewhere safe (not on the same phone).'),
    done);
}

export function mfaPanel(ctx) {
  const root = h('div', { class: 'mf-panel' });
  const err = h('div', { class: 'err', role: 'alert' });
  const say = ex => { err.textContent = ex && ex.message ? ex.message : ''; };

  async function load() {
    say(null);
    let st;
    try { st = await api('/auth/mfa/status'); }
    catch (ex) { mount(root, h('h4', null, 'Two-step sign-in'), notice(ex.message, 'warn')); return; }
    if (st.enabled) showOn(st); else showOff(st);
  }

  function showOff(st) {
    mount(root,
      h('h4', null, 'Two-step sign-in ', chip('off', 'warn')),
      h('p', { class: 'muted small' }, 'After your password you also type a 6-digit code from an app on your phone. A stolen password alone then cannot open your account. It works without internet or SMS.'),
      st.required ? notice('Your role must use two-step sign-in. Turn it on now.', 'warn') : null,
      h('button', { class: 'primary', onclick: () => wizard() }, 'Turn on two-step sign-in'), err);
  }

  function showOn(st) {
    const rec = st.recovery_left;
    mount(root,
      h('h4', null, 'Two-step sign-in ', chip('on', 'good')),
      h('p', { class: 'muted small' }, `Turned on ${st.enrolled_at ? fmtTime(st.enrolled_at) : ''}. You have ${rec} recovery code${rec === 1 ? '' : 's'} left.`),
      rec <= 2 ? notice('Only a few recovery codes are left. Make new ones below.', 'warn') : null,
      !st.session_has_otp ? notice('Sign out and sign in again with your code before changing these settings.', 'warn') : null,
      h('div', { class: 'mf-row' },
        h('button', { onclick: () => codeForm('Make new recovery codes', 'The old codes stop working. Enter your password and a code from your app.', 'Make new codes', '/auth/mfa/recovery-codes/regenerate', r => showCodes(r.recovery_codes)) }, 'New recovery codes'),
        st.required ? h('span', { class: 'muted small' }, 'Your role must use two-step sign-in, so it cannot be turned off.')
          : h('button', { class: 'danger', onclick: () => codeForm('Turn off two-step sign-in', 'Your account will only need the password. Enter your password and a code from your app.', 'Turn off', '/auth/mfa/disable', r => { if (r.access_token) session.token = r.access_token; toast('Two-step sign-in is off', 'good'); load(); }) }, 'Turn off')),
      err);
  }

  // password + code form used by "new recovery codes" and "turn off"
  function codeForm(title, help, label, path, onOk) {
    say(null);
    const pw = h('input', { type: 'password', autocomplete: 'current-password' });
    const code = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 12, placeholder: '6-digit code' });
    mount(root, h('h4', null, title), h('p', { class: 'muted small' }, help),
      h('form', { class: 'grid', onsubmit: async e => {
        e.preventDefault(); say(null);
        try { onOk(await api(path, { method: 'POST', body: { password: pw.value, code: code.value.trim() } })); }
        catch (ex) { say(ex); }
      } },
        h('label', null, 'Your password', pw), h('label', null, 'Code from your app (or a recovery code)', code),
        h('div', { class: 'mf-row' }, h('button', { class: 'primary', type: 'submit' }, label), h('button', { type: 'button', onclick: load }, 'Cancel'))), err);
  }

  function showCodes(codes) {
    mount(root, h('h4', null, 'Your recovery codes'), recoveryBlock(codes, load), err);
  }

  // ---- turn-on wizard: password -> scan/type secret -> confirm code -> recovery codes
  function wizard() {
    say(null);
    const pw = h('input', { type: 'password', autocomplete: 'current-password' });
    mount(root, h('h4', null, 'Turn on two-step sign-in'),
      h('ol', { class: 'mf-steps' }, h('li', { class: 'on' }, 'Password'), h('li', null, 'Scan'), h('li', null, 'Confirm'), h('li', null, 'Recovery codes')),
      h('p', { class: 'muted small' }, 'You need an authenticator app on your phone, for example Google Authenticator, Microsoft Authenticator or Authy.'),
      h('form', { class: 'grid', onsubmit: async e => {
        e.preventDefault(); say(null);
        try { scan(await api('/auth/mfa/enroll/start', { method: 'POST', body: { password: pw.value } })); }
        catch (ex) { say(ex); }
      } }, h('label', null, 'Your password (to make sure it is you)', pw),
        h('div', { class: 'mf-row' }, h('button', { class: 'primary', type: 'submit' }, 'Continue'), h('button', { type: 'button', onclick: load }, 'Cancel'))), err);
  }

  function scan(d) {
    const qr = svgNode(d.qr_svg);
    const code = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 8, placeholder: '6-digit code' });
    mount(root, h('h4', null, 'Scan, then confirm'),
      h('ol', { class: 'mf-steps' }, h('li', null, 'Password'), h('li', { class: 'on' }, 'Scan'), h('li', { class: 'on' }, 'Confirm'), h('li', null, 'Recovery codes')),
      h('p', { class: 'small' }, '1. In your app choose "add account" and scan this square.'),
      qr ? h('div', { class: 'mf-qr' }, qr) : notice('The QR code could not be shown. Type the key below into your app instead.', 'warn'),
      h('p', { class: 'small' }, 'Cannot scan? Type this key into the app (time-based, 6 digits):'),
      h('div', { class: 'mf-secret' }, h('code', null, groups(d.secret)), h('button', { type: 'button', class: 'sm', onclick: () => copyText(d.secret) }, 'Copy key')),
      h('form', { class: 'grid', onsubmit: async e => {
        e.preventDefault(); say(null);
        try {
          const r = await api('/auth/mfa/enroll/confirm', { method: 'POST', body: { code: code.value.trim() } });
          if (r.access_token) session.token = r.access_token;  // other sessions were signed out; this one continues
          mount(root, h('h4', null, 'Two-step sign-in is on'),
            h('ol', { class: 'mf-steps' }, h('li', null, 'Password'), h('li', null, 'Scan'), h('li', null, 'Confirm'), h('li', { class: 'on' }, 'Recovery codes')),
            recoveryBlock(r.recovery_codes, load), err);
        } catch (ex) { say(ex); }
      } }, h('label', null, '2. Type the 6-digit code your app shows now', code),
        h('div', { class: 'mf-row' }, h('button', { class: 'primary', type: 'submit' }, 'Confirm and turn on'), h('button', { type: 'button', onclick: load }, 'Cancel'))), err);
  }

  load();
  return root;
}

// ---------------------------------------------------------------- admin: which roles must use it
export function mfaPolicyCard(ctx, onChange) {
  const box = h('div', { class: 'mf-policy' });
  async function load() {
    let d;
    try { d = await api('/admin/mfa/policy'); }
    catch (ex) { mount(box, notice(ex.message, 'warn')); return; }
    mount(box,
      h('p', { class: 'muted small' }, 'Two-step sign-in asks for a 6-digit code from a phone app after the password. Switch a role on and everyone in it must set it up the next time they sign in. Anyone else may still turn it on for themselves.'),
      h('ul', { class: 'mf-roles' }, ROLE_ORDER.filter(r => d.roles[r]).map(r => {
        const p = d.roles[r];
        const box2 = h('input', { type: 'checkbox', role: 'switch', checked: p.required, disabled: p.locked, 'aria-label': `Two-step sign-in required for ${ROLE_LABEL[r] || r}`,
          onchange: async e => {
            const want = e.target.checked;
            try { await api('/admin/mfa/policy', { method: 'PUT', body: { [r]: want } }); toast(want ? `${ROLE_LABEL[r]} must now use two-step sign-in` : `${ROLE_LABEL[r]} no longer must use it`, 'good'); if (onChange) onChange(); }
            catch (ex) { toast(ex.message, 'bad'); }
            load();
          } });
        return h('li', { class: 'mf-role' },
          h('label', { class: 'mf-role-name' }, box2, ' ', ROLE_LABEL[r] || r),
          h('span', { class: 'muted small' }, p.locked ? 'Never required for the demo role.' : `${p.enrolled} of ${p.users} people have it on` + (p.source === 'admin' ? `. Set by ${p.updated_by || 'an admin'}.` : p.source === 'environment' ? '. From the server settings.' : '. Default setting.')),
          chip(p.required ? 'required' : 'optional', p.required ? 'good' : ''));
      })),
      h('p', { class: 'muted small' }, 'Lost phone? Use "Reset two-step" in the Users list. The person is signed out and sets it up again.'));
  }
  load();
  return h('div', { class: 'card' }, h('h3', null, 'Two-step sign-in'), box);
}

// Reset button for one user row (Users table). Returns a DOM node.
export function mfaResetButton(u, reload) {
  return h('button', { class: 'sm', title: 'Switch off this person\'s two-step sign-in and sign them out. They set it up again next time if their role requires it.',
    onclick: async () => {
      if (!window.confirm(`Reset two-step sign-in for ${u.username}? They will be signed out.`)) return;
      try {
        const r = await api(`/admin/users/${encodeURIComponent(u.username)}/mfa-reset`, { method: 'POST' });
        toast(r.must_enrol_again ? 'Reset. They must set it up again at next sign-in.' : 'Reset. Two-step sign-in is off for them.', 'good'); reload();
      } catch (ex) { toast(ex.message, 'bad'); }
    } }, 'Reset two-step');
}
