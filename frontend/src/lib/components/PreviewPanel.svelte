<script lang="ts">
  import type { GenerateJobStatus } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import { downloadUrl, formatBeijingTime, stageLabel, statusLabel } from '$lib/utils/format';

  export let loading = false;
  export let error = '';
  export let job: GenerateJobStatus | null = null;
  export let imageUrl = '';
  export let filename = '';
  export let prompt = '';
  export let onRegenerate: () => void = () => {};
  export let onClear: () => void = () => {};
</script>

<section class="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4 sm:p-5">
  <div class="mb-4 flex items-center justify-between gap-3">
    <div class="min-w-0">
      <h2 class="text-sm font-semibold text-zinc-100">{$t.preview.title}</h2>
      <p class="mt-1 truncate text-xs text-zinc-500">{prompt || $t.preview.subtitle}</p>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      {#if filename}
        <a href={downloadUrl(filename)} class="rounded-lg border border-zinc-700 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800">{$t.common.download}</a>
      {/if}
      <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!job && !imageUrl} on:click={onRegenerate}>
        {$t.preview.regenerate}
      </button>
      <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800" on:click={onClear}>
        {$t.common.clear}
      </button>
    </div>
  </div>

  {#if error}
    <div class="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>
  {/if}

  <div class={`mt-4 flex min-h-[360px] items-center justify-center overflow-hidden rounded-xl border border-zinc-800 ${imageUrl ? 'bg-zinc-950' : 'preview-empty'}`}>
    {#if loading}
      <div class="flex max-w-sm flex-col items-center px-6 text-center">
        <span class="spinner"></span>
        <p class="mt-4 text-sm font-semibold text-zinc-100">{stageLabel(job, $t.stages) || $t.preview.working}</p>
        <p class="mt-2 text-xs text-zinc-400">{statusLabel(job?.status, $t.statuses) || $t.preview.queued}</p>
      </div>
    {:else if imageUrl}
      <img src={imageUrl} alt={$t.preview.generatedAlt} class="max-h-[640px] max-w-full rounded-lg object-contain" />
    {:else}
      <div class="px-6 text-center">
        <p class="text-sm font-medium text-zinc-300">{$t.preview.noPreview}</p>
        <p class="mt-2 text-xs text-zinc-500">{$t.preview.noPreviewHint}</p>
      </div>
    {/if}
  </div>

  {#if job}
    <div class="mt-4 grid grid-cols-2 gap-2 text-xs text-zinc-500 sm:grid-cols-5">
      <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
        <div class="text-zinc-600">{$t.common.status}</div>
        <div class="mt-1 text-zinc-300">{statusLabel(job.status, $t.statuses)}</div>
      </div>
      <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
        <div class="text-zinc-600">{$t.common.completedAt}</div>
        <div class="mt-1 whitespace-nowrap text-zinc-300">{formatBeijingTime(job.completed_at)}</div>
      </div>
      <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
        <div class="text-zinc-600">{$t.common.size}</div>
        <div class="mt-1 text-zinc-300">{job.size || '-'}</div>
      </div>
      <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
        <div class="text-zinc-600">{$t.common.model}</div>
        <div class="mt-1 truncate text-zinc-300">{job.model || '-'}</div>
      </div>
      <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
        <div class="text-zinc-600">{$t.common.duration}</div>
        <div class="mt-1 text-zinc-300">{job.duration || '-'}</div>
      </div>
    </div>
  {/if}
</section>
