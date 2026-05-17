<script lang="ts">
  import { onMount } from 'svelte';
  import AccessGate from '$lib/components/AccessGate.svelte';
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
  import type { GalleryEntry, GenerateJobStatus } from '$lib/api/types';
  import { accessStore } from '$lib/stores/access';
  import { galleryStore } from '$lib/stores/gallery';
  import { jobsStore } from '$lib/stores/jobs';
  import { editSourceStore, initialPromptFormState, previewStore, type PromptFormState } from '$lib/stores/preview';
  import { settingsStore } from '$lib/stores/settings';
  import { uiStore } from '$lib/stores/ui';
  import { copyText, galleryImageSize, imageUrl } from '$lib/utils/format';

  const VERSION_CHECK_TIMEOUT_MS = 4000;

  let version = '';
  let latestVersion = '';
  let versionHasUpdate = false;
  let releaseUrl: string | null = null;
  let lightboxImage: GalleryEntry | null = null;
  let form: PromptFormState = { ...initialPromptFormState };
  let editPicker: EditSourcePicker;

  $: activeJobsCount = $jobsStore.jobs.length;

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
    await Promise.all([settingsStore.loadSettings(), galleryStore.loadGallery(1), jobsStore.loadJobs()]);
    jobsStore.startJobsEvents();
  }

  function showToast(message: string) {
    uiStore.showToast(message);
  }

  function setUi<K extends keyof typeof $uiStore>(key: K, value: (typeof $uiStore)[K]) {
    uiStore.setKey(key, value);
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

  function openJobsDrawer() {
    setUi('jobsOpen', true);
    void jobsStore.loadJobHistory();
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
    previewStore.regenerate((next) => (form = next), generateImage, editImage);
  }

  function clearPreview() {
    previewStore.clearPreview(jobsStore.closeActiveJobSource);
  }

  function prepareGalleryImageForEdit(image: GalleryEntry) {
    previewStore.cleanup();
    editSourceStore.set({
      file: null,
      selectedGalleryImageId: image.id,
      label: $t.messages.galleryEditLabel(image.filename),
      previewUrl: imageUrl(image.filename),
      previewLabel: $t.messages.galleryEditLabel(image.filename)
    });
    form = { ...form, size: galleryImageSize(image) };
    editPicker?.reset();
    lightboxImage = null;
    showToast($t.messages.galleryImageReady);
  }

  function handleEditFile(event: Event) {
    previewStore.handleEditFile(event);
  }

  function openEditPreview() {
    if ($editSourceStore.previewUrl) setUi('editPreviewOpen', true);
  }

  function clearEditSource() {
    previewStore.clearEditSource();
    editPicker?.reset();
  }

  async function batchFavoriteGallery(favorite: boolean) {
    await galleryStore.batchFavorite(favorite, showToast, (ids, nextFavorite) => {
      if (lightboxImage && ids.includes(lightboxImage.id)) lightboxImage = { ...lightboxImage, favorite: nextFavorite };
    });
  }

  async function batchDeleteGallery() {
    await galleryStore.batchDelete(showToast, (ids) => {
      if (lightboxImage && ids.includes(lightboxImage.id)) lightboxImage = null;
      if ($editSourceStore.selectedGalleryImageId && ids.includes($editSourceStore.selectedGalleryImageId)) clearEditSource();
    });
  }

  async function toggleFavorite(image: GalleryEntry) {
    await galleryStore.toggleFavorite(image, (next) => {
      if (lightboxImage?.id === image.id) lightboxImage = next;
    });
  }

  async function deleteImage(image: GalleryEntry) {
    await galleryStore.deleteImage(image, showToast, () => {
      if (lightboxImage?.id === image.id) lightboxImage = null;
      if ($editSourceStore.selectedGalleryImageId === image.id) clearEditSource();
    });
  }

  async function deleteAllImages() {
    await galleryStore.deleteAll(showToast, () => {
      lightboxImage = null;
      clearEditSource();
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
      model: job.model || initialPromptFormState.model,
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
    setUi('jobsOpen', false);
    showToast($t.messages.jobLoadedIntoPrompt);
  }

  function retryJob(job: GenerateJobStatus) {
    form = jobToPromptForm(job);
    setUi('jobsOpen', false);
    if (job.operation === 'edit') {
      if (!$editSourceStore.file && !$editSourceStore.selectedGalleryImageId) {
        previewStore.setError($t.messages.editRetryNeedsSource);
        showToast($t.messages.editRetryNeedsSource);
        return;
      }
      editImage();
      return;
    }
    generateImage();
  }

  onMount(() => {
    accessStore.installUnauthorizedHandler();
    void loadVersion();
    void accessStore.checkAccess(loadInitialData);

    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if ($uiStore.editPreviewOpen) setUi('editPreviewOpen', false);
      else if (lightboxImage) lightboxImage = null;
      else if ($uiStore.sizeDialogOpen) setUi('sizeDialogOpen', false);
      else if ($uiStore.jobsOpen) setUi('jobsOpen', false);
      else if ($uiStore.settingsOpen) setUi('settingsOpen', false);
    };
    window.addEventListener('keydown', keydown);
    return () => {
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
  jobs={$jobsStore.jobs}
  historyJobs={$jobsStore.historyJobs}
  historyLoading={$jobsStore.historyLoading}
  historyLoaded={$jobsStore.historyLoaded}
  selectedIds={$jobsStore.selectedIds}
  onClose={() => setUi('jobsOpen', false)}
  onRefresh={jobsStore.loadJobs}
  onRefreshHistory={jobsStore.loadJobHistory}
  onToggle={jobsStore.toggleSelection}
  onToggleAll={jobsStore.toggleAll}
  onCancelSelected={jobsStore.cancelSelected}
  onUseJob={useJobAsPrompt}
  onRetryJob={retryJob}
/>

<main class="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
  <ToastHost message={$uiStore.toast} />

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
      label={$editSourceStore.label}
      onChange={handleEditFile}
      onPreview={openEditPreview}
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
    onPage={(delta) => galleryStore.loadGallery($galleryStore.page + delta)}
    onLoadStats={() => galleryStore.loadGallery($galleryStore.page, true)}
    onFavorite={toggleFavorite}
    onDelete={deleteImage}
    onDeleteAll={deleteAllImages}
    onImport={importArchive}
    onOpen={(image) => (lightboxImage = image)}
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
  onClose={() => (lightboxImage = null)}
  onEdit={prepareGalleryImageForEdit}
  onFavorite={toggleFavorite}
  onDelete={deleteImage}
  onCopyPrompt={copyPrompt}
  onCopyUrl={copyImageUrl}
/>

<EditPreviewModal
  open={$uiStore.editPreviewOpen}
  url={$editSourceStore.previewUrl}
  label={$editSourceStore.previewLabel}
  onClose={() => setUi('editPreviewOpen', false)}
/>

<SizeDialog open={$uiStore.sizeDialogOpen} value={form.size} onApply={(nextSize) => (form = { ...form, size: nextSize })} onClose={() => setUi('sizeDialogOpen', false)} />
