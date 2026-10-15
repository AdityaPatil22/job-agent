'use strict';

const $ = (selector) => document.querySelector(selector);
const state = { jobs: [], resumes: [], taskId: null, taskPoller: null, lastSyncedAt: null };
const STATUS_LABELS = {
  discovered: 'Discovered', shortlisted: 'Shortlisted', ready: 'Ready to review',
  applied: 'Applied', interview: 'Interview', rejected: 'Rejected', withdrawn: 'Withdrawn',
};
const STATUS_OPTIONS = Object.keys(STATUS_LABELS);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  let payload;
  try { payload = await response.json(); }
  catch { payload = { error: `Unexpected server response (${response.status}).` }; }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}
function toast(message, kind = 'success') {
  const item = document.createElement('div');
  item.className = `toast${kind === 'error' ? ' error' : ''}`;
  item.textContent = message;
  $('#toast-region').append(item);
  window.setTimeout(() => item.remove(), 4000);
}
function text(value, fallback = '—') {
  return value === null || value === undefined || String(value).trim() === '' ? fallback : String(value);
}
function localISODate() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}
function date(value) {
  if (!value) return '—';
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(parsed);
}
function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
function statusBadge(status) {
  return element('span', `status-badge status-${status || 'discovered'}`, STATUS_LABELS[status] || status || 'Discovered');
}
function renderStats() {
  const jobs = state.jobs;
  $('#stat-total').textContent = jobs.length;
  $('#stat-progress').textContent = jobs.filter((job) => ['discovered', 'shortlisted', 'ready'].includes(job.status)).length;
  $('#stat-applied').textContent = jobs.filter((job) => job.status === 'applied').length;
  $('#stat-interviews').textContent = jobs.filter((job) => job.status === 'interview').length;
  const today = localISODate();
  $('#stat-followups').textContent = jobs.filter((job) =>
    job.next_action && job.next_action <= today &&
    !['rejected', 'withdrawn'].includes(job.status)
  ).length;
  $('#jobs-count').textContent = jobs.length;
  $('#last-updated').innerHTML = '<span class="online-dot"></span> Synced with local database · ' + (state.lastSyncedAt ? new Intl.DateTimeFormat(undefined, {hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(state.lastSyncedAt) : 'syncing');
}
function createRow(job) {
  const row = document.createElement('tr');
  const id = element('td', 'id-cell', `#${job.id}`);
  row.append(id);

  const companyCell = element('td', 'role-cell');
  companyCell.append(element('span', 'company-name', text(job.company, 'Unknown company')));
  companyCell.append(element('span', 'role-title', text(job.title, 'Untitled role')));
  if (job.url) {
    const link = element('a', 'url-link', 'Open posting ↗');
    link.href = job.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    companyCell.append(link);
  }
  row.append(companyCell);

  row.append(element('td', 'date-cell', date(job.applied_date)));
  const statusCell = document.createElement('td');
  const statusSelect = document.createElement('select');
  statusSelect.className = 'inline-status';
  statusSelect.setAttribute('aria-label', `Status for ${text(job.company)} — ${text(job.title)}`);
  STATUS_OPTIONS.forEach((status) => statusSelect.add(new Option(STATUS_LABELS[status], status, false, status === job.status)));
  statusSelect.dataset.action = 'status';
  statusSelect.dataset.jobId = String(job.id);
  statusCell.append(statusSelect, statusBadge(job.status));
  row.append(statusCell);

  const nextAction = element('td', 'date-cell', date(job.next_action));
  if (job.next_action && job.next_action < localISODate() && !['rejected', 'withdrawn'].includes(job.status)) {
    nextAction.append(element('span', 'overdue-label', 'Overdue'));
  }
  row.append(nextAction);
  row.append(element('td', 'resume-cell', text(job.resume_used)));

  const actions = element('td', 'actions-cell');
  const group = element('div', 'row-actions');
  const edit = element('button', 'action-link', 'Edit');
  edit.type = 'button'; edit.dataset.action = 'edit'; edit.dataset.jobId = String(job.id);
  group.append(edit);
  const assist = element('button', 'action-link action-apply', 'Assist');
  assist.type = 'button'; assist.dataset.action = 'apply'; assist.dataset.jobId = String(job.id);
  assist.dataset.jobTitle = `${text(job.title, 'Untitled role')} — ${text(job.company, 'Unknown company')}`;
  group.append(assist);
  const remove = element('button', 'action-link action-delete', 'Delete');
  remove.type = 'button'; remove.dataset.action = 'delete'; remove.dataset.jobId = String(job.id);
  remove.dataset.jobTitle = `${text(job.company, 'Unknown company')} — ${text(job.title, 'Untitled role')}`;
  group.append(remove);
  actions.append(group);
  row.append(actions);
  return row;
}
function renderJobs() {
  const body = $('#jobs-table-body');
  body.replaceChildren();
  const query = $('#search-input').value.trim().toLowerCase();
  const status = $('#status-filter').value;
  const filtered = state.jobs.filter((job) => {
    const corpus = [job.id, job.company, job.title, job.location, job.url, job.notes, job.resume_used].filter(Boolean).join(' ').toLowerCase();
    return corpus.includes(query) && (status === 'all' || job.status === status);
  });
  $('#jobs-loading').hidden = true;
  $('#jobs-empty').hidden = state.jobs.length > 0;
  $('#jobs-no-results').hidden = !state.jobs.length || !!filtered.length;
  $('#jobs-table-wrap').hidden = !filtered.length;
  filtered.forEach((job) => body.append(createRow(job)));
}
async function loadJobs() {
  $('#jobs-loading').hidden = false;
  $('#jobs-empty').hidden = true;
  $('#jobs-no-results').hidden = true;
  $('#jobs-table-wrap').hidden = true;
  try {
    const payload = await api('/api/jobs');
    state.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    state.lastSyncedAt = new Date();
    renderStats();
    renderJobs();
  } catch (error) {
    $('#jobs-loading').hidden = true;
    toast(`Could not load applications: ${error.message}`, 'error');
  }
}
async function loadResumes() {
  try {
    const payload = await api('/api/resumes');
    state.resumes = Array.isArray(payload.resumes) ? payload.resumes : [];
  } catch (error) {
    state.resumes = [];
    toast(`Could not load resumes: ${error.message}`, 'error');
  }
  const select = $('#resume-select');
  select.replaceChildren();
  $('#resume-warning').hidden = state.resumes.length > 0;
  $('#start-apply-button').disabled = !state.resumes.length;
  select.add(new Option(state.resumes.length ? 'Choose a resume…' : 'No resume files found', ''));
  state.resumes.forEach((resume) => select.add(new Option(resume, resume)));
}
function openEditDialog(job = null) {
  $('#edit-heading').textContent = job ? 'Edit application' : 'Add application manually';
  $('#edit-eyebrow').textContent = job ? `APPLICATION #${job.id}` : 'NEW APPLICATION';
  $('#edit-job-id').value = job ? job.id : '';
  $('#edit-company').value = job?.company || '';
  $('#edit-title').value = job?.title || '';
  $('#edit-url').value = job?.url || '';
  $('#edit-location').value = job?.location || '';
  $('#edit-status').value = job?.status || 'discovered';
  $('#edit-applied-date').value = job?.applied_date || '';
  $('#edit-next-action').value = job?.next_action || '';
  $('#edit-resume').value = job?.resume_used || '';
  $('#edit-notes').value = job?.notes || '';
  $('#edit-url').required = true;
  $('#edit-dialog').showModal();
}
function closeEditDialog() { $('#edit-dialog').close(); }
async function saveEdit(event) {
  event.preventDefault();
  const id = $('#edit-job-id').value;
  const payload = {
    company: $('#edit-company').value.trim(),
    title: $('#edit-title').value.trim(),
    url: $('#edit-url').value.trim(),
    location: $('#edit-location').value.trim(),
    status: $('#edit-status').value,
    applied_date: $('#edit-applied-date').value,
    next_action: $('#edit-next-action').value,
    resume_used: $('#edit-resume').value.trim(),
    notes: $('#edit-notes').value.trim(),
  };
  if (!payload.company || !payload.title || !payload.url) {
    toast('Company, role and URL are required.', 'error');
    return;
  }
  const button = $('#save-edit');
  button.disabled = true;
  try {
    if (id) {
      await api(`/api/jobs/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
      toast('Application updated.');
    } else {
      // Manual entries still go through the standard add-job parsing/deduplication path.
      const response = await api('/api/actions/add-manual-job', { method: 'POST', body: JSON.stringify(payload) });
      if (response.job) toast('Application added.');
    }
    closeEditDialog();
    await loadJobs();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}
function setTaskStatus(status) {
  const node = $('#task-status');
  node.className = `task-status status-${status || 'idle'}`;
  node.textContent = ({ running: 'Running', succeeded: 'Completed', failed: 'Failed', idle: 'Idle' })[status] || status;
  $('#activity').hidden = !['running', 'failed'].includes(status);
}
function renderTask(task) {
  if (!task) return;
  $('#terminal-task-name').textContent = `${task.label || 'job-agent'} · ${task.id}`;
  $('#terminal-output').textContent = task.output || '(No output yet.)';
  $('#terminal-output').scrollTop = $('#terminal-output').scrollHeight;
  setTaskStatus(task.status);
}
async function monitorTask(taskId) {
  state.taskId = taskId;
  if (state.taskPoller) window.clearInterval(state.taskPoller);
  async function update() {
    try {
      const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
      renderTask(task);
      if (task.status !== 'running') {
        window.clearInterval(state.taskPoller);
        state.taskPoller = null;
        state.taskId = null;
        await loadJobs();
        toast(task.status === 'succeeded' ? `${task.label} completed.` : `${task.label} failed. Check command activity.`, task.status === 'succeeded' ? 'success' : 'error');
      }
    } catch (error) {
      if (state.taskPoller) window.clearInterval(state.taskPoller);
      state.taskPoller = null;
      toast(`Could not read command output: ${error.message}`, 'error');
    }
  }
  await update();
  if (state.taskId) state.taskPoller = window.setInterval(update, 900);
}
async function startAction(path, payload, label) {
  try {
    const result = await api(path, { method: 'POST', body: JSON.stringify(payload) });
    if (result.task_id) {
      $('#activity').hidden = false;
      toast(`${label} started. If it asks for input, use the terminal running the dashboard.`);
      await monitorTask(result.task_id);
      return true;
    }
    return false;
  } catch (error) {
    toast(error.message, 'error');
    return false;
  }
}

$('#add-job-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = $('#job-url');
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  const url = input.value.trim();
  if (!url) return;
  button.disabled = true;
  button.textContent = 'Adding…';
  const started = await startAction('/api/actions/add-job', { url }, 'Add job');
  button.disabled = false;
  button.textContent = 'Add from URL';
  if (started) input.value = '';
});
$('#manual-add-button').addEventListener('click', () => openEditDialog());
$('#edit-form').addEventListener('submit', saveEdit);
$('#cancel-edit').addEventListener('click', closeEditDialog);
$('#close-edit-dialog').addEventListener('click', closeEditDialog);
$('#edit-dialog').addEventListener('click', (event) => { if (event.target === $('#edit-dialog')) closeEditDialog(); });

$('#jobs-table-body').addEventListener('change', async (event) => {
  const select = event.target.closest('select[data-action="status"]');
  if (!select) return;
  const jobId = Number(select.dataset.jobId);
  select.disabled = true;
  try {
    await api(`/api/jobs/${jobId}`, { method: 'PATCH', body: JSON.stringify({ status: select.value }) });
    await loadJobs();
    toast('Status updated.');
  } catch (error) {
    toast(error.message, 'error');
    await loadJobs();
  } finally { select.disabled = false; }
});
$('#jobs-table-body').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const jobId = Number(button.dataset.jobId);
  if (button.dataset.action === 'edit') {
    const job = state.jobs.find((item) => Number(item.id) === jobId);
    if (job) openEditDialog(job);
  } else if (button.dataset.action === 'apply') {
    await loadResumes();
    $('#apply-job-id').value = jobId;
    $('#apply-job-caption').textContent = `Job #${jobId}: ${button.dataset.jobTitle}. Review every field before submitting.`;
    $('#apply-dialog').showModal();
  } else if (button.dataset.action === 'delete') {
    const label = button.dataset.jobTitle || `Job #${jobId}`;
    if (!window.confirm(`Delete this application?\n\n${label}\n\nThis cannot be undone.`)) return;
    button.disabled = true;
    try {
      await api(`/api/jobs/${jobId}`, { method: 'DELETE' });
      await loadJobs();
      toast('Application deleted.');
    } catch (error) {
      toast(error.message, 'error');
      button.disabled = false;
    }
  }
});
$('#apply-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const jobId = Number($('#apply-job-id').value);
  const resume = $('#resume-select').value;
  if (!resume) { toast('Choose a resume first.', 'error'); return; }
  $('#apply-dialog').close();
  await startAction('/api/actions/apply', { job_id: jobId, resume }, 'Assist with application');
});
$('#cancel-apply').addEventListener('click', () => $('#apply-dialog').close());
$('#close-dialog').addEventListener('click', () => $('#apply-dialog').close());
$('#apply-dialog').addEventListener('click', (event) => { if (event.target === $('#apply-dialog')) $('#apply-dialog').close(); });
$('#search-input').addEventListener('input', renderJobs);
$('#status-filter').addEventListener('change', renderJobs);
$('#refresh-button').addEventListener('click', async () => { await Promise.all([loadJobs(), loadResumes()]); toast('Tracker refreshed.'); });
$('#export-button').addEventListener('click', async () => {
  const button = $('#export-button');
  button.disabled = true;
  try {
    const response = await fetch('/api/export.csv', { cache: 'no-store' });
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch {}
      throw new Error(payload.error || 'CSV export failed.');
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'job-applications.csv';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    toast(`Exported ${state.jobs.length} applications to CSV.`);
  } catch (error) { toast(error.message, 'error'); }
  finally { button.disabled = false; }
});
$('#import-button').addEventListener('click', () => $('#csv-file-input').click());
$('#csv-file-input').addEventListener('change', async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.csv')) {
    toast('Choose a .csv file.', 'error');
    event.target.value = '';
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    toast('CSV files must be 5 MB or smaller.', 'error');
    event.target.value = '';
    return;
  }
  const button = $('#import-button');
  button.disabled = true;
  button.textContent = 'Importing…';
  try {
    const csvText = await file.text();
    if (!window.confirm(`Import ${file.name}?\n\nRows are matched by job URL. Matching applications will be updated; new URLs will be added. Applications not in this file will not be deleted. A database backup is created before changes.`)) return;
    const result = await api('/api/import.csv', { method: 'POST', body: JSON.stringify({ csv_text: csvText }) });
    await loadJobs();
    const details = result.import || {};
    toast(`Import complete: ${details.inserted || 0} added, ${details.updated || 0} updated. Backup saved.`);
  } catch (error) { toast(`CSV import failed: ${error.message}`, 'error'); }
  finally {
    button.disabled = false;
    button.textContent = 'Import CSV';
    event.target.value = '';
  }
});
$('#clear-log-button').addEventListener('click', () => { $('#terminal-output').textContent = 'Output view cleared. The task continues if running.'; });
async function init() {
  await Promise.all([loadJobs(), loadResumes()]);
  try {
    const payload = await api('/api/tasks');
    if (payload.latest) {
      renderTask(payload.latest);
      if (payload.latest.status === 'running') await monitorTask(payload.latest.id);
    }
  } catch (error) { console.warn('Could not restore last task:', error.message); }
}
init();
