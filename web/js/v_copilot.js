import { h, api, mount, field, notice, toast, chip } from './lib.js';

export async function render(root, ctx) {
  const cn = ctx.caseNumber;
  const sug = cn ? await api('/copilot/suggestions', { params: { case_number: cn } }).catch(() => ({ suggestions: [] })) : { suggestions: [] };
  const q = h('input', { placeholder: 'Ask about this case, e.g. "Who is most central?"', style: 'flex:1;min-width:280px', maxlength: 1000 });
  const log = h('div');
  async function ask(text) {
    text = (text || q.value).trim(); if (!text) return;
    if (!cn) return toast('Select a case first', 'bad');
    q.value = '';
    const pending = h('div', { class: 'notice' }, h('b', null, 'You: '), text, h('div', { class: 'muted small' }, 'Thinking…'));
    log.prepend(pending);
    try {
      const r = await api('/copilot', { method: 'POST', body: { query: text, case_number: cn } });
      pending.replaceChildren(h('div', null, h('b', null, 'You: '), text), h('div', { style: 'white-space:pre-wrap;margin-top:.4rem' }, r.answer),
        (r.sources || []).length ? h('div', { class: 'small muted', style: 'margin-top:.4rem' }, 'Sources: ', r.sources.map(s => chip(String(s)))) : null,
        r.disclaimer ? h('div', { class: 'small muted', style: 'margin-top:.3rem' }, r.disclaimer) : null);
    } catch (ex) { pending.replaceChildren(h('b', null, 'You: '), text, notice(ex.message, 'bad')); }
  }
  mount(root, h('p', { class: 'muted' }, 'The copilot answers only from verified case data and cites its sources. It never infers guilt.'),
    (sug.suggestions || []).length ? h('div', { class: 'row' }, sug.suggestions.map(s => h('button', { class: 'sm', title: s.reason, onclick: () => ask(s.question) }, s.question))) : null,
    h('div', { class: 'row center' }, q, h('button', { class: 'primary', onclick: () => ask() }, 'Ask')), log);
  q.addEventListener('keydown', e => { if (e.key === 'Enter') ask(); });
}
