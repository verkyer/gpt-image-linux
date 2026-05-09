<script lang="ts">
  import { onMount } from 'svelte';
  import AccessGate from '$lib/components/AccessGate.svelte';
  import GalleryGrid from '$lib/components/GalleryGrid.svelte';
  import Header from '$lib/components/Header.svelte';
  import JobHistoryDrawer from '$lib/components/JobHistoryDrawer.svelte';
  import Lightbox from '$lib/components/Lightbox.svelte';
  import PreviewPanel from '$lib/components/PreviewPanel.svelte';
  import SettingsDrawer from '$lib/components/SettingsDrawer.svelte';
  import SizeDialog from '$lib/components/SizeDialog.svelte';
  import { apiFetch, setUnauthorizedHandler } from '$lib/api/client';
  import { openJsonEventSource } from '$lib/api/events';
  import type {
    AccessStatus,
    ApiPath,
    GalleryEntry,
    GalleryResponse,
    GenerateJobResponse,
    GenerateJobStatus,
    GenerateRequestBody,
    SettingsResponse
  } from '$lib/api/types';
  import { accessStore } from '$lib/stores/access';
  import { defaultGalleryFilters, galleryFiltersStore, galleryStore, type GalleryFilters } from '$lib/stores/gallery';
  import { jobsStore } from '$lib/stores/jobs';
  import { previewStore, type PreviewState } from '$lib/stores/preview';
  import { settingsStore } from '$lib/stores/settings';
  import { uiStore } from '$lib/stores/ui';
  import { copyText, filenameFromImageUrl, imageUrl } from '$lib/utils/format';

  const ACTIVE_STATUSES = new Set(['queued', 'running']);

  let version = '';
  let releaseUrl: string | null = null;
  let accessVisible = true;
  let accessLoading = false;
  let accessError = '';
  let settings: SettingsResponse | null = null;
  let settingsOpen = false;
  let settingsSaving = false;
  let jobsOpen = false;
  let gallery: GalleryResponse | null = null;
  let galleryLoading = false;
  let galleryPage = 1;
  let galleryFilters: GalleryFilters = { ...defaultGalleryFilters };
  let selectedJobIds = new Set<string>();
  let jobs: GenerateJobStatus[] = [];
  let lightboxImage: GalleryEntry | null = null;
  let sizeDialogOpen = false;
  let toast = '';

  let prompt = '';
  let size = 'auto';
  let model = 'gpt-image-2';
  let quality: GenerateRequestBody['quality'] = 'auto';
  let outputFormat: GenerateRequestBody['output_format'] = 'png';
  let outputCompression = '';
  let quantity = 1;
  let responseFormat = '';
  let webhookUrl = '';
  let editFile: File | null = null;
  let selectedGalleryImageId = '';
  let editLabel = '';
  let editPreviewOpen = false;
  let editPreviewUrl = '';
  let editPreviewLabel = '';
  let editPreviewObjectUrl = '';
  let editInput: HTMLInputElement;
  let promptLen = 0;

  let preview: PreviewState = {
    loading: false,
    error: '',
    job: null,
    imageUrl: '',
    filename: '',
    prompt: ''
  };

  let lastRequest: GenerateRequestBody | null = null;
  let lastAction: 'generate' | 'edit' = 'generate';
  let activeJobSource: EventSource | null = null;
  let jobsSource: EventSource | null = null;
  let jobsPollingTimer: ReturnType<typeof setInterval> | null = null;
  let galleryFilterTimer: ReturnType<typeof setTimeout> | null = null;
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  $: responsesMode = settings?.api_path === '/v1/responses';
  $: activeJobsCount = jobs.length;
  $: promptLen = prompt.length;
  $: previewStore.set(preview);
  $: galleryStore.set(gallery);
  $: galleryFiltersStore.set(galleryFilters);
  $: jobsStore.set(jobs);
  $: settingsStore.set(settings);
  $: uiStore.set({
    settingsOpen,
    jobsOpen,
    lightboxOpen: Boolean(lightboxImage),
    sizeDialogOpen,
    toast
  });
  $: accessStore.set({
    authenticated: !accessVisible,
    loading: accessLoading,
    gateVisible: accessVisible,
    error: accessError
  });

  function showToast(message: string) {
    toast = message;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast = '';
    }, 2500);
  }

  async function loadVersion() {
    try {
      const data = await apiFetch<{ version: string; release_url: string | null }>('/api/version', {}, 'loading version');
      version = data.version;
      releaseUrl = data.release_url;
    } catch {
      version = '';
      releaseUrl = null;
    }
  }

  async function checkAccess() {
    try {
      const data = await apiFetch<AccessStatus>('/api/access/status', {}, 'checking access');
      if (data.authenticated) {
        accessVisible = false;
        accessError = '';
        await loadInitialData();
        return;
      }
      accessVisible = true;
    } catch (error) {
      accessVisible = true;
      accessError = error instanceof Error ? error.message : 'Access check failed';
    }
  }

  async function unlockAccess(accessKey: string) {
    accessLoading = true;
    accessError = '';
    try {
      const data = await apiFetch<AccessStatus>(
        '/api/access',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_key: accessKey })
        },
        'unlocking access'
      );
      if (!data.authenticated) throw new Error('Invalid access key');
      accessVisible = false;
      await loadInitialData();
    } catch (error) {
      accessError = error instanceof Error ? error.message : 'Invalid access key';
    } finally {
      accessLoading = false;
    }
  }

  async function loadInitialData() {
    await Promise.all([loadSettings(), loadGallery(1), loadJobs()]);
    startJobsEvents();
  }

  async function loadSettings() {
    const data = await apiFetch<SettingsResponse>('/api/settings', {}, 'loading settings');
    settings = data;
  }

  async function saveSettings(body: Record<string, unknown>) {
    if (!String(body.api_url || '').trim()) {
      showToast('Please enter an API URL');
      return;
    }
    if (body.api_key !== null && !String(body.api_key || '').trim()) {
      showToast('Please enter an API Key');
      return;
    }

    settingsSaving = true;
    try {
      settings = await apiFetch<SettingsResponse>(
        '/api/settings',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'saving settings'
      );
      settingsOpen = false;
      showToast('Preset saved');
    } finally {
      settingsSaving = false;
    }
  }

  async function createPreset() {
    settings = await apiFetch<SettingsResponse>(
      '/api/settings/presets',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_preset_id: settings?.active_preset_id })
      },
      'creating preset'
    );
    showToast('Preset created');
  }

  async function activatePreset(presetId: string) {
    if (!presetId || presetId === settings?.active_preset_id) return;
    settings = await apiFetch<SettingsResponse>(
      `/api/settings/presets/${encodeURIComponent(presetId)}/activate`,
      { method: 'POST' },
      'switching preset'
    );
    showToast('Preset switched');
  }

  async function deleteActivePreset() {
    if (!settings || settings.presets.length <= 1) return;
    const active = settings.presets.find((preset) => preset.id === settings?.active_preset_id);
    if (!active || !confirm(`Delete preset "${active.name || 'Untitled preset'}"?`)) return;
    settings = await apiFetch<SettingsResponse>(
      `/api/settings/presets/${encodeURIComponent(active.id)}`,
      { method: 'DELETE' },
      'deleting preset'
    );
    showToast('Preset deleted');
  }

  function buildRequestBody(): GenerateRequestBody {
    const body: GenerateRequestBody = {
      prompt: prompt.trim(),
      size,
      model: model.trim() || 'gpt-image-2',
      n: Math.min(Math.max(Number(quantity) || 1, 1), 10),
      quality,
      output_format: outputFormat,
      output_compression: null,
      response_format: responseFormat ? (responseFormat as 'url' | 'b64_json') : null,
      webhook_url: webhookUrl.trim() || null
    };

    if (outputFormat !== 'png' && outputCompression !== '') {
      body.output_compression = Math.min(Math.max(Number(outputCompression), 0), 100);
    }

    return body;
  }

  async function generateImage() {
    const body = buildRequestBody();
    if (!body.prompt) {
      setPreviewError('Please enter a prompt');
      return;
    }
    lastRequest = body;
    lastAction = 'generate';
    setPreviewLoading(body.prompt, 'generation');

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

  async function editImage() {
    if (!editFile && !selectedGalleryImageId) {
      setPreviewError('Please upload an image or choose one from gallery first');
      return;
    }

    const body = buildRequestBody();
    if (!body.prompt) {
      setPreviewError('Please enter a prompt');
      return;
    }
    lastRequest = body;
    lastAction = 'edit';
    setPreviewLoading(body.prompt, 'edit');

    const formData = new FormData();
    Object.entries(body).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        formData.append(key, String(value));
      }
    });

    let endpoint = '/api/edits';
    if (editFile) {
      formData.append('image', editFile, editFile.name);
    } else {
      endpoint = `/api/edits/from-gallery/${encodeURIComponent(selectedGalleryImageId)}`;
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

  function setPreviewLoading(currentPrompt: string, operation: 'generation' | 'edit') {
    closeActiveJobSource();
    preview = {
      loading: true,
      error: '',
      imageUrl: '',
      filename: '',
      prompt: currentPrompt,
      job: {
        job_id: '',
        status: 'queued',
        stage: 'queued',
        message: operation === 'edit' ? 'Queued image edit' : 'Queued image generation',
        operation
      }
    };
  }

  function setPreviewError(message: string) {
    preview = {
      ...preview,
      loading: false,
      error: message
    };
  }

  function trackJob(jobId: string) {
    if (!jobId) return;
    let terminal = false;
    closeActiveJobSource();
    activeJobSource = openJsonEventSource<GenerateJobStatus>(`/api/generate/${encodeURIComponent(jobId)}/events`, {
      onEvent: ({ data }) => {
        updatePreviewFromJob(data);
        if (!ACTIVE_STATUSES.has(data.status)) {
          terminal = true;
          closeActiveJobSource();
        }
      },
      onError: () => {
        if (!terminal) pollJob(jobId);
        closeActiveJobSource();
      }
    });
  }

  async function pollJob(jobId: string) {
    try {
      const job = await apiFetch<GenerateJobStatus>(`/api/generate/${encodeURIComponent(jobId)}`, {}, 'loading job');
      updatePreviewFromJob(job);
      if (ACTIVE_STATUSES.has(job.status)) {
        setTimeout(() => pollJob(jobId), 1200);
      }
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : 'Failed to load job');
    }
  }

  async function updatePreviewFromJob(job: GenerateJobStatus) {
    const image = job.image_url || '';
    preview = {
      loading: ACTIVE_STATUSES.has(job.status),
      error: job.status === 'error' ? job.error || job.message || 'Job failed' : '',
      job,
      imageUrl: image || preview.imageUrl,
      filename: image ? filenameFromImageUrl(image) : preview.filename,
      prompt: job.prompt || preview.prompt
    };

    if (!ACTIVE_STATUSES.has(job.status)) {
      await loadJobs();
      if (job.status === 'success') await loadGallery(1);
    }
  }

  function closeActiveJobSource() {
    activeJobSource?.close();
    activeJobSource = null;
  }

  function regenerate() {
    if (!lastRequest) return;
    prompt = lastRequest.prompt;
    size = lastRequest.size;
    model = lastRequest.model;
    quantity = lastRequest.n;
    quality = lastRequest.quality;
    outputFormat = lastRequest.output_format;
    outputCompression = lastRequest.output_compression === null || lastRequest.output_compression === undefined ? '' : String(lastRequest.output_compression);
    responseFormat = lastRequest.response_format || '';
    webhookUrl = lastRequest.webhook_url || '';
    if (lastAction === 'edit') {
      void editImage();
    } else {
      void generateImage();
    }
  }

  function clearPreview() {
    closeActiveJobSource();
    preview = {
      loading: false,
      error: '',
      job: null,
      imageUrl: '',
      filename: '',
      prompt: ''
    };
  }

  async function loadJobs() {
    try {
      const data = await apiFetch<GenerateJobStatus[]>('/api/generate/jobs', {}, 'loading jobs');
      jobs = data;
      selectedJobIds = new Set([...selectedJobIds].filter((id) => jobs.some((job) => job.job_id === id)));
    } catch {
      jobs = [];
    }
  }

  function startJobsEvents() {
    jobsSource?.close();
    jobsSource = openJsonEventSource<GenerateJobStatus[]>('/api/generate/jobs/events', {
      onEvent: ({ data }) => {
        if (Array.isArray(data)) jobs = data;
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

  function toggleJobSelection(jobId: string) {
    const next = new Set(selectedJobIds);
    if (next.has(jobId)) next.delete(jobId);
    else next.add(jobId);
    selectedJobIds = next;
  }

  function toggleAllJobs() {
    selectedJobIds = selectedJobIds.size === jobs.length ? new Set() : new Set(jobs.map((job) => job.job_id));
  }

  async function cancelSelectedJobs() {
    const ids = [...selectedJobIds];
    await Promise.all(
      ids.map((jobId) =>
        apiFetch(`/api/generate/${encodeURIComponent(jobId)}`, { method: 'DELETE' }, 'cancelling job').catch(() => null)
      )
    );
    selectedJobIds = new Set();
    await loadJobs();
  }

  function buildGalleryParams(page: number) {
    const params = new URLSearchParams({ page: String(page), page_size: '9' });
    if (galleryFilters.prompt.trim()) params.set('prompt', galleryFilters.prompt.trim());
    if (galleryFilters.model) params.set('model', galleryFilters.model);
    if (galleryFilters.preset) params.set('preset', galleryFilters.preset);
    if (galleryFilters.size) params.set('size', galleryFilters.size);
    if (galleryFilters.dateFrom) params.set('date_from', galleryFilters.dateFrom);
    if (galleryFilters.dateTo) params.set('date_to', galleryFilters.dateTo);
    if (galleryFilters.favorite) params.set('favorite', 'true');
    return params;
  }

  async function loadGallery(page = galleryPage) {
    galleryLoading = true;
    try {
      const data = await apiFetch<GalleryResponse>(`/api/gallery?${buildGalleryParams(page).toString()}`, {}, 'loading gallery');
      gallery = data;
      galleryPage = data.page;
    } finally {
      galleryLoading = false;
    }
  }

  function updateGalleryFilter(key: keyof GalleryFilters, value: string | boolean) {
    galleryFilters = {
      ...galleryFilters,
      [key]: key === 'favorite' ? Boolean(value) : String(value || '')
    };
    if (galleryFilterTimer) clearTimeout(galleryFilterTimer);
    galleryFilterTimer = setTimeout(() => {
      void loadGallery(1);
    }, key === 'prompt' ? 250 : 0);
  }

  function resetGalleryFilters() {
    galleryFilters = { ...defaultGalleryFilters };
    void loadGallery(1);
  }

  async function toggleFavorite(image: GalleryEntry) {
    await apiFetch<GalleryEntry>(
      `/api/gallery/${encodeURIComponent(image.id)}/favorite`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ favorite: !image.favorite })
      },
      'updating favorite'
    );
    await loadGallery(galleryPage);
    if (lightboxImage?.id === image.id) {
      lightboxImage = { ...image, favorite: !image.favorite };
    }
  }

  async function deleteImage(image: GalleryEntry) {
    if (!confirm('Delete this image from gallery?')) return;
    await apiFetch(`/api/gallery/${encodeURIComponent(image.id)}`, { method: 'DELETE' }, 'deleting image');
    if (lightboxImage?.id === image.id) lightboxImage = null;
    if (selectedGalleryImageId === image.id) clearEditSource();
    await loadGallery(galleryPage);
    showToast('Image deleted');
  }

  async function deleteAllImages() {
    if (!confirm('This permanently deletes every gallery image stored on the server. Continue?')) return;
    await apiFetch('/api/gallery', { method: 'DELETE' }, 'deleting all images');
    lightboxImage = null;
    clearEditSource();
    clearPreview();
    await loadGallery(1);
    showToast('All server images deleted');
  }

  async function importArchive(file: File) {
    const formData = new FormData();
    formData.append('archive', file, file.name);
    const result = await apiFetch<{ status: string; imported: number }>(
      '/api/import',
      {
        method: 'POST',
        body: formData
      },
      'importing archive'
    );
    await loadGallery(1);
    showToast(`Imported ${result.imported} image${result.imported === 1 ? '' : 's'}`);
  }

  function prepareGalleryImageForEdit(image: GalleryEntry) {
    editFile = null;
    selectedGalleryImageId = image.id;
    editLabel = `Gallery: ${image.filename}`;
    setEditPreview(imageUrl(image.filename), editLabel);
    if (editInput) editInput.value = '';
    lightboxImage = null;
    showToast('Gallery image ready for edits');
  }

  function handleEditFile(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0] || null;
    if (!file) {
      clearEditSource();
      return;
    }
    if (!isImageFile(file)) {
      input.value = '';
      clearEditSource();
      setPreviewError('Please upload an image file');
      return;
    }
    editFile = file;
    selectedGalleryImageId = '';
    editLabel = file.name;
    const objectUrl = URL.createObjectURL(file);
    setEditPreview(objectUrl, file.name, objectUrl);
  }

  function openEditPicker() {
    editInput?.click();
  }

  function setEditPreview(url: string, label: string, objectUrl = '') {
    revokeEditPreviewObjectUrl();
    editPreviewUrl = url;
    editPreviewLabel = label;
    editPreviewObjectUrl = objectUrl;
  }

  function revokeEditPreviewObjectUrl() {
    if (!editPreviewObjectUrl) return;
    URL.revokeObjectURL(editPreviewObjectUrl);
    editPreviewObjectUrl = '';
  }

  function clearEditSource() {
    editFile = null;
    selectedGalleryImageId = '';
    editLabel = '';
    editPreviewOpen = false;
    editPreviewUrl = '';
    editPreviewLabel = '';
    revokeEditPreviewObjectUrl();
    if (editInput) editInput.value = '';
  }

  function openEditPreview() {
    if (editPreviewUrl) editPreviewOpen = true;
  }

  function isImageFile(file: File) {
    if (file.type.startsWith('image/')) return true;
    return /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|svg|tiff?|webp)$/i.test(file.name);
  }

  async function copyPrompt(image: GalleryEntry) {
    await copyText(image.prompt);
    showToast('Prompt copied');
  }

  async function copyImageUrl(image: GalleryEntry) {
    await copyText(new URL(imageUrl(image.filename), window.location.origin).href);
    showToast('Image URL copied');
  }

  function clampQuantity() {
    quantity = Math.min(Math.max(Number(quantity) || 1, 1), 10);
  }

  function clampCompression() {
    if (outputCompression === '') return;
    outputCompression = String(Math.min(Math.max(Number(outputCompression) || 0, 0), 100));
  }

  $: if (outputFormat === 'png') outputCompression = '';

  onMount(() => {
    setUnauthorizedHandler((message) => {
      accessVisible = true;
      accessError = message || '';
    });
    void loadVersion();
    void checkAccess();

    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (editPreviewOpen) editPreviewOpen = false;
      else if (lightboxImage) lightboxImage = null;
      else if (sizeDialogOpen) sizeDialogOpen = false;
      else if (jobsOpen) jobsOpen = false;
      else if (settingsOpen) settingsOpen = false;
    };
    window.addEventListener('keydown', keydown);
    return () => {
      window.removeEventListener('keydown', keydown);
      closeActiveJobSource();
      jobsSource?.close();
      if (jobsPollingTimer) clearInterval(jobsPollingTimer);
      if (galleryFilterTimer) clearTimeout(galleryFilterTimer);
      if (toastTimer) clearTimeout(toastTimer);
      revokeEditPreviewObjectUrl();
    };
  });
