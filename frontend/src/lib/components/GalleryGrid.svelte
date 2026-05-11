<script lang="ts">
  import type { GalleryEntry, GalleryResponse } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import type { GalleryFilters } from '$lib/stores/gallery';
  import { formatBytes, imageUrl } from '$lib/utils/format';

  export let gallery: GalleryResponse | null = null;
  export let filters: GalleryFilters;
  export let loading = false;
  export let onFilter: (key: keyof GalleryFilters, value: string | boolean) => void = () => {};
  export let onResetFilters: () => void = () => {};
  export let onPage: (delta: number) => void = () => {};
  export let onFavorite: (image: GalleryEntry) => void = () => {};
  export let onDelete: (image: GalleryEntry) => void = () => {};
  export let onDeleteAll: () => void = () => {};
  export let onImport: (file: File) => void = () => {};
  export let onOpen: (image: GalleryEntry) => void = () => {};
  export let onEdit: (image: GalleryEntry) => void = () => {};

  let importInput: HTMLInputElement;

  $: images = gallery?.images || [];
  $: hasFilters = Boolean(
    filters.prompt.trim() ||
      filters.model ||
      filters.preset ||
      filters.size ||
      filters.dateFrom ||
      filters.dateTo ||
      filters.favorite
  );

  function importSelected() {
    const file = importInput.files?.[0];
    if (file) onImport(file);
    importInput.value = '';
  }

  function handleGalleryAction(event: MouseEvent, action: () => void) {
    event.preventDefault();
    event.stopPropagation();
    action();
  }
</script>

<section class="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4 sm:p-5">
  <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <div>
      <h2 class="text-sm font-semibold text-zinc-100">{$t.gallery.title}</h2>
      <p class="mt-1 text-xs text-zinc-500">
        {gallery?.total ? $t.gallery.imageCount(gallery.total) : $t.gallery.noImages}
        {#if gallery?.total_bytes}
          <span class="ml-2">{formatBytes(gallery.total_bytes)}</span>
        {/if}
      </p>
    </div>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => importInput.click()}>
        {$t.gallery.import}
      </button>
      <a href="/api/download-all" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800">{$t.gallery.exportZip}</a>
      <button type="button" class="rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-300 hover:bg-red-500/10" on:click={onDeleteAll}>
        {$t.gallery.deleteAll}
      </button>
      <input bind:this={importInput} type="file" accept=".zip,application/zip" class="hidden" on:change={importSelected} />
    </div>
  </div>

  <div class="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
    <input
      value={filters.prompt}
      placeholder={$t.gallery.filterPrompt}
      class="lg:col-span-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
      on:input={(event) => onFilter('prompt', event.currentTarget.value)}
    />
    <select value={filters.model} class="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" on:change={(event) => onFilter('model', event.currentTarget.value)}>
      <option value="">{$t.gallery.allModels}</option>
      {#each gallery?.filter_options.models || [] as model}
        <option value={model}>{model}</option>
      {/each}
    </select>
    <select value={filters.preset} class="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" on:change={(event) => onFilter('preset', event.currentTarget.value)}>
      <option value="">{$t.gallery.allPresets}</option>
      {#each gallery?.filter_options.presets || [] as preset}
        <option value={preset}>{preset}</option>
      {/each}
    </select>
    <select value={filters.size} class="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" on:change={(event) => onFilter('size', event.currentTarget.value)}>
      <option value="">{$t.gallery.allSizes}</option>
      {#each gallery?.filter_options.sizes || [] as size}
        <option value={size}>{size}</option>
      {/each}
    </select>
    <label class="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-300">
      <input type="checkbox" class="accent-emerald-500" checked={filters.favorite} on:change={(event) => onFilter('favorite', event.currentTarget.checked)} />
      {$t.gallery.favorites}
    </label>
  </div>

  {#if hasFilters}
    <button type="button" class="mb-4 text-xs font-medium text-emerald-300 hover:text-emerald-200" on:click={onResetFilters}>{$t.gallery.resetFilters}</button>
  {/if}

  {#if loading}
    <div class="rounded-xl border border-zinc-800 bg-zinc-950/40 px-4 py-10 text-center text-sm text-zinc-400">{$t.gallery.loading}</div>
  {:else if images.length === 0}
    <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
      <p class="text-sm font-medium text-zinc-300">{hasFilters ? $t.gallery.noMatch : $t.gallery.empty}</p>
      <p class="mt-2 text-xs text-zinc-500">{hasFilters ? $t.gallery.noMatchHint : $t.gallery.emptyHint}</p>
    </div>
  {:else}
    <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {#each images as image}
        <article class="gallery-card overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/45">
          <button type="button" class="block aspect-square w-full bg-zinc-950" on:click={() => onOpen(image)}>
            <img src={imageUrl(image.filename)} alt={image.prompt} class="h-full w-full object-cover" loading="lazy" />
          </button>
          <div class="space-y-3 p-3">
            <div class="min-w-0">
              <p class="line-clamp-2 text-sm text-zinc-200">{image.prompt}</p>
              <p class="mt-1 text-xs text-zinc-500">{image.size} / {image.model || '-'}</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button type="button" class="rounded-md border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800" on:click={(event) => handleGalleryAction(event, () => onEdit(image))}>{$t.common.edit}</button>
              <button type="button" class="rounded-md border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800" on:click={(event) => handleGalleryAction(event, () => onFavorite(image))}>{image.favorite ? $t.common.unfavorite : $t.common.favorite}</button>
              <a href={`/api/download/${encodeURIComponent(image.filename)}`} class="rounded-md border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800" on:click|stopPropagation>{$t.common.download}</a>
              <button type="button" class="rounded-md border border-red-500/40 px-2 py-1 text-xs text-red-300 hover:bg-red-500/10" on:click={(event) => handleGalleryAction(event, () => onDelete(image))}>{$t.common.delete}</button>
            </div>
          </div>
        </article>
      {/each}
    </div>

    <div class="mt-5 flex items-center justify-between">
      <button type="button" disabled={!gallery?.has_prev} class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" on:click={() => onPage(-1)}>
        {$t.gallery.previous}
      </button>
      <span class="text-xs text-zinc-500">{$t.gallery.page(gallery?.page || 1, gallery?.total_pages || 1)}</span>
      <button type="button" disabled={!gallery?.has_next} class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" on:click={() => onPage(1)}>
        {$t.gallery.next}
      </button>
    </div>
  {/if}
</section>
