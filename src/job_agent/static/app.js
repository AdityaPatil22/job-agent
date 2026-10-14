'use strict';

const $ = (selector) => document.querySelector(selector);
const state = {
  jobs: [],
  resumes: [],
  taskId: null,
  latestTaskId: null,
  taskPoller: null,
  hasRenderedTask: false,
};

const STATUS_LABELS = {
  discovered: 'Discovered',
  shortlisted: 'Shortlisted',
  ready: 'Ready to review',
  applied: 'Applied',
  interview: 'Interview',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
};

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
  const region = $('#toast-region');
  const item = document.createElement('div');
  item.className = `toast${kind === 'error' ? ' error' : ''}`;
  item.textContent = message;
  region.append(item);
  window.setTimeout(() => item.remove(), 4200);
}

function safeText(value, fallback = '—') {
  if (value === null || value === undefined || String(value).trim() === '') return fallback;
  return String(value);
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderStats(jobs) {
  const count = (status) => jobs.filter((job) => job.status === status).length;
  $('#stat-total').textContent = jobs.length;
  $('#stat-progress').textContent = jobs.filter((job) => ['discovered', 'shortlisted', 'ready'].includes(job.status)).length;
  $('#stat-applied').textContent = count('applied');
  $('#stat-interviews').textContent = count('interview');
  $('#jobs-count').textContent = jobs.length;
}

function statusBadge(status) {
  const span = makeElement('span', `status-badge status-${status || 'discovered'}`, STATUS_LABELS[status] || status || 'Discovered');
  return span;
}

function createJobRow(job) {
  const row = document.createElement('tr');
  row.dataset.search = [job.title, job.company, job.location, job.url].filter(Boolean).join(' ').toLowerCase();
  row.dataset.status = job.status || 'discovered';

  const roleCell = makeElement('td', 'role-cell');
  roleCell.append(makeElement('span', 'role-title', safeText(job.title, 'Untitled role')));
  roleCell.append(makeElement('span', 'company-name', safeText(job.company, 'Unknown company')));
  const link = makeElement('a', 'url-link', 'Open job posting ↗');
  link.href = job.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  roleCell.append(link);
  row.append(roleCell);

  row.append(makeElement('td', 'location-cell', safeText(job.location, 'Not detected')));
  const statusCell = document.createElement('td');
  statusCell.append(statusBadge(job.status));
  row.append(statusCell);
  row.append(makeElement('td', 'date-cell', formatDate(job.date_found)));

  const actions = makeElement('td', 'actions-cell');
  const actionGroup = makeElement('div', 'row-actions');
  const assist = makeElement('button', 'action-link action-apply', 'Assist');
  assist.type = 'button';
  assist.dataset.action = 'apply';
  assist.dataset.jobId = String(job.id);
  assist.dataset.jobTitle = `${safeText(job.title, 'Untitled role')} — ${safeText(job.company, 'Unknown company')}`;
  actionGroup.append(assist);

  if (job.status !== 'applied') {
    const mark = makeElement('button', 'action-link action-mark', 'Mark applied');
    mark.type = 'button';
    mark.dataset.action = 'mark-applied';
    mark.dataset.jobId = String(job.id);
    actionGroup.append(mark);
  }

  const remove = makeElement('button', 'action-link action-delete', 'Delete');
  remove.type = 'button';
  remove.dataset.action = 'delete';
  remove.dataset.jobId = String(job.id);
  remove.dataset.jobTitle = `${safeText(job.title, 'Untitled role')} — ${safeText(job.company, 'Unknown company')}`;
  actionGroup.append(remove);
  actions.append(actionGroup);
  row.append(actions);
  return row;
}

function renderJobs() {
  const loading = $('#jobs-loading');
  const empty = $('#jobs-empty');
  const noResults = $('#jobs-no-results');
  const tableWrap = $('#jobs-table-wrap');
  const body = $('#jobs-table-body');
  body.replaceChildren();

  const searchTerm = $('#search-input').value.trim().toLowerCase();
  const selectedStatus = $('#status-filter').value;
  const filtered = state.jobs.filter((job) => {
    const searchable = [job.title, job.company, job.location, job.url].filter(Boolean).join(' ').toLowerCase();
    return searchable.includes(searchTerm) && (selectedStatus === 'all' || job.status === selectedStatus);
  });

  loading.hidden = true;
  empty.hidden = state.jobs.length !== 0;
  noResults.hidden = state.jobs.length === 0 || filtered.length !== 0;
  tableWrap.hidden = filtered.length === 0;
  filtered.forEach((job) => body.append(createJobRow(job)));
}

async function loadJobs() {
  $('#jobs-loading').hidden = false;
  $('#jobs-empty').hidden = true;
  $('#jobs-no-results').hidden = true;
  $('#jobs-table-wrap').hidden = true;
  try {
    const payload = await api('/api/jobs');
    state.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    renderStats(state.jobs);
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
  renderResumeOptions();
}

function renderResumeOptions() {
  const select = $('#resume-select');
  select.replaceChildren();
  if (!state.resumes.length) {
    select.append(new Option('No resume files found', ''));
    $('#resume-warning').hidden = false;
    $('#start-apply-button').disabled = true;
    return;
  }
  $('#resume-warning').hidden = true;
  $('#start-apply-button').disabled = false;
  select.append(new Option('Choose a resume…', ''));
  state.resumes.forEach((resume) => select.append(new Option(resume, resume)));
}

function openApplyDialog(jobId, jobTitle) {
  $('#apply-job-id').value = jobId;
  $('#apply-job-caption').textContent = `Job #${jobId}: ${jobTitle}. A browser window will open for you to review the application.`;
  if (typeof $('#apply-dialog').showModal === 'function') $('#apply-dialog').showModal();
  else toast('Your browser does not support the application dialog. Please update it.', 'error');
}

function setTaskStatus(status) {
  const element = $('#task-status');
  element.className = `task-status status-${status || 'idle'}`;
  const label = { running: 'Running', succeeded: 'Completed', failed: 'Failed', idle: 'Idle' }[status] || status;
  element.textContent = label;
}

function renderTask(task, force = false) {
  if (!task) return;
  state.latestTaskId = task.id;
  $('#terminal-task-name').textContent = `${task.label || 'job-agent'} · ${task.id}`;
  $('#terminal-output').textContent = task.output || '(No output yet.)';
  if (force || $('#terminal-output').dataset.follow !== 'false') {
    $('#terminal-output').scrollTop = $('#terminal-output').scrollHeight;
  }
  setTaskStatus(task.status);
  if (task.status === 'running') {
    $('#terminal-output').dataset.follow = 'true';
  }
}

async function monitorTask(taskId) {
  state.taskId = taskId;
  if (state.taskPoller) window.clearInterval(state.taskPoller);
  const update = async () => {
    try {
      const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
      renderTask(task, true);
      if (task.status !== 'running') {
        window.clearInterval(state.taskPoller);
        state.taskPoller = null;
        state.taskId = null;
        await loadJobs();
        toast(task.status === 'succeeded' ? `${task.label} completed.` : `${task.label} failed. Check command activity.`, task.status === 'succeeded' ? 'success' : 'error');
      }
    } catch (error) {
      window.clearInterval(state.taskPoller);
      state.taskPoller = null;
      toast(`Could not read command output: ${error.message}`, 'error');
    }
  };
  await update();
  if (state.taskId) state.taskPoller = window.setInterval(update, 900);
}

async function startAction(path, payload, label) {
  try {
    const result = await api(path, { method: 'POST', body: JSON.stringify(payload) });
    if (result.task_id) {
      toast(`${label} started. Watch the command output below and respond in the terminal if prompted.`);
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
  button.textContent = 'Starting…';
  const started = await startAction('/api/actions/add-job', { url }, 'Add job');
  button.disabled = false;
  button.innerHTML = '<span class="button-plus">＋</span> Add job';
  if (started) input.value = '';
});

$('#jobs-table-body').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  if (button.dataset.action === 'apply') {
    await loadResumes();
    openApplyDialog(button.dataset.jobId, button.dataset.jobTitle);
  }
  if (button.dataset.action === 'mark-applied') {
    const confirmed = window.confirm('Mark this job as applied? Do this only after you have submitted the application yourself.');
    if (!confirmed) return;
    const notes = window.prompt('Optional note (for example, confirmation number). Leave blank to skip.', '') || '';
    await startAction('/api/actions/mark-applied', { job_id: Number(button.dataset.jobId), notes }, 'Mark applied');
  }
  if (button.dataset.action === 'delete') {
    const jobId = Number(button.dataset.jobId);
    const title = button.dataset.jobTitle || `Job #${jobId}`;
    const confirmed = window.confirm(`Delete this application?\n\n${title}\n\nThis removes the job, its application status, and its activity history. This cannot be undone.`);
    if (!confirmed) return;
    button.disabled = true;
    try {
      await api(`/api/jobs/${jobId}`, { method: 'DELETE' });
      await loadJobs();
      toast(`Deleted ${title}.`);
    } catch (error) {
      toast(`Could not delete application: ${error.message}`, 'error');
      button.disabled = false;
    }
  }
});

$('#apply-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const jobId = Number($('#apply-job-id').value);
  const resume = $('#resume-select').value;
  if (!resume) {
    toast('Choose a resume file first.', 'error');
    return;
  }
  $('#apply-dialog').close();
  await startAction('/api/actions/apply', { job_id: jobId, resume }, 'Assist with application');
});

function closeDialog() { $('#apply-dialog').close(); }
$('#cancel-apply').addEventListener('click', closeDialog);
$('#close-dialog').addEventListener('click', closeDialog);
$('#apply-dialog').addEventListener('click', (event) => {
  if (event.target === $('#apply-dialog')) closeDialog();
});

$('#search-input').addEventListener('input', renderJobs);
$('#status-filter').addEventListener('change', renderJobs);
$('#refresh-button').addEventListener('click', async () => {
  await Promise.all([loadJobs(), loadResumes()]);
  toast('Dashboard refreshed.');
});
$('#export-button').addEventListener('click', () => startAction('/api/actions/export', {}, 'Export CSV'));
$('#clear-log-button').addEventListener('click', () => {
  $('#terminal-output').textContent = 'Output view cleared. The CLI task continues if it is running.';
  $('#terminal-output').dataset.follow = 'false';
});

async function init() {
  await Promise.all([loadJobs(), loadResumes()]);
  try {
    const payload = await api('/api/tasks');
    if (payload.latest) {
      renderTask(payload.latest, true);
      if (payload.latest.status === 'running') await monitorTask(payload.latest.id);
    }
  } catch (error) {
    console.warn('Could not restore last task:', error.message);
  }
}

init();
