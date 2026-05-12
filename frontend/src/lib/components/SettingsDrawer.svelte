<script lang="ts">
  import { t } from '$lib/i18n';
  import type { ApiPath, ApiPreset, PresetHealthResponse, PresetHealthStatus, SettingsResponse } from '$lib/api/types';

  const MASKED_API_KEY_VALUE = '********';

  export let open = false;
  export let settings: SettingsResponse | null = null;
  export let saving = false;
  export let health: PresetHealthResponse | null = null;
  export let healthChecking = false;
  export let onClose: () => void = () => {};
  export let onSave: (body: Record<string, unknown>) => Promise<void> | void = () => {};
  export let onCreate: () => Promise<void> | void = () => {};
  export let onActivate: (presetId: string) => Promise<void> | void = () => {};
  export let onDelete: () => Promise<void> | void = () => {};
  export let onHealthCheck: (presetId: string) => Promise<void> | void = () => {};

  let activePresetId = '';
  let presetName = '';
  let apiUrl = '';
  let apiKey = '';
  let apiPath: ApiPath = '/v1/images/generations';
  let upstreamSocks5Proxy = '';
  let apiKeyInputType = 'password';

  $: activePreset = settings?.presets.find((preset) => preset.id === settings.active_preset_id) || settings?.presets[0] || null;
  $: if (settings && activePreset) {
    activePresetId = settings.active_preset_id;
    presetName = activePreset.name || '';
    apiUrl = activePreset.api_url || settings.api_url || '';
    apiKey =
      activePreset.api_key_source === 'env' && activePreset.api_key_env_var
        ? `\${${activePreset.api_key_env_var}}`
        : activePreset.has_api_key || settings.has_api_key
          ? MASKED_API_KEY_VALUE
          : '';
    apiPath = activePreset.api_path || settings.api_path || '/v1/images/generations';
    upstreamSocks5Proxy = settings.has_upstream_socks5_proxy ? settings.upstream_socks5_proxy_masked : '';
  }
  $: apiKeyInputType = apiKey.trim().startsWith('${') && apiKey.trim().endsWith('}') ? 'text' : 'password';

  async function save() {
    const proxyValue = upstreamSocks5Proxy.trim();
    const currentProxyMask = settings?.upstream_socks5_proxy_masked || '';
    await onSave({
      active_preset_id: activePresetId,
      preset_name: presetName.trim(),
      api_url: apiUrl.trim(),
      api_key: apiKey.trim() === MASKED_API_KEY_VALUE ? null : apiKey.trim(),
      api_path: apiPath,
      upstream_socks5_proxy: proxyValue === currentProxyMask ? null : proxyValue
    });
  }

  function keyLabel(preset: ApiPreset) {
    if (preset.api_key_source === 'env') {
      return `${$t.settings.envRef}: ${preset.api_key_env_var || preset.api_key_masked}`;
    }
    return preset.has_api_key ? preset.api_key_masked : $t.common.noKey;
  }

  async function checkHealth() {
    if (!activePresetId) return;
    await onHealthCheck(activePresetId);
  }

  function healthStatusLabel(status: PresetHealthStatus) {
    if (status === 'ok') return $t.settings.healthOk;
    if (status === 'warning') return $t.settings.healthWarning;
    return $t.settings.healthError;
  }

  function healthPanelClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100';
    if (status === 'warning') return 'border-amber-500/40 bg-amber-500/10 text-amber-100';
    return 'border-red-500/40 bg-red-500/10 text-red-100';
  }

  function healthBadgeClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-500/40 text-emerald-300';
    if (status === 'warning') return 'border-amber-500/40 text-amber-300';
    return 'border-red-500/40 text-red-300';
  }
</script>

