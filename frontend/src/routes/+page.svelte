<script lang="ts">
  import { onMount } from 'svelte';
  import AccessGate from '$lib/components/AccessGate.svelte';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  import EditPreviewModal from '$lib/components/EditPreviewModal.svelte';
  import EditSourcePicker from '$lib/components/EditSourcePicker.svelte';
  import GalleryGrid from '$lib/components/GalleryGrid.svelte';
  import Header from '$lib/components/Header.svelte';
  import JobHistoryDrawer from '$lib/components/JobHistoryDrawer.svelte';
  import Lightbox from '$lib/components/Lightbox.svelte';
  import PreviewPanel from '$lib/components/PreviewPanel.svelte';
  import PromptForm from '$lib/components/PromptForm.svelte';
  import SettingsDrawer from '$lib/components/SettingsDrawer.svelte';
  import SizeDialog from '$lib/components/SizeDialog.svelte';
  import ToastHost from '$lib/components/ToastHost.svelte';
  import { apiFetch } from '$lib/api/client';
  import { t } from '$lib/i18n';
  import type { GalleryEntry, GenerateJobStatus, SettingsResponse } from '$lib/api/types';
  import { accessStore } from '$lib/stores/access';
  import { confirmStore } from '$lib/stores/confirm';
  import { galleryStore, readGalleryUrlState, writeGalleryUrlState } from '$lib/stores/gallery';
  import { jobsStore } from '$lib/stores/jobs';
  import { DEFAULT_PROMPT_MODEL, MAX_EDIT_SOURCE_IMAGES, editSourceStore, initialPromptFormState, previewStore, type PromptFormState } from '$lib/stores/preview';
  import { settingsStore } from '$lib/stores/settings';
  import { uiStore, type ToastOptions } from '$lib/stores/ui';
  import { copyText, galleryImageSize, imageUrl } from '$lib/utils/format';

  const VERSION_CHECK_TIMEOUT_MS = 4000;
  type JobsTab = 'running' | 'history';

  let version = '';
  let latestVersion = '';
  let versionHasUpdate = false;
  let releaseUrl: string | null = null;
  let lightboxImage: GalleryEntry | null = null;
  let jobsTab: JobsTab = 'running';
  let form: PromptFormState = { ...initialPromptFormState };
  let editPicker: EditSourcePicker;
  let editPreviewUrl = '';
  let editPreviewLabel = '';
  let lastActivePresetId = '';
  let lastActivePresetDefaultModel = DEFAULT_PROMPT_MODEL;
  let urlSyncReady = false;
  let applyingUrlState = false;
  let urlSyncQueued = false;
  let queuedUrlSyncMode: 'replace' | 'push' = 'replace';
  let lightboxLookupSeq = 0;

  $: activeJobsCount = $jobsStore.jobs.length;
  $: syncFormModelToActivePreset($settingsStore.settings);

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

      const latest = await fetchLatestVersion();
      latestVersion = latest?.latest_version ?? '';
      versionHasUpdate = Boolean(latest?.has_update);
    } catch {
      version = '';
      latestVersion = '';
      versionHasUpdate = false;
      releaseUrl = null;
    }
  }

  async function fetchLatestVersion(): Promise<{ latest_version: string | null; has_update: boolean } | null> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), VERSION_CHECK_TIMEOUT_MS);
    try {
      return await apiFetch<{ latest_version: string | null; has_update: boolean }>(
        '/api/version/latest',
        { signal: controller.signal },
        'loading latest version'
      );
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  async function loadInitialData() {
    await Promise.all([settingsStore.loadSettings(), jobsStore.loadJobs(), applyUrlStateToApp()]);
    urlSyncReady = true;
    syncUrlState();
    jobsStore.startJobsEvents();
  }

  function showToast(message: string, variant?: 'status' | 'error', options?: ToastOptions) {
    uiStore.showToast(message, variant, options);
  }

  function syncUrlState(mode: 'replace' | 'push' = 'replace') {
    if (!urlSyncReady || applyingUrlState || typeof window === 'undefined') return;

    const url = new URL(window.location.href);
    writeGalleryUrlState(url.searchParams, $galleryStore.page, $galleryStore.filters);

    if (lightboxImage) url.searchParams.set('image', lightboxImage.id);
    else url.searchParams.delete('image');

    if ($uiStore.jobsOpen) url.searchParams.set('jobs', jobsTab);
    else url.searchParams.delete('jobs');

    const nextUrl = `${url.pathname}${url.search}${url.hash}`;
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (nextUrl === currentUrl) return;
    window.history[mode === 'push' ? 'pushState' : 'replaceState']({}, '', nextUrl);
  }

  function queueUrlSync(mode: 'replace' | 'push' = 'replace') {
    if (mode === 'push') queuedUrlSyncMode = 'push';
    if (urlSyncQueued) return;
    urlSyncQueued = true;
    queueMicrotask(() => {
      urlSyncQueued = false;
      const nextMode = queuedUrlSyncMode;
      queuedUrlSyncMode = 'replace';
      syncUrlState(nextMode);
    });
  }

  function parseJobsTab(value: string | null): JobsTab | null {
    if (value === 'history' || value === 'running') return value;
    return null;
  }

  async function syncLightboxFromUrl(imageId: string | null | undefined) {
    const nextImageId = String(imageId || '').trim();
    if (!nextImageId) {
      lightboxImage = null;
      return;
    }

    const existing = $galleryStore.gallery?.images.find((image) => image.id === nextImageId);
    if (existing) {
      lightboxImage = existing;
      return;
    }

    const seq = ++lightboxLookupSeq;
    try {
      const image = await apiFetch<GalleryEntry>(`/api/gallery/${encodeURIComponent(nextImageId)}`, {}, 'loading gallery image');
      if (seq === lightboxLookupSeq) lightboxImage = image;
    } catch {
      if (seq !== lightboxLookupSeq) return;
      lightboxImage = null;
      showToast($t.messages.galleryImageNotFound, 'error');
    }
  }

  function openLightbox(image: GalleryEntry) {
    lightboxImage = image;
    queueUrlSync('push');
  }

  function closeLightbox() {
    lightboxImage = null;
    queueUrlSync('replace');
  }

  function openJobsDrawer(tab: JobsTab = jobsTab) {
    jobsTab = tab;
    setUi('jobsOpen', true);
    if (tab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) void jobsStore.loadJobHistory();
    queueUrlSync();
  }

  function closeJobsDrawer() {
    setUi('jobsOpen', false);
    queueUrlSync();
  }

  function setJobsTab(tab: JobsTab) {
    jobsTab = tab;
    if (tab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) void jobsStore.loadJobHistory();
    queueUrlSync();
  }

  async function applyUrlStateToApp() {
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    const state = readGalleryUrlState(url.searchParams);
    const nextJobsTab = parseJobsTab(url.searchParams.get('jobs'));
    const imageId = url.searchParams.get('image');

    applyingUrlState = true;
    try {
      galleryStore.setPageAndFilters(state.page, state.filters);
      jobsTab = nextJobsTab || 'running';
      setUi('jobsOpen', Boolean(nextJobsTab));

      if (nextJobsTab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) {
        void jobsStore.loadJobHistory();
      }

      await galleryStore.loadGallery(state.page);
      await syncLightboxFromUrl(imageId);
    } finally {
      applyingUrlState = false;
    }
    syncUrlState();
  }

  function setUi<K extends keyof typeof $uiStore>(key: K, value: (typeof $uiStore)[K]) {
    uiStore.setKey(key, value);
  }

  function activePreset(settings: SettingsResponse | null) {
    return settings?.presets.find((preset) => preset.id === settings.active_preset_id) || settings?.presets[0] || null;
  }

  function presetDefaultModel(settings: SettingsResponse | null) {
    const preset = activePreset(settings);
    return (preset?.default_model || settings?.default_model || DEFAULT_PROMPT_MODEL).trim() || DEFAULT_PROMPT_MODEL;
  }

  function syncFormModelToActivePreset(settings: SettingsResponse | null) {
    const preset = activePreset(settings);
    const nextPresetId = preset?.id || '';
    const nextDefaultModel = presetDefaultModel(settings);
    if (nextPresetId === lastActivePresetId && nextDefaultModel === lastActivePresetDefaultModel) return;

    const currentModel = form.model.trim();
    if (!currentModel || currentModel === lastActivePresetDefaultModel) {
      form = { ...form, model: nextDefaultModel };
    }
    lastActivePresetId = nextPresetId;
    lastActivePresetDefaultModel = nextDefaultModel;
  }

  function saveSettings(body: Record<string, unknown>) {
    void settingsStore.saveSettings(body, showToast).then(() => setUi('settingsOpen', false));
  }

  function createPreset() {
    void settingsStore.createPreset(showToast);
  }

  function activatePreset(presetId: string) {
    void settingsStore.activatePreset(presetId, showToast);
  }

  function deleteActivePreset() {
    void settingsStore.deleteActivePreset(showToast);
  }

  function checkPresetHealth(presetId: string) {
    void settingsStore.checkPresetHealth(presetId);
  }

  function updatePreviewFromJob(job: GenerateJobStatus) {
    previewStore.setPreview(jobsStore.previewFromJob(job, $previewStore));
    if (job.status !== 'queued' && job.status !== 'running') {
      void jobsStore.loadJobs();
      void jobsStore.refreshHistoryIfLoaded();
      if (job.status === 'success') void galleryStore.loadGallery(1);
    }
  }

  function trackJob(jobId: string) {
    jobsStore.trackJob(jobId, async (job) => updatePreviewFromJob(job), previewStore.setError);
  }

  function generateImage() {
    void previewStore.generateImage(form, jobsStore.makeQueuedPreview, trackJob, jobsStore.loadJobs);
  }

  function editImage() {
    void previewStore.editImage(form, $editSourceStore, jobsStore.makeQueuedPreview, trackJob, jobsStore.loadJobs);
  }

  function regenerate() {
    previewStore.regenerate(
      (next) => (form = { ...next, model: next.model.trim() || lastActivePresetDefaultModel || initialPromptFormState.model }),
      generateImage,
      editImage
    );
  }

  function clearPreview() {
    previewStore.clearPreview(jobsStore.closeActiveJobSource);
  }

  function prepareGalleryImageForEdit(image: GalleryEntry) {
    const nextLabel = $t.messages.galleryEditLabel(image.filename);
    if (!previewStore.setGalleryEditSource(image.id, nextLabel, imageUrl(image.filename), nextLabel)) {
      showToast($t.messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES), 'error');
      return;
    }
    form = { ...form, size: galleryImageSize(image) };
    closeLightbox();
    showToast($t.messages.galleryImageReady);
  }

  function handleEditFile(event: Event) {
    previewStore.handleEditFile(event);
  }

  function openEditPreview(sourceId: string) {
    const upload = $editSourceStore.files.find((source) => source.id === sourceId);
    if (upload) {
      editPreviewUrl = upload.previewUrl;
      editPreviewLabel = upload.previewLabel;
      setUi('editPreviewOpen', true);
      return;
    }
    if ($editSourceStore.selectedGalleryImageId === sourceId && $editSourceStore.galleryPreviewUrl) {
      editPreviewUrl = $editSourceStore.galleryPreviewUrl;
      editPreviewLabel = $editSourceStore.galleryPreviewLabel || $editSourceStore.galleryLabel;
      setUi('editPreviewOpen', true);
    }
  }

  function clearEditSource() {
    previewStore.clearEditSource();
    editPicker?.reset();
    setUi('editPreviewOpen', false);
    editPreviewUrl = '';
    editPreviewLabel = '';
  }

  async function batchFavoriteGallery(favorite: boolean) {
    await galleryStore.batchFavorite(favorite, showToast, (ids, nextFavorite) => {
      if (lightboxImage && ids.includes(lightboxImage.id)) lightboxImage = { ...lightboxImage, favorite: nextFavorite };
    });
  }

  async function batchDeleteGallery() {
    await galleryStore.batchDelete(showToast, (ids) => {
      if (lightboxImage && ids.includes(lightboxImage.id)) closeLightbox();
      if ($editSourceStore.selectedGalleryImageId && ids.includes($editSourceStore.selectedGalleryImageId)) {
        previewStore.clearGalleryEditSource($editSourceStore.selectedGalleryImageId);
        setUi('editPreviewOpen', false);
        editPreviewUrl = '';
        editPreviewLabel = '';
      }
    });
  }

  async function toggleFavorite(image: GalleryEntry) {
    await galleryStore.toggleFavorite(image, (next) => {
      if (lightboxImage?.id === image.id) lightboxImage = next;
    });
  }

  async function deleteImage(image: GalleryEntry) {
    await galleryStore.deleteImage(
      image,
      showToast,
      () => {
        if (lightboxImage?.id === image.id) closeLightbox();
      },
      () => {
        if ($editSourceStore.selectedGalleryImageId === image.id) {
          previewStore.clearGalleryEditSource(image.id);
          setUi('editPreviewOpen', false);
          editPreviewUrl = '';
          editPreviewLabel = '';
        }
      }
    );
  }

  async function deleteAllImages() {
    await galleryStore.deleteAll(showToast, () => {
      closeLightbox();
      previewStore.clearGalleryEditSource($editSourceStore.selectedGalleryImageId);
      setUi('editPreviewOpen', false);
      editPreviewUrl = '';
      editPreviewLabel = '';
      clearPreview();
    });
  }

  async function importArchive(file: File) {
    await galleryStore.importArchive(file, showToast);
  }

  async function copyPrompt(image: GalleryEntry) {
    await copyText(image.prompt);
    showToast($t.messages.promptCopied);
  }

  async function copyImageUrl(image: GalleryEntry) {
    await copyText(new URL(imageUrl(image.filename), window.location.origin).href);
    showToast($t.messages.imageUrlCopied);
  }

  function normalizeJobQuality(value: string | null | undefined): PromptFormState['quality'] {
    if (value === 'auto' || value === 'low' || value === 'medium' || value === 'high') return value;
    return initialPromptFormState.quality;
  }

  function normalizeJobOutputFormat(value: string | null | undefined): PromptFormState['outputFormat'] {
    if (value === 'png' || value === 'jpeg' || value === 'webp') return value;
    return initialPromptFormState.outputFormat;
  }

  function jobToPromptForm(job: GenerateJobStatus): PromptFormState {
    return {
      prompt: job.prompt || '',
      size: job.size || initialPromptFormState.size,
      model: job.model || lastActivePresetDefaultModel || initialPromptFormState.model,
      quality: normalizeJobQuality(job.quality),
      outputFormat: normalizeJobOutputFormat(job.output_format),
      outputCompression: job.output_compression === null || job.output_compression === undefined ? '' : String(job.output_compression),
      quantity: Math.min(Math.max(Number(job.n) || initialPromptFormState.quantity, 1), 10),
      responseFormat: job.response_format === 'url' || job.response_format === 'b64_json' ? job.response_format : '',
      webhookUrl: ''
    };
  }

  function useJobAsPrompt(job: GenerateJobStatus) {
    form = jobToPromptForm(job);
    closeJobsDrawer();
    showToast($t.messages.jobLoadedIntoPrompt);
  }

  function retryJob(job: GenerateJobStatus) {
    form = jobToPromptForm(job);
    closeJobsDrawer();
    if (job.operation === 'edit') {
      if (!$editSourceStore.files.length && !$editSourceStore.selectedGalleryImageId) {
        previewStore.setError($t.messages.editRetryNeedsSource);
        showToast($t.messages.editRetryNeedsSource, 'error');
        return;
      }
      editImage();
      return;
    }
    generateImage();
  }

  $: if (urlSyncReady) {
    $galleryStore.page;
    $galleryStore.filters;
    $uiStore.jobsOpen;
    jobsTab;
    lightboxImage?.id;
    queueUrlSync();
  }

  onMount(() => {
    accessStore.installUnauthorizedHandler();
    void loadVersion();
    void accessStore.checkAccess(loadInitialData);

    const popstate = () => {
      void applyUrlStateToApp();
    };
    window.addEventListener('popstate', popstate);

    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if ($uiStore.editPreviewOpen) setUi('editPreviewOpen', false);
      else if (lightboxImage) closeLightbox();
      else if ($uiStore.sizeDialogOpen) setUi('sizeDialogOpen', false);
      else if ($uiStore.jobsOpen) closeJobsDrawer();
      else if ($uiStore.settingsOpen) setUi('settingsOpen', false);
    };
    window.addEventListener('keydown', keydown);
    return () => {
      window.removeEventListener('popstate', popstate);
      window.removeEventListener('keydown', keydown);
      jobsStore.cleanup();
      galleryStore.cleanup();
      previewStore.cleanup();
      uiStore.cleanup();
    };
  });
