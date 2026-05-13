import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import type { GenerateJobResponse, GenerateJobStatus, GenerateRequestBody } from '$lib/api/types';

export type PreviewState = {
  loading: boolean;
  error: string;
  job: GenerateJobStatus | null;
  imageUrl: string;
  filename: string;
  prompt: string;
};

export type PromptFormState = {
  prompt: string;
  size: string;
  model: string;
  quality: GenerateRequestBody['quality'];
  outputFormat: GenerateRequestBody['output_format'];
  outputCompression: string;
  quantity: number;
  responseFormat: string;
  webhookUrl: string;
};

export type EditSourceState = {
  file: File | null;
  selectedGalleryImageId: string;
  label: string;
  previewUrl: string;
  previewLabel: string;
};

const initialPreviewState: PreviewState = {
  loading: false,
  error: '',
  job: null,
  imageUrl: '',
  filename: '',
  prompt: ''
};

export const initialPromptFormState: PromptFormState = {
  prompt: '',
  size: 'auto',
  model: 'gpt-image-2',
  quality: 'auto',
  outputFormat: 'png',
  outputCompression: '',
  quantity: 1,
  responseFormat: '',
  webhookUrl: ''
};

const initialEditSourceState: EditSourceState = {
  file: null,
  selectedGalleryImageId: '',
  label: '',
  previewUrl: '',
  previewLabel: ''
};

function buildRequestBody(form: PromptFormState): GenerateRequestBody {
  const body: GenerateRequestBody = {
    prompt: form.prompt.trim(),
    size: form.size,
    model: form.model.trim() || 'gpt-image-2',
    n: Math.min(Math.max(Number(form.quantity) || 1, 1), 10),
    quality: form.quality,
    output_format: form.outputFormat,
    output_compression: null,
    response_format: form.responseFormat ? (form.responseFormat as 'url' | 'b64_json') : null,
    webhook_url: form.webhookUrl.trim() || null
  };

  if (form.outputFormat !== 'png' && form.outputCompression !== '') {
    body.output_compression = Math.min(Math.max(Number(form.outputCompression), 0), 100);
  }

  return body;
}

function isImageFile(file: File) {
  if (file.type.startsWith('image/') && file.type !== 'image/svg+xml') return true;
  return /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|tiff?|webp)$/i.test(file.name);
}

function createPreviewStore() {
  const { subscribe, set, update } = writable<PreviewState>(initialPreviewState);
  let state = initialPreviewState;
  let lastRequest: GenerateRequestBody | null = null;
  let lastAction: 'generate' | 'edit' = 'generate';
  let editPreviewObjectUrl = '';

  subscribe((value) => {
    state = value;
  });

  function setPreview(next: PreviewState) {
    set(next);
  }

  function setError(message: string) {
    update((current) => ({ ...current, loading: false, error: message }));
  }

  function clearPreview(closeActiveJobSource?: () => void) {
    closeActiveJobSource?.();
    set(initialPreviewState);
  }

  function revokeEditPreviewObjectUrl() {
    if (!editPreviewObjectUrl) return;
    URL.revokeObjectURL(editPreviewObjectUrl);
    editPreviewObjectUrl = '';
  }

  function setEditPreview(url: string, label: string, objectUrl = ''): EditSourceState {
    revokeEditPreviewObjectUrl();
    editPreviewObjectUrl = objectUrl;
    return {
      file: objectUrl ? null : get(editSourceStore).file,
      selectedGalleryImageId: get(editSourceStore).selectedGalleryImageId,
      label: get(editSourceStore).label,
      previewUrl: url,
      previewLabel: label
    };
  }

  function clearEditSource(input?: HTMLInputElement) {
    revokeEditPreviewObjectUrl();
    editSourceStore.set(initialEditSourceState);
    if (input) input.value = '';
  }

  function handleEditFile(event: Event, input?: HTMLInputElement) {
    const target = event.currentTarget as HTMLInputElement;
    const file = target.files?.[0] || null;
    if (!file) {
      clearEditSource(input || target);
      return;
    }
    if (!isImageFile(file)) {
      target.value = '';
      clearEditSource(input || target);
      setError(get(t).messages.imageUploadRequired);
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    revokeEditPreviewObjectUrl();
    editPreviewObjectUrl = objectUrl;
    editSourceStore.set({
      file,
      selectedGalleryImageId: '',
      label: file.name,
      previewUrl: objectUrl,
      previewLabel: file.name
    });
  }

  async function generateImage(
    form: PromptFormState,
    makeQueuedPreview: (prompt: string, operation: NonNullable<GenerateJobResponse['operation']>) => PreviewState,
    trackJob: (jobId: string) => void,
    loadJobs: () => Promise<void>
  ) {
    const body = buildRequestBody(form);
    if (!body.prompt) {
      setError(get(t).messages.promptRequired);
      return;
    }
    lastRequest = body;
    lastAction = 'generate';
    set(makeQueuedPreview(body.prompt, 'generation'));

    const job = await apiFetch<GenerateJobResponse>(
      '/api/generate',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      },
      'starting image generation'
    );
    trackJob(job.job_id);
    await loadJobs();
  }

  async function editImage(
    form: PromptFormState,
    editSource: EditSourceState,
    makeQueuedPreview: (prompt: string, operation: NonNullable<GenerateJobResponse['operation']>) => PreviewState,
    trackJob: (jobId: string) => void,
    loadJobs: () => Promise<void>
  ) {
    if (!editSource.file && !editSource.selectedGalleryImageId) {
      setError(get(t).messages.editSourceRequired);
      return;
    }

    const body = buildRequestBody(form);
    if (!body.prompt) {
      setError(get(t).messages.promptRequired);
      return;
    }
    lastRequest = body;
    lastAction = 'edit';
    set(makeQueuedPreview(body.prompt, 'edit'));

    const formData = new FormData();
    Object.entries(body).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        formData.append(key, String(value));
      }
    });

    let endpoint = '/api/edits';
    if (editSource.file) {
      formData.append('image', editSource.file, editSource.file.name);
    } else {
      endpoint = `/api/edits/from-gallery/${encodeURIComponent(editSource.selectedGalleryImageId)}`;
    }

    const job = await apiFetch<GenerateJobResponse>(
      endpoint,
      {
        method: 'POST',
        body: formData
      },
      'starting image edit'
    );
    trackJob(job.job_id);
    await loadJobs();
  }

  function regenerate(setForm: (form: PromptFormState) => void, generate: () => void, edit: () => void) {
    if (!lastRequest) return;
    setForm({
      prompt: lastRequest.prompt,
      size: lastRequest.size,
      model: lastRequest.model,
      quantity: lastRequest.n,
      quality: lastRequest.quality,
      outputFormat: lastRequest.output_format,
      outputCompression: lastRequest.output_compression === null || lastRequest.output_compression === undefined ? '' : String(lastRequest.output_compression),
      responseFormat: lastRequest.response_format || '',
      webhookUrl: lastRequest.webhook_url || ''
    });
    if (lastAction === 'edit') edit();
    else generate();
  }

  function cleanup() {
    revokeEditPreviewObjectUrl();
  }

  return {
    subscribe,
    set,
    setPreview,
    setError,
    clearPreview,
    clearEditSource,
    handleEditFile,
    setEditPreview,
    generateImage,
    editImage,
    regenerate,
    cleanup
  };
}

export const previewStore = createPreviewStore();
export const editSourceStore = writable<EditSourceState>(initialEditSourceState);