{#if open}
  <div class="fixed inset-0 z-50">
    <button class="drawer-backdrop absolute inset-0" type="button" aria-label={$t.settings.closeLabel} on:click={onClose}></button>
    <aside class="fade-in absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-zinc-800 bg-zinc-900 shadow-2xl">
      <div class="flex items-center justify-between border-b border-zinc-800 p-5">
        <div>
          <h2 class="text-lg font-semibold text-zinc-100">{$t.settings.title}</h2>
          <p class="mt-1 text-xs text-zinc-500">{$t.settings.subtitle}</p>
        </div>
        <button type="button" class="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.settings.closeLabel} on:click={onClose}>x</button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-5">
        <div class="mb-5 flex items-center justify-between">
          <h3 class="text-sm font-semibold text-zinc-200">{$t.settings.presets}</h3>
          <div class="flex gap-2">
            <button type="button" class="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800" on:click={onCreate}>
              {$t.settings.newPreset}
            </button>
            <button
              type="button"
              disabled={!settings || settings.presets.length <= 1}
              class="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
              on:click={onDelete}
            >
              {$t.settings.deletePreset}
            </button>
          </div>
        </div>

        <div class="mb-6 max-h-[260px] space-y-2 overflow-y-auto">
          {#each settings?.presets || [] as preset}
            <button
              type="button"
              class={`w-full rounded-md border px-3 py-2.5 text-left transition-colors ${
                preset.id === settings?.active_preset_id
                  ? 'border-emerald-500/70 bg-emerald-500/10 text-zinc-100'
                  : 'border-zinc-800 bg-zinc-950/40 text-zinc-300 hover:border-zinc-700 hover:bg-zinc-800/70'
              }`}
              on:click={() => onActivate(preset.id)}
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="truncate text-sm font-medium">{preset.name || $t.common.untitledPreset}</div>
                  <div class="mt-1 truncate font-mono text-xs text-zinc-500">{preset.api_url || $t.common.noApiUrl}</div>
                </div>
                <span class="shrink-0 rounded-md border border-zinc-700 px-2 py-0.5 text-[11px] font-medium text-zinc-500">
                  {preset.id === settings?.active_preset_id ? $t.common.active : $t.common.switch}
                </span>
              </div>
              <div class="mt-2 flex items-center justify-between gap-3 text-xs text-zinc-500">
                <span class="truncate font-mono">{preset.api_path}</span>
                <span class="shrink-0 font-mono">{keyLabel(preset)}</span>
              </div>
            </button>
          {/each}
        </div>

        <div class="space-y-4">
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.presetName}</span>
            <input bind:value={presetName} class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.apiUrl}</span>
            <input bind:value={apiUrl} class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" placeholder="https://api.example.com" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.apiPath}</span>
            <select bind:value={apiPath} class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none">
              <option value="/v1/images/generations">/v1/images/generations</option>
              <option value="/v1/responses">/v1/responses</option>
            </select>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.apiKey}</span>
            <input bind:value={apiKey} type={apiKeyInputType} class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" />
            <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.apiKeyHint}</span>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.upstreamSocks5Proxy}</span>
            <input bind:value={upstreamSocks5Proxy} class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none" placeholder="socks5://127.0.0.1:1080" />
            <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.upstreamSocks5ProxyHint}</span>
          </label>
        </div>
      </div>

      <div class="space-y-3 border-t border-zinc-800 p-5">
        {#if health}
          <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(health.status)}`}>
            <div class="flex items-center justify-between gap-3">
              <span class="font-semibold">{$t.settings.healthStatus}</span>
              <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(health.status)}`}>
                {healthStatusLabel(health.status)}
              </span>
            </div>
            <div class="mt-2 space-y-1.5">
              {#each health.checks as check}
                <div class="rounded-md border border-zinc-800 bg-zinc-950/50 p-2 text-zinc-300">
                  <div class="flex items-center justify-between gap-2">
                    <span class="font-mono text-[11px] text-zinc-500">{check.name}</span>
                    <span class={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${healthBadgeClass(check.status)}`}>
                      {healthStatusLabel(check.status)}
                    </span>
                  </div>
                  <div class="mt-1 leading-relaxed text-zinc-400">{check.message}</div>
                </div>
              {/each}
            </div>
          </div>
        {/if}
        <div class="grid grid-cols-2 gap-3">
          <button
            type="button"
            disabled={healthChecking || !activePresetId}
            class="rounded-xl border border-zinc-700 px-4 py-3 text-sm font-semibold text-zinc-200 transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            on:click={checkHealth}
          >
            {healthChecking ? $t.settings.healthChecking : $t.settings.healthCheck}
          </button>
          <button
            type="button"
            disabled={saving}
            class="rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            on:click={save}
          >
            {saving ? $t.settings.saving : $t.settings.savePreset}
          </button>
        </div>
      </div>
    </aside>
  </div>
{/if}