</script>

<svelte:head>
  <title>GPT Image Panel</title>
</svelte:head>

<AccessGate visible={$accessStore.gateVisible} error={$accessStore.error} loading={$accessStore.loading} onUnlock={(key) => accessStore.unlockAccess(key, loadInitialData)} />
<Header
  {version}
  {latestVersion}
  hasVersionUpdate={versionHasUpdate}
  {releaseUrl}
  {activeJobsCount}
  onOpenJobs={openJobsDrawer}
  onOpenSettings={() => setUi('settingsOpen', true)}
/>

<ConfirmDialog request={$confirmStore.request} />

<SettingsDrawer
  open={$uiStore.settingsOpen}
  settings={$settingsStore.settings}
  saving={$settingsStore.saving}
  health={$settingsStore.health}
  healthChecking={$settingsStore.healthChecking}
  onClose={() => setUi('settingsOpen', false)}
  onSave={saveSettings}
  onCreate={createPreset}
  onActivate={activatePreset}
  onDelete={deleteActivePreset}
  onHealthCheck={checkPresetHealth}
/>

<JobHistoryDrawer
  open={$uiStore.jobsOpen}
  activeTab={jobsTab}
  jobs={$jobsStore.jobs}
  historyJobs={$jobsStore.historyJobs}
  historyLoading={$jobsStore.historyLoading}
  historyLoaded={$jobsStore.historyLoaded}
  historyHasMore={$jobsStore.historyHasMore}
  selectedIds={$jobsStore.selectedIds}
  onClose={closeJobsDrawer}
  onTabChange={setJobsTab}
  onRefresh={jobsStore.loadJobs}
  onRefreshHistory={jobsStore.loadJobHistory}
  onLoadMoreHistory={jobsStore.loadMoreJobHistory}
  onToggle={jobsStore.toggleSelection}
  onToggleAll={jobsStore.toggleAll}
  onCancelSelected={jobsStore.cancelSelected}
  onUseJob={useJobAsPrompt}
  onRetryJob={retryJob}
