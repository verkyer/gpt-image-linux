<script lang="ts">
  import { t } from '$lib/i18n';

  export let label: string = '';
  export let onChange: (event: Event) => void = () => {};
  export let onPreview: () => void = () => {};

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
    accept="image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp,image/heic,image/heif,image/x-icon,image/tiff"
    class="hidden"
    on:change={onChange}
  />
  <button
    type="button"
    class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800"
    on:click={openPicker}
  >
    {$t.promptForm.uploadEditImage}
  </button>
  {#if label}
    <button
      type="button"
      class="ml-3 inline-block max-w-[260px] truncate align-middle text-left text-xs font-medium text-emerald-300 underline decoration-emerald-500/40 underline-offset-4 hover:text-emerald-200"
      title={$t.promptForm.previewEditLabel(label)}
      on:click={onPreview}
    >
      {label}
    </button>
  {/if}
</div>
