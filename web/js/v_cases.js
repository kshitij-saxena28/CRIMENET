import { h, api, mount, field, table, tabs, notice, empty, chip, toast, fmtTime, fmtNum } from './lib.js';

export async function render(root, ctx) {
  const cn = ctx.caseNumber;
  const listBox = h('div');
  const cases = await api('/cases'); ctx.cases = cases;
  mount(listBox, table([{ k: 'case_number', label: 'Case' }, { k: 'title', label: 'Title' }, { label: 'Status', render: c => chip(c.status, c.status === 'Active' ? 'good' : '') }, { k: 'visibility', label: 'Visibility' }, { k: 'created_by', label: 'Created by' }, { label: 'Created', render: c => fmtTime(c.created_at) }], cases,
    { onRow: c => { ctx.caseNumber = c.case_number; ctx.refresh().then(() => ctx.go('cases', true)); }, emptyText: 'No cases yet.' }));

  const num = h('input', { placeholder: 'CASE-2026-XXX', style: 'width:200px' }), title = h('input', { placeholder: 'Title' }), sum = h('input', { placeholder: 'Short summary', style: 'min-width:260px' });
  const create = ctx.can('write') ? h('div', { class: 'card' }, h('h3', null, 'New workbench'), h('div', { class: 'row' }, field('Case number', num), field('Title', title), field('Summary', sum), h('button', { class: 'primary', onclick: async () => {
    if (!num.value.trim() || !title.value.trim()) { toast('Enter a case number and a title first.', 'bad'); return; }
    try { const r = await api('/workbench/new', { method: 'POST', body: { case_number: num.value, title: title.value, summary: sum.value, status: 'Active' } }); toast('Workbench created', 'good'); ctx.caseNumber = num.value.trim().toUpperCase(); await ctx.refresh(); ctx.go('cases', true); } catch (ex) { toast(ex.message, 'bad'); }
  } }, 'Create'))) : null;

  mount(root, h('div', { class: 'card' }, h('h3', null, 'Accessible cases'), h('p', { class: 'muted small' }, 'Click a row to make it the active case.'), listBox), h('div', { style: 'height:1rem' }), create);
  if (!cn) return;
  const ws = await api(`/cases/${encodeURIComponent(cn)}/workspace`);
  const [tasks, notes] = await Promise.all([api('/tasks', { params: { case_number: cn } }), api('/notes', { params: { case_number: cn } })]);
  const taskBox = h('div'), noteBox = h('div');
  const drawTasks = t => mount(taskBox, table([{ k: 'id', label: '#' }, { k: 'title', label: 'Task' }, { k: 'assignee', label: 'Assignee' }, { k: 'status', label: 'Status' }], t, { emptyText: 'No tasks.' }));
  const drawNotes = n => mount(noteBox, n.length ? n.map(x => h('div', { class: 'notice' }, h('div', { class: 'small muted' }, `${x.author} · ${fmtTime(x.created_at)}`), h('div', { style: 'white-space:pre-wrap' }, x.body))) : empty('No notes yet.'));
  drawTasks(tasks); drawNotes(notes);
  const tt = h('input', { placeholder: 'New task', style: 'min-width:240px' }), ta = h('input', { placeholder: 'Assignee (optional)' });
  const nb = h('textarea', { placeholder: 'Write a case note…', maxlength: 5000 });
  mount(root.appendChild(h('div', { style: 'margin-top:1rem' })), h('div', { class: 'card' }, h('h3', null, `Workbench ${cn}`), h('p', { class: 'muted small' }, ws.case.summary || 'No summary.'),
    h('div', { class: 'grid g4' }, [['Entities', ws.entities.length], ['Relationships', ws.relationships.length], ['Events', ws.events.length], ['Evidence', (ws.evidence || []).length], ['Documents', ws.documents.length], ['Alerts', ws.alerts.length]].map(([l, v]) => h('div', null, h('div', { class: 'muted small' }, l), h('b', null, fmtNum(v)))))),
    h('div', { class: 'grid g2', style: 'margin-top:1rem' },
      h('div', { class: 'card' }, h('h3', null, 'Tasks'), taskBox, ctx.can('assign') ? h('div', { class: 'row', style: 'margin-top:.7rem' }, tt, ta, h('button', { onclick: async () => {
        if (!tt.value.trim()) return; try { await api('/tasks', { method: 'POST', body: { case_number: cn, title: tt.value.trim(), assignee: ta.value.trim() } }); tt.value = ''; drawTasks(await api('/tasks', { params: { case_number: cn } })); } catch (ex) { toast(ex.message, 'bad'); }
      } }, 'Add task')) : null),
      h('div', { class: 'card' }, h('h3', null, 'Notes'), ctx.can('note') ? h('div', null, nb, h('div', { class: 'row', style: 'margin-top:.4rem' }, h('button', { onclick: async () => {
        if (!nb.value.trim()) return; try { await api('/notes', { method: 'POST', body: { case_number: cn, body: nb.value } }); nb.value = ''; drawNotes(await api('/notes', { params: { case_number: cn } })); } catch (ex) { toast(ex.message, 'bad'); }
      } }, 'Add note'))) : null, noteBox)));
}
