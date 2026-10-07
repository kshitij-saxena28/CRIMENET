// Demo-data switch for the top bar (demo account and admins only). Returns a DOM node.
//   const node = demoToggle(ctx, async state => { await ctx.refresh(); });
// `state` is the /demo/status payload after every change. node.refresh() re-reads the status.
import { h, api, toast } from './lib.js';

export function demoToggle(ctx, onChange) {
  const role = ctx && ctx.user && ctx.user.role;
  if (role !== 'admin' && role !== 'demo') return h('span', { hidden: true });
  const state = h('span', { class: 'au-demo-state' }, 'Checking…');
  const box = h('input', { type: 'checkbox', role: 'switch', class: 'au-demo-input', 'aria-label': 'Demo data on or off', disabled: true });
  const node = h('label', { class: 'au-demo', title: 'Switch the built-in demonstration data on or off. Real data is never touched.' },
    box, h('span', { class: 'au-demo-track', 'aria-hidden': 'true' }, h('span', { class: 'au-demo-knob' })), h('span', { class: 'au-demo-name' }, 'Demo data'), state);
  let last = null;

  function paint(s) {
    last = s;
    box.checked = !!s.enabled;
    box.disabled = !s.enabled && !s.available;  // production installs cannot load demo data, only remove it
    state.textContent = s.enabled ? `On · ${s.case_count} cases` : (s.available ? 'Off' : 'Not available');
    node.dataset.on = s.enabled ? '1' : '0';
  }
  async function refresh() {
    try { paint(await api('/demo/status')); }
    catch (ex) { box.disabled = true; state.textContent = 'Unavailable'; }
  }
  box.addEventListener('change', async () => {
    const turnOn = box.checked;
    if (!turnOn && !window.confirm('Turn demo data off? Only the demonstration cases and records are removed. Real data is not touched.')) { box.checked = true; return; }
    box.disabled = true; state.textContent = turnOn ? 'Loading demo data…' : 'Removing demo data…';
    try {
      const s = await api(turnOn ? '/demo/enable' : '/demo/disable', { method: 'POST' });
      paint(s);
      toast(s.message || (turnOn ? 'Demo data is on' : 'Demo data is off'), 'good');
      if (onChange) await onChange(s);
    } catch (ex) {
      toast(ex.message, 'bad');
      if (last) paint(last); else await refresh();
    }
  });
  node.refresh = refresh;
  refresh();
  return node;
}
