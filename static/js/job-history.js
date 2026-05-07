import { apiFetch } from './api.js';
import { markGenerateJobCancelled } from './jobs.js';
import {
  escapeAttribute,
  escapeHtml,
  showToast,
  unlockBodyOverflowIfIdle,
} from './ui.js';

const ACTIVE_JOB_STATUSES = new Set(['queued', 'running']);
const POLL_INTERVAL_MS = 3000;

let jobHistoryState = {
  jobs: [],
  selectedJobIds: new Set(),
  loading: false,
  deleting: false,
  pollingStarted: false,
};

export function startJobHistoryPolling() {
  if (jobHistoryState.pollingStarted) return;
  jobHistoryState.pollingStarted = true;
  refreshJobHistory({ silent: true });
  window.setInterval(() => refreshJobHistory({ silent: true }), POLL_INTERVAL_MS);
}

export async function toggleJobHistory() {
  const drawer = document.getElementById('jobHistoryDrawer');
  const shouldOpen = drawer.classList.contains('hidden');
  drawer.classList.toggle('hidden', !shouldOpen);
  if (shouldOpen) {
    document.body.style.overflow = 'hidden';
    await refreshJobHistory();
  } else {
    unlockBodyOverflowIfIdle();
  }
}

export async function refreshJobHistory(options = {}) {
  jobHistoryState.loading = true;
  renderJobHistory();

  try {
    const jobs = await apiFetch('/api/generate/jobs', {}, 'loading active jobs');
    jobHistoryState.jobs = Array.isArray(jobs)
      ? jobs.filter(job => ACTIVE_JOB_STATUSES.has(job.status))
      : [];
    pruneSelectedJobs();
    updateJobHistoryBadge();
  } catch (error) {
    if (!options.silent) {
      showToast('Failed to load jobs: ' + error.message, 'error');
    }
  } finally {
    jobHistoryState.loading = false;
    renderJobHistory();
  }
}

export function toggleGenerateJobSelection(jobId, selected) {
  if (selected) {
    jobHistoryState.selectedJobIds.add(jobId);
  } else {
    jobHistoryState.selectedJobIds.delete(jobId);
  }
  renderJobHistoryActions();
}

export async function deleteSelectedGenerateJobs() {
  const jobIds = Array.from(jobHistoryState.selectedJobIds);
  if (!jobIds.length || jobHistoryState.deleting) return;

  const label = jobIds.length === 1 ? 'selected job' : `${jobIds.length} selected jobs`;
  if (!confirm(`Cancel and delete ${label}?`)) return;

  jobHistoryState.deleting = true;
  renderJobHistoryActions();

  const results = await Promise.allSettled(
    jobIds.map(jobId => apiFetch(
      `/api/generate/${encodeURIComponent(jobId)}`,
      { method: 'DELETE' },
      'cancelling generation job',
    )),
  );
  let cancelledCount = 0;

  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      cancelledCount += 1;
      markGenerateJobCancelled(jobIds[index]);
    }
  });

  jobHistoryState.deleting = false;
  jobHistoryState.selectedJobIds.clear();
  await refreshJobHistory({ silent: true });

  if (cancelledCount === jobIds.length) {
    showToast(cancelledCount === 1 ? 'Job cancelled' : `${cancelledCount} jobs cancelled`, 'success');
  } else if (cancelledCount > 0) {
    showToast(`${cancelledCount}/${jobIds.length} jobs cancelled`, 'error');
  } else {
    showToast('Failed to cancel jobs', 'error');
  }
}

function pruneSelectedJobs() {
  const activeJobIds = new Set(jobHistoryState.jobs.map(job => job.job_id));
  jobHistoryState.selectedJobIds = new Set(
    Array.from(jobHistoryState.selectedJobIds).filter(jobId => activeJobIds.has(jobId)),
  );
}

function updateJobHistoryBadge() {
  const badge = document.getElementById('jobHistoryBadge');
  if (!badge) return;

  const count = jobHistoryState.jobs.length;
  badge.textContent = String(count);
  badge.classList.toggle('hidden', count === 0);
}

function renderJobHistory() {
  const list = document.getElementById('jobHistoryList');
  const empty = document.getElementById('jobHistoryEmpty');
  if (!list || !empty) return;

  const jobs = jobHistoryState.jobs;

  if (jobHistoryState.loading && !jobs.length) {
    empty.classList.add('hidden');
    list.innerHTML = `
      <div class="rounded-lg border border-zinc-800 bg-zinc-950/45 px-4 py-5 text-sm text-zinc-500">
        Loading active jobs...
      </div>
    `;
    renderJobHistoryActions();
    return;
  }

  if (!jobs.length) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    renderJobHistoryActions();
    return;
  }

  empty.classList.add('hidden');
  list.innerHTML = jobs.map(renderJobHistoryItem).join('');
  renderJobHistoryActions();
}

function renderJobHistoryItem(job) {
  const jobId = job.job_id || '';
  const checked = jobHistoryState.selectedJobIds.has(jobId) ? 'checked' : '';
  const disabled = jobHistoryState.deleting ? 'disabled' : '';
  const operation = job.operation === 'edit' ? 'Edit' : 'Generate';
  const statusClass = job.status === 'queued'
    ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
    : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
  const stage = job.message || job.stage || job.status || 'Running';
  const prompt = job.prompt || '(no prompt)';
  const meta = [
    job.model,
    job.size,
    job.api_preset_name,
    formatCreatedAt(job.created_at),
  ].filter(Boolean).join(' · ');

  return `
    <label class="block rounded-lg border border-zinc-800 bg-zinc-950/45 p-3 transition-colors hover:border-zinc-700 hover:bg-zinc-900/80">
      <div class="flex items-start gap-3">
        <input type="checkbox" class="mt-1 h-4 w-4 rounded border-zinc-700 bg-zinc-900 text-emerald-500 focus:ring-emerald-500/30"
          onchange="toggleGenerateJobSelection(${JSON.stringify(jobId)}, this.checked)" ${checked} ${disabled}>
        <div class="min-w-0 flex-1">
          <div class="flex items-center justify-between gap-3">
            <span class="text-xs font-semibold uppercase tracking-wider text-zinc-500">${operation}</span>
            <span class="shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium ${statusClass}">
              ${escapeHtml(job.status || 'running')}
            </span>
          </div>
          <p class="mt-2 text-sm leading-5 text-zinc-200" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;">${escapeHtml(prompt)}</p>
          <p class="mt-2 truncate text-xs text-zinc-500" title="${escapeAttribute(stage)}">${escapeHtml(stage)}</p>
          <p class="mt-1 truncate text-xs font-mono text-zinc-600">${escapeHtml(meta || jobId)}</p>
        </div>
      </div>
    </label>
  `;
}

function renderJobHistoryActions() {
  const deleteBtn = document.getElementById('deleteSelectedJobsBtn');
  const selectedCount = document.getElementById('jobHistorySelectedCount');
  if (!deleteBtn || !selectedCount) return;

  const count = jobHistoryState.selectedJobIds.size;
  selectedCount.textContent = count ? `${count} selected` : 'No jobs selected';
  deleteBtn.disabled = count === 0 || jobHistoryState.deleting;
  deleteBtn.innerHTML = jobHistoryState.deleting
    ? '<span class="spinner"></span> Cancelling...'
    : '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg> Delete Selected';
}

function formatCreatedAt(createdAt) {
  if (!createdAt) return '';

  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return '';

  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}
