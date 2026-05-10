<script lang="ts">
  import type { GenerateJobStatus } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import { operationLabel, stageLabel, statusLabel } from '$lib/utils/format';

  export let open = false;
  export let jobs: GenerateJobStatus[] = [];
  export let selectedIds: Set<string> = new Set();
  export let onClose: () => void = () => {};
  export let onRefresh: () => void = () => {};
  export let onToggle: (jobId: string) => void = () => {};
  export let onToggleAll: () => void = () => {};
  export let onCancelSelected: () => void = () => {};
</script>

{#if open}
  <div class="fixed inset-0 z-50">
    <button class="drawer-backdrop absolute inset-0" type="button" aria-label={$t.jobs.closeLabel} on:click={onClose}></button>
    <aside class="fade-in absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-zinc-800 bg-zinc-900 shadow-2xl">
      <div class="flex items-center justify-between border-b border-zinc-800 p-5">
        <div class="min-w-0">
          <h2 class="text-lg font-semibold text-zinc-100">{$t.jobs.title}</h2>
          <p class="mt-1 text-xs text-zinc-500">{$t.jobs.subtitle}</p>
        </div>
        <button type="button" class="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.jobs.closeLabel} on:click={onClose}>x</button>
      </div>

      <div class="flex items-center justify-end gap-3 border-b border-zinc-800 p-5">
        <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!jobs.length} on:click={onToggleAll}>
          {$t.jobs.selectAll}
        </button>
        <button type="button" class="rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={onRefresh}>
          {$t.jobs.refresh}
        </button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-5">
        {#if jobs.length === 0}
          <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
            <p class="text-sm font-medium text-zinc-300">{$t.jobs.noRunning}</p>
            <p class="mt-2 text-xs text-zinc-500">{$t.jobs.noRunningHint}</p>
          </div>
        {:else}
          <div class="space-y-3">
            {#each jobs as job}
              <label class="flex gap-3 rounded-xl border border-zinc-800 bg-zinc-950/45 p-4">
                <input type="checkbox" class="mt-1 accent-emerald-500" checked={selectedIds.has(job.job_id)} on:change={() => onToggle(job.job_id)} />
                <div class="min-w-0 flex-1">
                  <div class="flex items-center justify-between gap-3">
                    <span class="rounded-md border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-400">{operationLabel(job.operation, $t.operations)}</span>
                    <span class="text-xs font-medium text-emerald-300">{statusLabel(job.status, $t.statuses)}</span>
                  </div>
                  <p class="mt-2 truncate text-sm text-zinc-200">{job.prompt || $t.common.untitledJob}</p>
                  <p class="mt-1 truncate text-xs text-zinc-500">{stageLabel(job, $t.stages)}</p>
                </div>
              </label>
            {/each}
          </div>
        {/if}
      </div>

      <div class="border-t border-zinc-800 p-5">
        <button type="button" disabled={!selectedIds.size} class="w-full rounded-xl bg-red-600 px-4 py-3 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40" on:click={onCancelSelected}>
          {$t.jobs.cancelSelected}
        </button>
      </div>
    </aside>
  </div>
{/if}