</script>

<svelte:head>
  <title>GPT Image Panel</title>
</svelte:head>

<AccessGate visible={accessVisible} error={accessError} loading={accessLoading} onUnlock={unlockAccess} />
<Header {version} {releaseUrl} {activeJobsCount} onOpenJobs={() => (jobsOpen = true)} onOpenSettings={() => (settingsOpen = true)} />

<SettingsDrawer
  open={settingsOpen}
  {settings}
  saving={settingsSaving}
  onClose={() => (settingsOpen = false)}
  onSave={saveSettings}
  onCreate={createPreset}
  onActivate={activatePreset}
  onDelete={deleteActivePreset}
/>

<JobHistoryDrawer
  open={jobsOpen}
  {jobs}
  selectedIds={selectedJobIds}
  onClose={() => (jobsOpen = false)}
  onRefresh={loadJobs}
  onToggle={toggleJobSelection}
  onToggleAll={toggleAllJobs}
  onCancelSelected={cancelSelectedJobs}
/>

<main class="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
  {#if toast}
    <div class="fixed bottom-5 right-5 z-[90] rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-zinc-100 shadow-2xl">
      {toast}
    </div>
  {/if}

  <section class="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4 sm:p-5">
    <div class="mb-4 flex items-start justify-between gap-4">
      <div>
        <h2 class="text-sm font-semibold text-zinc-100">Prompt</h2>
        <p class="mt-1 text-xs text-zinc-500">Generation and edit requests use the same frozen API contract.</p>
      </div>
      {#if responsesMode}
        <span class="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-xs font-medium text-cyan-200">Responses mode</span>
      {/if}
    </div>

    <textarea
      bind:value={prompt}
      maxlength="4000"
      rows="5"
      placeholder="Describe the image you want to create..."
      class="w-full resize-y rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3 text-sm leading-6 text-zinc-100 focus:border-emerald-500 focus:outline-none"
    ></textarea>
    <div class="mt-2 flex justify-end text-xs text-zinc-500">{promptLen}/4000</div>

    <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Model</span>
        <input bind:value={model} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Size</span>
        <button
          type="button"
          disabled={responsesMode || preview.loading}
          class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-left font-mono text-sm text-zinc-100 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
          on:click={() => (sizeDialogOpen = true)}
        >
          {responsesMode ? 'Disabled for Responses' : size}
        </button>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Quality</span>
        <select bind:value={quality} disabled={responsesMode || preview.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="auto">auto</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Quantity</span>
        <input bind:value={quantity} disabled={responsesMode || preview.loading} type="number" min="1" max="10" class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampQuantity} />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Format</span>
        <select bind:value={outputFormat} disabled={responsesMode || preview.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="png">png</option>
          <option value="jpeg">jpeg</option>
          <option value="webp">webp</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Compression</span>
        <input bind:value={outputCompression} disabled={responsesMode || preview.loading || outputFormat === 'png'} type="number" min="0" max="100" placeholder={outputFormat === 'png' ? 'Disabled for PNG' : '0-100'} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampCompression} />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Response format</span>
        <select bind:value={responseFormat} disabled={responsesMode || preview.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="">default</option>
          <option value="url">url</option>
          <option value="b64_json">b64_json</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">Webhook URL</span>
        <input bind:value={webhookUrl} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" placeholder="https://..." />
      </label>
    </div>

    <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="min-w-0">
        <input bind:this={editInput} type="file" accept="image/*" class="hidden" on:change={handleEditFile} />
        <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={openEditPicker}>
          Upload edit image
        </button>
        {#if editLabel}
          <button
            type="button"
            class="ml-3 inline-block max-w-[260px] truncate align-middle text-left text-xs font-medium text-emerald-300 underline decoration-emerald-500/40 underline-offset-4 hover:text-emerald-200"
            title={`Preview ${editLabel}`}
            on:click={openEditPreview}
          >
            {editLabel}
          </button>
        {/if}
      </div>
      <div class="flex gap-2">
        <button type="button" disabled={preview.loading} class="rounded-xl bg-zinc-700 px-4 py-3 text-sm font-semibold text-white hover:bg-zinc-600 disabled:cursor-not-allowed disabled:opacity-50" on:click={editImage}>
          Edits
        </button>
        <button type="button" disabled={preview.loading} class="rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" on:click={generateImage}>
          Generate
        </button>
      </div>
    </div>
  </section>

  <PreviewPanel
    loading={preview.loading}
    error={preview.error}
    job={preview.job}
    imageUrl={preview.imageUrl}
    filename={preview.filename}
    prompt={preview.prompt}
    onRegenerate={regenerate}
    onClear={clearPreview}
  />

  <GalleryGrid
    {gallery}
    filters={galleryFilters}
    loading={galleryLoading}
    onFilter={updateGalleryFilter}
    onResetFilters={resetGalleryFilters}
    onPage={(delta) => loadGallery(galleryPage + delta)}
    onFavorite={toggleFavorite}
    onDelete={deleteImage}
    onDeleteAll={deleteAllImages}
    onImport={importArchive}
    onOpen={(image) => (lightboxImage = image)}
    onEdit={prepareGalleryImageForEdit}
  />
</main>

<Lightbox
  open={Boolean(lightboxImage)}
  image={lightboxImage}
  onClose={() => (lightboxImage = null)}
  onEdit={prepareGalleryImageForEdit}
  onFavorite={toggleFavorite}
  onDelete={deleteImage}
  onCopyPrompt={copyPrompt}
  onCopyUrl={copyImageUrl}
/>

{#if editPreviewOpen && editPreviewUrl}
  <div class="fixed inset-0 z-[75] flex items-center justify-center bg-black/75 p-4">
    <button class="absolute inset-0" type="button" aria-label="Close edit image preview" on:click={() => (editPreviewOpen = false)}></button>
    <div class="relative flex max-h-[calc(100vh-32px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950 shadow-2xl">
      <div class="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div class="min-w-0">
          <h2 class="text-sm font-semibold text-zinc-100">Edit Source Preview</h2>
          <p class="mt-1 truncate text-xs text-zinc-500">{editPreviewLabel}</p>
        </div>
        <button type="button" class="rounded-lg px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" on:click={() => (editPreviewOpen = false)}>x</button>
      </div>
      <div class="flex min-h-0 flex-1 items-center justify-center bg-zinc-950 p-4">
        <img src={editPreviewUrl} alt={editPreviewLabel} class="max-h-[calc(100vh-140px)] max-w-full rounded-lg object-contain" />
      </div>
    </div>
  </div>
{/if}

<SizeDialog open={sizeDialogOpen} value={size} onApply={(nextSize) => (size = nextSize)} onClose={() => (sizeDialogOpen = false)} />
