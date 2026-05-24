<script lang="ts">
  import { confirmStore, type ConfirmRequest } from '$lib/stores/confirm';
  import { dialog } from '$lib/actions/dialog';

  export let request: ConfirmRequest | null = null;

  let requiredValue = '';
  let lastRequestId = 0;

  $: if ((request?.id || 0) !== lastRequestId) {
    requiredValue = '';
    lastRequestId = request?.id || 0;
  }
  $: requiredText = request?.requiredText || '';
  $: canConfirm = !requiredText || requiredValue === requiredText;
  $: confirmClass =
    request?.variant === 'danger'
      ? 'bg-red-600 text-white hover:bg-red-500 disabled:bg-red-900 disabled:text-red-200'
      : 'bg-emerald-600 text-white hover:bg-emerald-500 disabled:bg-emerald-900 disabled:text-emerald-200';
</script>

{#if request}
  <div class="fixed inset-0 z-[95] flex items-center justify-center bg-black/70 p-4">
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={request.closeLabel} on:click={() => confirmStore.cancel()}></button>
    <div
      class="fade-in relative w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900 shadow-2xl"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      use:dialog={{ open: Boolean(request), onClose: () => confirmStore.cancel() }}
    >
      <div class="border-b border-zinc-800 p-5">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 id="confirm-dialog-title" class="text-base font-semibold text-zinc-100">{request.title}</h2>
            <p class="mt-2 text-sm leading-6 text-zinc-400">{request.message}</p>
          </div>
          <button type="button" class="control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={request.closeLabel} on:click={() => confirmStore.cancel()}>
            x
          </button>
        </div>
      </div>

      {#if request.details?.length || requiredText}
        <div class="space-y-4 p-5">
          {#if request.details?.length}
            <ul class="space-y-2 text-sm text-zinc-300">
              {#each request.details as detail}
                <li class="rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2">{detail}</li>
              {/each}
            </ul>
          {/if}

          {#if requiredText}
            <label class="block">
              <span class="text-xs font-medium text-zinc-500">{request.requiredTextLabel}</span>
              <input
                bind:value={requiredValue}
                class="control-focus mt-2 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-red-500"
                autocomplete="off"
                spellcheck="false"
                data-autofocus
              />
            </label>
          {/if}
        </div>
      {/if}

      <div class="flex justify-end gap-3 border-t border-zinc-800 p-5">
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800" on:click={() => confirmStore.cancel()}>
          {request.cancelLabel}
        </button>
        <button type="button" disabled={!canConfirm} class={`control-focus rounded-lg px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60 ${confirmClass}`} on:click={() => confirmStore.accept()}>
          {request.confirmLabel}
        </button>
      </div>
    </div>
  </div>
{/if}
