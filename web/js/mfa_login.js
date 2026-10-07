// The second step of signing in: enter a 6-digit code, or (for roles that must use two-step sign-in and have not set it up
// yet) set it up right here. Everything is DOM built through h(); the QR code is a server SVG parsed by svgNode().
import { h, api, mount } from './lib.js';
import { svgNode, recoveryBlock, groups, copyText } from './mfa_ui.js';

/**
 * @param first  the /auth/login reply: {mfa_required, mfa_token, methods} or {mfa_setup_required, mfa_token}
 * @param finish called with the full login payload (access_token, username, role, permissions) when done
 * @param back   go back to the password step
 */
export function mfaLogin(first, { finish, back }) {
  const root = h('div', { class: 'au-form mfl' });
  const err = h('div', { class: 'au-msg bad', role: 'alert' });
  const say = ex => { err.textContent = ex ? (ex.status === 429 ? 'Too many wrong codes. Wait a few minutes, then try again.' : ex.message || 'That did not work') : ''; };
  let token = first.mfa_token;

  function verify() {
    say(null);
    const code = h('input', { inputmode: 'text', autocomplete: 'one-time-code', maxlength: 24, placeholder: '6-digit code', required: true, id: 'mfa-code', 'aria-label': 'Verification code' });
    const btn = h('button', { class: 'primary', type: 'submit' }, 'Verify and sign in');
    mount(root,
      h('div', { class: 'mfl-head' }, h('b', null, 'Two-step sign-in'), h('span', null, 'Open your authenticator app and type the current 6-digit code. Lost your phone? Type one of your recovery codes instead.')),
      h('form', { class: 'au-form-inner', onsubmit: async e => {
        e.preventDefault(); say(null); btn.disabled = true;
        try { finish(await api('/auth/mfa/verify', { method: 'POST', body: { mfa_token: token, code: code.value.trim() } })); }
        catch (ex) { say(ex); btn.disabled = false; code.select(); }
      } }, h('label', null, 'Code', code), btn, h('button', { type: 'button', class: 'linkish', onclick: back }, 'Back to password')), err);
    setTimeout(() => code.focus(), 30);
  }

  async function setup() {
    say(null);
    mount(root, h('div', { class: 'mfl-head' }, h('b', null, 'Set up two-step sign-in'), h('span', null, 'Loading…')));
    let d;
    try { d = await api('/auth/mfa/enroll/start', { method: 'POST', body: { mfa_token: token } }); }
    catch (ex) { mount(root, h('div', { class: 'mfl-head' }, h('b', null, 'Could not start set-up')), err, h('button', { type: 'button', class: 'linkish', onclick: back }, 'Back to password')); say(ex); return; }
    const qr = svgNode(d.qr_svg);
    const code = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 8, placeholder: '6-digit code', required: true, id: 'mfa-code', 'aria-label': 'Verification code' });
    mount(root,
      h('div', { class: 'mfl-head' }, h('b', null, 'Set up two-step sign-in'), h('span', null, 'Your role must use it. It takes a minute and you only do it once.')),
      h('ol', { class: 'mf-steps' }, h('li', { class: 'on' }, 'Scan'), h('li', { class: 'on' }, 'Confirm'), h('li', null, 'Save codes')),
      h('p', { class: 'small' }, '1. In an authenticator app (Google Authenticator, Microsoft Authenticator, Authy) choose "add account" and scan this square.'),
      qr ? h('div', { class: 'mf-qr' }, qr) : h('p', { class: 'small' }, 'The QR code could not be shown. Type the key below into your app.'),
      h('div', { class: 'mf-secret' }, h('code', null, groups(d.secret)), h('button', { type: 'button', class: 'sm', onclick: () => copyText(d.secret) }, 'Copy key')),
      h('form', { class: 'au-form-inner', onsubmit: async e => {
        e.preventDefault(); say(null);
        try {
          const r = await api('/auth/mfa/enroll/confirm', { method: 'POST', body: { mfa_token: token, code: code.value.trim() } });
          mount(root, h('div', { class: 'mfl-head' }, h('b', null, 'Two-step sign-in is on')),
            h('ol', { class: 'mf-steps' }, h('li', null, 'Scan'), h('li', null, 'Confirm'), h('li', { class: 'on' }, 'Save codes')),
            recoveryBlock(r.recovery_codes || [], () => finish(r), 'Continue to DARK CRIMENET'));
        } catch (ex) { say(ex); }
      } }, h('label', null, '2. Type the 6-digit code your app shows now', code), h('button', { class: 'primary', type: 'submit' }, 'Confirm'),
        h('button', { type: 'button', class: 'linkish', onclick: back }, 'Back to password')), err);
    setTimeout(() => code.focus(), 30);
  }

  if (first.mfa_setup_required) setup(); else verify();
  return root;
}
