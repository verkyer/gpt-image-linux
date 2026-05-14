<script lang="ts">
  import { t } from '$lib/i18n';
  import type { PromptFormState } from '$lib/stores/preview';

  export let form: PromptFormState;
  export let responsesMode = false;
  export let loading = false;
  export let onGenerate: () => void = () => {};
  export let onEdit: () => void = () => {};
  export let onOpenSize: () => void = () => {};

  $: promptLen = form.prompt.length;

  function clampQuantity() {
    form = { ...form, quantity: Math.min(Math.max(Number(form.quantity) || 1, 1), 10) };
  }

  function clampCompression() {
    if (form.outputCompression === '') return;
    form = { ...form, outputCompression: String(Math.min(Math.max(Number(form.outputCompression) || 0, 0), 100)) };
  }

  $: if (form.outputFormat === 'png' && form.outputCompression !== '') form = { ...form, outputCompression: '' };
</script>

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
        disabled={responsesMode || loading}
        class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-left font-mono text-sm text-zinc-100 hover:bg-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
        on:click={onOpenSize}
      >
        {responsesMode ? $t.promptForm.disabledForResponses : form.size}
      </button>
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.quality}</span>
      <select bind:value={form.quality} disabled={responsesMode || loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
        <option value="auto">auto</option>
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.quantity}</span>
      <input bind:value={form.quantity} disabled={responsesMode || loading} type="number" min="1" max="10" class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampQuantity} />
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.format}</span>
      <select bind:value={form.outputFormat} disabled={responsesMode || loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
        <option value="png">png</option>
        <option value="jpeg">jpeg</option>
        <option value="webp">webp</option>
      </select>
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.compression}</span>
      <input bind:value={form.outputCompression} disabled={responsesMode || loading || form.outputFormat === 'png'} type="number" min="0" max="100" placeholder={form.outputFormat === 'png' ? $t.promptForm.disabledForPng : '0-100'} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50" on:input={clampCompression} />
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptForm.responseFormat}</span>
      <select bind:value={form.responseFormat} disabled={responsesMode || loading} class="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50">
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
    <slot name="edit-source" />
    <div class="flex gap-2">
      <button type="button" disabled={loading} class="rounded-xl bg-zinc-700 px-4 py-3 text-sm font-semibold text-white hover:bg-zinc-600 disabled:cursor-not-allowed disabled:opacity-50" on:click={onEdit}>
        {$t.promptForm.edits}
      </button>
      <button type="button" disabled={loading} class="rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" on:click={onGenerate}>
        {$t.promptForm.generate}
      </button>
    </div>
  </div>
</section>
