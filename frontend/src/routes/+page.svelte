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

  const VERSION_BRANCH = 'main';

  let version = '';
  let latestVersion = '';
  let versionHasUpdate = false;
  let releaseUrl: string | null = null;
  let lightboxImage: GalleryEntry | null = null;
  let form: PromptFormState = { ...initialPromptFormState };
  let editInput: HTMLInputElement;

  $: responsesMode = $settingsStore.settings?.api_path === '/v1/responses';
  $: activeJobsCount = $jobsStore.jobs.length;
  $: promptLen = form.prompt.length;

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
    if (!githubRepo) return '';
    const url = `https://raw.githubusercontent.com/${githubRepo}/${VERSION_BRANCH}/VERSION`;
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Version check failed: ${res.status}`);
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

  function updatePreviewFromJob(job: GenerateJobStatus) {
    previewStore.setPreview(jobsStore.previewFromJob(job, $previewStore));
    if (job.status !== 'queued' && job.status !== 'running') {
      void jobsStore.loadJobs();
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
    if (editInput) editInput.value = '';
    lightboxImage = null;
    showToast($t.messages.galleryImageReady);
  }

  function handleEditFile(event: Event) {
    previewStore.handleEditFile(event, editInput);
  }

  function openEditPicker() {
    editInput?.click();
  }

  function openEditPreview() {
    if ($editSourceStore.previewUrl) setUi('editPreviewOpen', true);
  }

  function clearEditSource() {
    previewStore.clearEditSource(editInput);
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

  function clampQuantity() {
    form = { ...form, quantity: Math.min(Math.max(Number(form.quantity) || 1, 1), 10) };
  }

  function clampCompression() {
    if (form.outputCompression === '') return;
    form = { ...form, outputCompression: String(Math.min(Math.max(Number(form.outputCompression) || 0, 0), 100)) };
  }

  $: if (form.outputFormat === 'png' && form.outputCompression !== '') form = { ...form, outputCompression: '' };

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
  onOpenJobs={() => setUi('jobsOpen', true)}
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
  selectedIds={$jobsStore.selectedIds}
  onClose={() => setUi('jobsOpen', false)}
  onRefresh={jobsStore.loadJobs}
  onToggle={jobsStore.toggleSelection}
  onToggleAll={jobsStore.toggleAll}
  onCancelSelected={jobsStore.cancelSelected}
/>

<main class="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
  {#if $uiStore.toast}
    <div class="fixed bottom-5 right-5 z-[90] rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-zinc-100 shadow-2xl">
      {$uiStore.toast}
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
      bind:value={form.prompt}
      maxlength="4000"
      rows="5"
      placeholder={$t.promptForm.placeholder}
      class="w-full resize-y rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3 text-sm leading-6 text-zinc-100 focus:border-emerald-500 focus:outline-none"
    ></textarea>
    <div class="mt-2 flex justify-end text-xs text-zinc-500">{promptLen}/4000</div>

    <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.common.model}</span>
        <input bind:value={form.model} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.common.size}</span>
        <button
          type="button"
          disabled={responsesMode || $previewStore.loading}
          class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-left font-mono text-sm text-zinc-100 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
          on:click={() => setUi('sizeDialogOpen', true)}
        >
          {responsesMode ? $t.promptForm.disabledForResponses : form.size}
        </button>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.quality}</span>
        <select bind:value={form.quality} disabled={responsesMode || $previewStore.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="auto">auto</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.quantity}</span>
        <input bind:value={form.quantity} disabled={responsesMode || $previewStore.loading} type="number" min="1" max="10" class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampQuantity} />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.format}</span>
        <select bind:value={form.outputFormat} disabled={responsesMode || $previewStore.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="png">png</option>
          <option value="jpeg">jpeg</option>
          <option value="webp">webp</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.compression}</span>
        <input bind:value={form.outputCompression} disabled={responsesMode || $previewStore.loading || form.outputFormat === 'png'} type="number" min="0" max="100" placeholder={form.outputFormat === 'png' ? $t.promptForm.disabledForPng : '0-100'} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampCompression} />
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.responseFormat}</span>
        <select bind:value={form.responseFormat} disabled={responsesMode || $previewStore.loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
          <option value="">{$t.promptForm.defaultResponseFormat}</option>
          <option value="url">url</option>
          <option value="b64_json">b64_json</option>
        </select>
      </label>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.webhookUrl}</span>
        <input bind:value={form.webhookUrl} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" placeholder="https://..." />
      </label>
    </div>

    <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="min-w-0">
        <input bind:this={editInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp,image/heic,image/heif,image/x-icon,image/tiff" class="hidden" on:change={handleEditFile} />
        <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={openEditPicker}>
          {$t.promptForm.uploadEditImage}
        </button>
        {#if $editSourceStore.label}
          <button
            type="button"
            class="ml-3 inline-block max-w-[260px] truncate align-middle text-left text-xs font-medium text-emerald-300 underline decoration-emerald-500/40 underline-offset-4 hover:text-emerald-200"
            title={$t.promptForm.previewEditLabel($editSourceStore.label)}
            on:click={openEditPreview}
          >
            {$editSourceStore.label}
          </button>
        {/if}
      </div>
      <div class="flex gap-2">
        <button type="button" disabled={$previewStore.loading} class="rounded-xl bg-zinc-700 px-4 py-3 text-sm font-semibold text-white hover:bg-zinc-600 disabled:cursor-not-allowed disabled:opacity-50" on:click={editImage}>
          {$t.promptForm.edits}
        </button>
        <button type="button" disabled={$previewStore.loading} class="rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" on:click={generateImage}>
          {$t.promptForm.generate}
        </button>
      </div>
    </div>
  </section>

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

{#if $uiStore.editPreviewOpen && $editSourceStore.previewUrl}
  <div class="fixed inset-0 z-[75] flex items-center justify-center bg-black/75 p-4">
    <button class="absolute inset-0" type="button" aria-label={$t.promptForm.closeEditPreview} on:click={() => setUi('editPreviewOpen', false)}></button>
    <div class="relative flex max-h-[calc(100vh-32px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950 shadow-2xl">
      <div class="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div class="min-w-0">
          <h2 class="text-sm font-semibold text-zinc-100">{$t.promptForm.editSourcePreview}</h2>
          <p class="mt-1 truncate text-xs text-zinc-500">{$editSourceStore.previewLabel}</p>
        </div>
        <button type="button" class="rounded-lg px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.promptForm.closeEditPreview} on:click={() => setUi('editPreviewOpen', false)}>x</button>
      </div>
      <div class="flex min-h-0 flex-1 items-center justify-center bg-zinc-950 p-4">
        <img src={$editSourceStore.previewUrl} alt={$editSourceStore.previewLabel} class="max-h-[calc(100vh-140px)] max-w-full rounded-lg object-contain" />
      </div>
    </div>
  </div>
{/if}

<SizeDialog open={$uiStore.sizeDialogOpen} value={form.size} onApply={(nextSize) => (form = { ...form, size: nextSize })} onClose={() => setUi('sizeDialogOpen', false)} />
