<script lang="ts">
  import { t } from '$lib/i18n';

  export let sources: { id: string; label: string; kind: 'upload' | 'gallery' }[] = [];
  export let onChange: (event: Event) => void = () => {};
  export let onPreview: (sourceId: string) => void = () => {};
  export let onClear: () => void = () => {};

  let input: HTMLInputElement;

  export function openPicker() {
    input?.click();
  }

  export function reset() {
    if (input) input.value = '';
  }
</script>

<div class="min-w-0">
  <input
    bind:this={input}
    type="file"
    multiple
    accept="image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp,image/heic,image/heif,image/x-icon,image/tiff"
    aria-label={$t.promptForm.uploadEditImage}
    class="hidden"
    on:change={onChange}
  />
  <button
    type="button"
    class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800"
    on:click={openPicker}
  >
    {$t.promptForm.uploadEditImage}
  </button>
  {#if sources.length}
    <button
      type="button"
      class="control-focus ml-2 rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800"
      aria-label={$t.promptForm.clearEditSources}
      on:click={onClear}
    >
      {$t.common.clear}
    </button>
    <div class="mt-2 flex max-w-full flex-wrap gap-2">
      {#each sources as source (source.id)}
        <button
          type="button"
          class="control-focus max-w-[220px] truncate rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-left text-xs font-medium text-emerald-200 hover:bg-emerald-500/15"
          title={$t.promptForm.previewEditLabel(source.label)}
          on:click={() => onPreview(source.id)}
        >
          {source.kind === 'gallery' ? $t.promptForm.gallerySourceBadge : $t.promptForm.uploadSourceBadge} · {source.label}
        </button>
      {/each}
    </div>
  {/if}
</div>
