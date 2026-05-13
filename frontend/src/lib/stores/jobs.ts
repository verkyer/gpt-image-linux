import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { openJsonEventSource } from '$lib/api/events';
import { t } from '$lib/i18n';
import { filenameFromImageUrl } from '$lib/utils/format';
import type { GenerateJobResponse, GenerateJobStatus } from '$lib/api/types';
import type { PreviewState } from '$lib/stores/preview';

const ACTIVE_STATUSES = new Set(['queued', 'running']);

export type JobsState = {
  jobs: GenerateJobStatus[];
  selectedIds: Set<string>;
};

const initialJobsState: JobsState = {
  jobs: [],
  selectedIds: new Set()
};

function createJobsStore() {
  const { subscribe, set, update } = writable<JobsState>(initialJobsState);
  let state = initialJobsState;
  let jobsSource: EventSource | null = null;
  let jobsPollingTimer: ReturnType<typeof setInterval> | null = null;
  let activeJobSource: EventSource | null = null;

  subscribe((value) => {
    state = value;
  });

  async function loadJobs() {
    try {
      const jobs = await apiFetch<GenerateJobStatus[]>('/api/generate/jobs', {}, 'loading jobs');
      const selectedIds = new Set([...state.selectedIds].filter((id) => jobs.some((job) => job.job_id === id)));
      update((current) => ({ ...current, jobs, selectedIds }));
    } catch {
      update((current) => ({ ...current, jobs: [] }));
    }
  }

  function startJobsEvents() {
    jobsSource?.close();
    jobsSource = openJsonEventSource<GenerateJobStatus[]>('/api/generate/jobs/events', {
      onEvent: ({ data }) => {
        if (Array.isArray(data)) update((current) => ({ ...current, jobs: data }));
      },
      onError: () => {
        jobsSource?.close();
        jobsSource = null;
      }
    });

    if (!jobsPollingTimer) {
      jobsPollingTimer = setInterval(() => {
        void loadJobs();
      }, 5000);
    }
  }

  function toggleSelection(jobId: string) {
    const selectedIds = new Set(state.selectedIds);
    if (selectedIds.has(jobId)) selectedIds.delete(jobId);
    else selectedIds.add(jobId);
    update((current) => ({ ...current, selectedIds }));
  }

  function toggleAll() {
    const selectedIds = state.selectedIds.size === state.jobs.length ? new Set<string>() : new Set(state.jobs.map((job) => job.job_id));
    update((current) => ({ ...current, selectedIds }));
  }

  async function cancelSelected() {
    const ids = [...state.selectedIds];
    await Promise.all(
      ids.map((jobId) =>
        apiFetch(`/api/generate/${encodeURIComponent(jobId)}`, { method: 'DELETE' }, 'cancelling job').catch(() => null)
      )
    );
    update((current) => ({ ...current, selectedIds: new Set() }));
    await loadJobs();
  }

  function trackJob(
    jobId: string,
    updatePreviewFromJob: (job: GenerateJobStatus) => Promise<void>,
    setPreviewError: (message: string) => void
  ) {
    if (!jobId) return;
    let terminal = false;
    closeActiveJobSource();
    activeJobSource = openJsonEventSource<GenerateJobStatus>(`/api/generate/${encodeURIComponent(jobId)}/events`, {
      onEvent: ({ data }) => {
        void updatePreviewFromJob(data);
        if (!ACTIVE_STATUSES.has(data.status)) {
          terminal = true;
          closeActiveJobSource();
        }
      },
      onError: () => {
        if (!terminal) void pollJob(jobId, updatePreviewFromJob, setPreviewError);
        closeActiveJobSource();
      }
    });
  }

  async function pollJob(
    jobId: string,
    updatePreviewFromJob: (job: GenerateJobStatus) => Promise<void>,
    setPreviewError: (message: string) => void
  ) {
    try {
      const job = await apiFetch<GenerateJobStatus>(`/api/generate/${encodeURIComponent(jobId)}`, {}, 'loading job');
      await updatePreviewFromJob(job);
      if (ACTIVE_STATUSES.has(job.status)) {
        setTimeout(() => void pollJob(jobId, updatePreviewFromJob, setPreviewError), 1200);
      }
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : get(t).messages.jobLoadFailed);
    }
  }

  function makeQueuedPreview(currentPrompt: string, operation: NonNullable<GenerateJobResponse['operation']>): PreviewState {
    closeActiveJobSource();
    return {
      loading: true,
      error: '',
      imageUrl: '',
      filename: '',
      prompt: currentPrompt,
      job: {
        job_id: '',
        status: 'queued',
        stage: 'queued',
        message: operation === 'edit' ? get(t).messages.queuedEdit : get(t).messages.queuedGeneration,
        operation
      }
    };
  }

  function previewFromJob(job: GenerateJobStatus, preview: PreviewState): PreviewState {
    const image = job.image_url || '';
    return {
      loading: ACTIVE_STATUSES.has(job.status),
      error: job.status === 'error' ? job.error || job.message || get(t).messages.jobFailed : '',
      job,
      imageUrl: image || preview.imageUrl,
      filename: image ? filenameFromImageUrl(image) : preview.filename,
      prompt: job.prompt || preview.prompt
    };
  }

  function closeActiveJobSource() {
    activeJobSource?.close();
    activeJobSource = null;
  }

  function cleanup() {
    closeActiveJobSource();
    jobsSource?.close();
    jobsSource = null;
    if (jobsPollingTimer) clearInterval(jobsPollingTimer);
    jobsPollingTimer = null;
  }

  return {
    subscribe,
    set,
    loadJobs,
    startJobsEvents,
    toggleSelection,
    toggleAll,
    cancelSelected,
    trackJob,
    makeQueuedPreview,
    previewFromJob,
    closeActiveJobSource,
    cleanup
  };
}

export const jobsStore = createJobsStore();