/>

<main class="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
  <ToastHost toast={$uiStore.toast} />

  <PromptForm
    bind:form
    apiPath={$settingsStore.settings?.api_path || '/v1/images/generations'}
    loading={$previewStore.loading}
    onGenerate={generateImage}
    onEdit={editImage}
    onOpenSize={() => setUi('sizeDialogOpen', true)}
  >
    <EditSourcePicker
      slot="edit-source"
      bind:this={editPicker}
      sources={[
        ...($editSourceStore.selectedGalleryImageId
          ? [
              {
                id: $editSourceStore.selectedGalleryImageId,
                label: $editSourceStore.galleryLabel || $editSourceStore.galleryPreviewLabel,
                kind: 'gallery' as const
              }
            ]
          : []),
        ...$editSourceStore.files.map((source) => ({
          id: source.id,
          label: source.label,
          kind: 'upload' as const
        }))
      ]}
      onChange={handleEditFile}
      onPreview={openEditPreview}
      onClear={clearEditSource}
    />
  </PromptForm>

  <PreviewPanel
    loading={$previewStore.loading}
    error={$previewStore.error}
    job={$previewStore.job}
    imageUrl={$previewStore.imageUrl}
    filename={$previewStore.filename}
    prompt={$previewStore.prompt}
    onRegenerate={regenerate}
    onClear={clearPreview}
  />

  <GalleryGrid
    gallery={$galleryStore.gallery}
    filters={$galleryStore.filters}
    loading={$galleryStore.loading}
    onFilter={galleryStore.updateFilter}
    onResetFilters={galleryStore.resetFilters}
    onPage={galleryStore.loadGallery}
    onLoadStats={() => galleryStore.loadGallery($galleryStore.page, true)}
    onFavorite={toggleFavorite}
    onDelete={deleteImage}
    onDeleteAll={deleteAllImages}
    onImport={importArchive}
    onOpen={openLightbox}
    onEdit={prepareGalleryImageForEdit}
    selectionMode={$galleryStore.selectionMode}
    selectedIds={$galleryStore.selectedIds}
    onSelectionMode={galleryStore.setSelectionMode}
    onToggleSelection={galleryStore.toggleSelection}
    onSelectPage={galleryStore.selectPage}
    onClearSelection={galleryStore.clearSelection}
    onBatchDelete={batchDeleteGallery}
    onBatchFavorite={batchFavoriteGallery}
    onBatchDownload={galleryStore.batchDownload}
  />
</main>

<Lightbox
  open={Boolean(lightboxImage)}
  image={lightboxImage}
  onClose={closeLightbox}
  onEdit={prepareGalleryImageForEdit}
  onFavorite={toggleFavorite}
  onDelete={deleteImage}
  onCopyPrompt={copyPrompt}
  onCopyUrl={copyImageUrl}
/>

<EditPreviewModal
  open={$uiStore.editPreviewOpen}
  url={editPreviewUrl}
  label={editPreviewLabel}
  onClose={() => setUi('editPreviewOpen', false)}
/>

<SizeDialog open={$uiStore.sizeDialogOpen} value={form.size} onApply={(nextSize) => (form = { ...form, size: nextSize })} onClose={() => setUi('sizeDialogOpen', false)} />
