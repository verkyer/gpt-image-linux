<script lang="ts">
  import type { ToastMessage } from '$lib/stores/ui';

  export let toast: ToastMessage | null = null;

  let role: 'alert' | 'status' = 'status';
  let ariaLive: 'assertive' | 'polite' = 'polite';

  $: isError = toast?.variant === 'error';
  $: {
    role = isError ? 'alert' : 'status';
    ariaLive = isError ? 'assertive' : 'polite';
  }
  $: toastClass = isError
    ? 'border-red-500/40 bg-red-950/90 text-red-100'
    : 'border-zinc-700 bg-zinc-900 text-zinc-100';
</script>

{#if toast?.message}
  <div
    class={`fixed bottom-5 right-5 z-[90] rounded-xl border px-4 py-3 text-sm shadow-2xl ${toastClass}`}
    {role}
    aria-live={ariaLive}
    aria-atomic="true"
  >
    {toast.message}
  </div>
{/if}
