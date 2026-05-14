<script lang="ts">
  export let message: string = '';
</script>

{#if message}
  <div class="fixed bottom-5 right-5 z-[90] rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-zinc-100 shadow-2xl">
    {message}
  </div>
{/if}
