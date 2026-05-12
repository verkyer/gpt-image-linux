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
  import { t } from '$lib/i18n';
  import type {
    AccessStatus,
    ApiPath,
    GalleryEntry,
    GalleryBatchResponse,
    GalleryResponse,
    GenerateJobResponse,
    GenerateJobStatus,
    GenerateRequestBody,
    PresetHealthResponse,
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
  const VERSION_BRANCH = 'main';

  let version = '';
  let latestVersion = '';
  let versionHasUpdate = false;
  let releaseUrl: string | null = null;
  let accessVisible = true;
  let accessLoading = false;
  let accessError = '';
  let settings: SettingsResponse | null = null;
  let settingsOpen = false;
  let settingsSaving = false;
  let settingsHealthChecking = false;
  let settingsHealth: PresetHealthResponse | null = null;
  let jobsOpen = false;
  let gallery: GalleryResponse | null = null;
  let galleryLoading = false;
  let galleryPage = 1;
  let galleryFilters: GalleryFilters = { ...defaultGalleryFilters };
  let gallerySelectionMode = false;
  let selectedGalleryIds = new Set<string>();
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
      const data = await apiFetch<{ version: string; github_repo?: string; release_url: string | null }>(
        '/api/version',
        {},
        'loading version'
      );
      version = data.version;
      releaseUrl = data.release_url;
      latestVersion = '';
      versionHasUpdate = false;

      try {
        const latest = await fetchLatestVersion(data.github_repo);
        latestVersion = latest;
        versionHasUpdate = compareVersions(latest, version) > 0;
      } catch {
        latestVersion = '';
        versionHasUpdate = false;
      }
    } catch {
      version = '';
      latestVersion = '';
      versionHasUpdate = false;
      releaseUrl = null;
    }
  }

  async function fetchLatestVersion(githubRepo?: string) {
    if (!githubRepo) {
      return '';
    }

    const url = `https://raw.githubusercontent.com/${githubRepo}/${VERSION_BRANCH}/VERSION`;
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      throw new Error(`Version check failed: ${res.status}`);
    }
    return normalizeVersion(await res.text());
  }

  function compareVersions(a: string, b: string) {
    const left = versionParts(a);
    const right = versionParts(b);
    const length = Math.max(left.length, right.length);

    for (let i = 0; i < length; i += 1) {
      const l = left[i] || 0;
      const r = right[i] || 0;
      if (l > r) return 1;
      if (l < r) return -1;
    }

    return 0;
  }

  function versionParts(value: string) {
    return normalizeVersion(value)
      .split('.')
      .map((part) => Number.parseInt(part, 10))
      .map((part) => (Number.isFinite(part) ? part : 0));
  }

  function normalizeVersion(value: string) {
    return String(value || '')
      .trim()
      .replace(/^v/i, '');
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
      accessError = error instanceof Error ? error.message : $t.messages.accessCheckFailed;
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
      if (!data.authenticated) throw new Error($t.messages.invalidAccessKey);
      accessVisible = false;
      await loadInitialData();
    } catch (error) {
      accessError = error instanceof Error ? error.message : $t.messages.invalidAccessKey;
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
      showToast($t.messages.apiUrlRequired);
      return;
    }
    if (body.api_key !== null && !String(body.api_key || '').trim()) {
      showToast($t.messages.apiKeyRequired);
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
      settingsHealth = null;
      settingsOpen = false;
      showToast($t.messages.presetSaved);
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
    settingsHealth = null;
    showToast($t.messages.presetCreated);
  }

  async function activatePreset(presetId: string) {
    if (!presetId || presetId === settings?.active_preset_id) return;
    settings = await apiFetch<SettingsResponse>(
      `/api/settings/presets/${encodeURIComponent(presetId)}/activate`,
      { method: 'POST' },
      'switching preset'
    );
    settingsHealth = null;
    showToast($t.messages.presetSwitched);
  }

  async function deleteActivePreset() {
    if (!settings || settings.presets.length <= 1) return;
    const active = settings.presets.find((preset) => preset.id === settings?.active_preset_id);
    if (!active || !confirm($t.messages.deletePresetConfirm(active.name || $t.common.untitledPreset))) return;
    settings = await apiFetch<SettingsResponse>(
      `/api/settings/presets/${encodeURIComponent(active.id)}`,
      { method: 'DELETE' },
      'deleting preset'
    );
    settingsHealth = null;
    showToast($t.messages.presetDeleted);
  }

  async function checkPresetHealth(presetId: string) {
    if (!presetId) return;
    settingsHealthChecking = true;
    try {
      settingsHealth = await apiFetch<PresetHealthResponse>(
        `/api/settings/presets/${encodeURIComponent(presetId)}/health`,
        { method: 'POST' },
        'checking preset health'
      );
    } finally {
      settingsHealthChecking = false;
    }
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
      setPreviewError($t.messages.promptRequired);
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
      setPreviewError($t.messages.editSourceRequired);
      return;
    }

    const body = buildRequestBody();
    if (!body.prompt) {
      setPreviewError($t.messages.promptRequired);
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
        message: operation === 'edit' ? $t.messages.queuedEdit : $t.messages.queuedGeneration,
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
      setPreviewError(error instanceof Error ? error.message : $t.messages.jobLoadFailed);
    }
  }

  async function updatePreviewFromJob(job: GenerateJobStatus) {
    const image = job.image_url || '';
    preview = {
      loading: ACTIVE_STATUSES.has(job.status),
      error: job.status === 'error' ? job.error || job.message || $t.messages.jobFailed : '',
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
      const visibleIds = new Set(data.images.map((image) => image.id));
      selectedGalleryIds = new Set([...selectedGalleryIds].filter((id) => visibleIds.has(id)));
      if (selectedGalleryIds.size === 0) gallerySelectionMode = false;
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

  function setGallerySelectionMode(enabled: boolean) {
    gallerySelectionMode = enabled;
    if (!enabled) selectedGalleryIds = new Set();
  }

  function toggleGallerySelection(image: GalleryEntry) {
    const next = new Set(selectedGalleryIds);
    if (next.has(image.id)) {
      next.delete(image.id);
    } else {
      next.add(image.id);
    }
    selectedGalleryIds = next;
  }

  function selectGalleryPage() {
    selectedGalleryIds = new Set(gallery?.images.map((image) => image.id) || []);
  }

  function clearGallerySelection() {
    selectedGalleryIds = new Set();
  }

  async function batchFavoriteGallery(favorite: boolean) {
    const ids = [...selectedGalleryIds];
    if (!ids.length) return;
    const result = await apiFetch<GalleryBatchResponse>(
      '/api/gallery/batch/favorite',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, favorite })
      },
      'updating selected favorites'
    );
    await loadGallery(galleryPage);
    if (lightboxImage && ids.includes(lightboxImage.id)) {
      lightboxImage = { ...lightboxImage, favorite };
    }
    showToast($t.messages.selectedImagesFavorited(result.count));
  }

  async function batchDeleteGallery() {
    const ids = [...selectedGalleryIds];
    if (!ids.length || !confirm($t.messages.deleteSelectedConfirm(ids.length))) return;
    const result = await apiFetch<GalleryBatchResponse>(
      '/api/gallery/batch/delete',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
      },
      'deleting selected images'
    );
    if (lightboxImage && ids.includes(lightboxImage.id)) lightboxImage = null;
    if (selectedGalleryImageId && ids.includes(selectedGalleryImageId)) clearEditSource();
    clearGallerySelection();
    await loadGallery(galleryPage);
    showToast($t.messages.selectedImagesDeleted(result.count));
  }

  async function batchDownloadGallery() {
    const ids = [...selectedGalleryIds];
    if (!ids.length) return;
    const response = await fetch('/api/gallery/batch/download', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/zip', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids })
    });
    if (!response.ok) {
      throw new Error($t.messages.requestFailed);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'gpt-images-selected.zip';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
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
    if (!confirm($t.messages.deleteImageConfirm)) return;
    await apiFetch(`/api/gallery/${encodeURIComponent(image.id)}`, { method: 'DELETE' }, 'deleting image');
    if (lightboxImage?.id === image.id) lightboxImage = null;
    if (selectedGalleryImageId === image.id) clearEditSource();
    await loadGallery(galleryPage);
    showToast($t.messages.imageDeleted);
  }

  async function deleteAllImages() {
    if (!confirm($t.messages.deleteAllConfirm)) return;
    await apiFetch('/api/gallery', { method: 'DELETE' }, 'deleting all images');
    lightboxImage = null;
    clearEditSource();
    clearPreview();
    await loadGallery(1);
    showToast($t.messages.allImagesDeleted);
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
    showToast($t.messages.imported(result.imported));
  }

  function prepareGalleryImageForEdit(image: GalleryEntry) {
    editFile = null;
    selectedGalleryImageId = image.id;
    editLabel = $t.messages.galleryEditLabel(image.filename);
    setEditPreview(imageUrl(image.filename), editLabel);
    if (editInput) editInput.value = '';
    lightboxImage = null;
    showToast($t.messages.galleryImageReady);
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
      setPreviewError($t.messages.imageUploadRequired);
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
    if (file.type.startsWith('image/') && file.type !== 'image/svg+xml') return true;
    return /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|tiff?|webp)$/i.test(file.name);
  }

  async function copyPrompt(image: GalleryEntry) {
    await copyText(image.prompt);
    showToast($t.messages.promptCopied);
  }

  async function copyImageUrl(image: GalleryEntry) {
    await copyText(new URL(imageUrl(image.filename), window.location.origin).href);
    showToast($t.messages.imageUrlCopied);
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
<Header
  {version}
  {latestVersion}
  hasVersionUpdate={versionHasUpdate}
  {releaseUrl}
  {activeJobsCount}
  onOpenJobs={() => (jobsOpen = true)}
  onOpenSettings={() => (settingsOpen = true)}
/>

<SettingsDrawer
  open={settingsOpen}
  {settings}
  saving={settingsSaving}
  health={settingsHealth}
  healthChecking={settingsHealthChecking}
  onClose={() => (settingsOpen = false)}
  onSave={saveSettings}
  onCreate={createPreset}
  onActivate={activatePreset}
  onDelete={deleteActivePreset}
  onHealthCheck={checkPresetHealth}
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
        <h2 class="text-sm font-semibold text-zinc-100">{$t.promptForm.title}</h2>
        <p class="mt-1 text-xs text-zinc-500">{$t.promptForm.subtitle}</p>
      </div>
      {#if responsesMode}
        <span class="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-xs font-medium text-cyan-200">{$t.promptForm.responsesMode}</span>
      {/if}
    </div>

    <textarea
      bind:value={prompt}
      maxlength="4000"
      rows="5"
      placeholder={$t.promptForm.placeholder}
      class="w-full resize-y rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3 text-sm leading-6 text-zinc-100 focus:border-emerald-500 focus:outline-none"
    ></textarea>
    <div class="mt-2 flex justify-end text-xs text-zinc-500">{promptLen}/4000</div>

    <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.common.model}</span>
        <input bind:value={model} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.common.size}</span>
        <button
          type="button"
          disabled={responsesMode || preview.loading}
          class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-left font-mono text-sm text-zinc-100 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
          on:click={() => (sizeDialogOpen = true)}
        >
          {responsesMode ? $t.promptForm.disabledForResponses : size}
        </button>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.quality}</span>
        <select bind:value={quality} disabled={responsesMode || preview.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="auto">auto</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.quantity}</span>
        <input bind:value={quantity} disabled={responsesMode || preview.loading} type="number" min="1" max="10" class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampQuantity} />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.format}</span>
        <select bind:value={outputFormat} disabled={responsesMode || preview.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="png">png</option>
          <option value="jpeg">jpeg</option>
          <option value="webp">webp</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.compression}</span>
        <input bind:value={outputCompression} disabled={responsesMode || preview.loading || outputFormat === 'png'} type="number" min="0" max="100" placeholder={outputFormat === 'png' ? $t.promptForm.disabledForPng : '0-100'} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampCompression} />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.responseFormat}</span>
        <select bind:value={responseFormat} disabled={responsesMode || preview.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="">{$t.promptForm.defaultResponseFormat}</option>
          <option value="url">url</option>
          <option value="b64_json">b64_json</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.webhookUrl}</span>
        <input bind:value={webhookUrl} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" placeholder="https://..." />
      </label>
    </div>

    <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="min-w-0">
        <input bind:this={editInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp,image/heic,image/heif,image/x-icon,image/tiff" class="hidden" on:change={handleEditFile} />
        <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={openEditPicker}>
          {$t.promptForm.uploadEditImage}
        </button>
        {#if editLabel}
          <button
            type="button"
            class="ml-3 inline-block max-w-[260px] truncate align-middle text-left text-xs font-medium text-emerald-300 underline decoration-emerald-500/40 underline-offset-4 hover:text-emerald-200"
            title={$t.promptForm.previewEditLabel(editLabel)}
            on:click={openEditPreview}
          >
            {editLabel}
          </button>
        {/if}
      </div>
      <div class="flex gap-2">
        <button type="button" disabled={preview.loading} class="rounded-xl bg-zinc-700 px-4 py-3 text-sm font-semibold text-white hover:bg-zinc-600 disabled:cursor-not-allowed disabled:opacity-50" on:click={editImage}>
          {$t.promptForm.edits}
        </button>
        <button type="button" disabled={preview.loading} class="rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" on:click={generateImage}>
          {$t.promptForm.generate}
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
    selectionMode={gallerySelectionMode}
    selectedIds={selectedGalleryIds}
    onSelectionMode={setGallerySelectionMode}
    onToggleSelection={toggleGallerySelection}
    onSelectPage={selectGalleryPage}
    onClearSelection={clearGallerySelection}
    onBatchDelete={batchDeleteGallery}
    onBatchFavorite={batchFavoriteGallery}
    onBatchDownload={batchDownloadGallery}
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
    <button class="absolute inset-0" type="button" aria-label={$t.promptForm.closeEditPreview} on:click={() => (editPreviewOpen = false)}></button>
    <div class="relative flex max-h-[calc(100vh-32px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950 shadow-2xl">
      <div class="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div class="min-w-0">
          <h2 class="text-sm font-semibold text-zinc-100">{$t.promptForm.editSourcePreview}</h2>
          <p class="mt-1 truncate text-xs text-zinc-500">{editPreviewLabel}</p>
        </div>
        <button type="button" class="rounded-lg px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.promptForm.closeEditPreview} on:click={() => (editPreviewOpen = false)}>x</button>
      </div>
      <div class="flex min-h-0 flex-1 items-center justify-center bg-zinc-950 p-4">
        <img src={editPreviewUrl} alt={editPreviewLabel} class="max-h-[calc(100vh-140px)] max-w-full rounded-lg object-contain" />
      </div>
    </div>
  </div>
{/if}

<SizeDialog open={sizeDialogOpen} value={size} onApply={(nextSize) => (size = nextSize)} onClose={() => (sizeDialogOpen = false)} />
