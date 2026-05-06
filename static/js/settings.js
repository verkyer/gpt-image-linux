import { apiFetch } from './api.js';
import {
  clampCompressionInput,
  escapeAttribute,
  escapeHtml,
  setControlDisabled,
  showToast,
} from './ui.js';

const MASKED_API_KEY_VALUE = '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';

let settingsState = {
  activePresetId: null,
  presets: [],
};
let activeApiPath = '/v1/images/generations';

export async function loadSettings() {
  try {
    const data = await apiFetch('/api/settings', {}, 'loading settings');
    syncSettingsState(data);
  } catch (e) {
    // Settings are also editable after unlock; a transient failure here should not block the app.
  }
}

function syncSettingsState(data) {
  settingsState = {
    activePresetId: data.active_preset_id,
    presets: Array.isArray(data.presets) ? data.presets : [],
  };

  const activePreset = getActiveSettingsPreset(data);
  activeApiPath = activePreset?.api_path || data.api_path || '/v1/images/generations';
  document.getElementById('settingsPresetName').value = activePreset?.name || '';
  document.getElementById('settingsApiUrl').value = activePreset?.api_url || data.api_url || '';
  document.getElementById('settingsApiPath').value = activeApiPath;
  document.getElementById('settingsApiKey').value = (activePreset?.has_api_key || data.has_api_key) ? MASKED_API_KEY_VALUE : '';
  renderSettingsPresets();
  refreshParameterControls();
}

function getActiveSettingsPreset(data = null) {
  const activePresetId = data?.active_preset_id || settingsState.activePresetId;
  const presets = data?.presets || settingsState.presets;
  return presets.find(preset => preset.id === activePresetId) || presets[0] || null;
}

function renderSettingsPresets() {
  const list = document.getElementById('settingsPresetsList');
  const deleteButton = document.getElementById('deletePresetBtn');
  deleteButton.disabled = settingsState.presets.length <= 1;

  if (!settingsState.presets.length) {
    list.innerHTML = '<div class="rounded-md border border-dashed border-zinc-700 px-3 py-3 text-sm text-zinc-500">No presets</div>';
    return;
  }

  list.innerHTML = settingsState.presets.map(preset => {
    const active = preset.id === settingsState.activePresetId;
    const classes = active
      ? 'border-emerald-500/70 bg-emerald-500/10 text-zinc-100'
      : 'border-zinc-800 bg-zinc-950/40 text-zinc-300 hover:border-zinc-700 hover:bg-zinc-800/70';
    const keyLabel = preset.has_api_key ? preset.api_key_masked : 'No key';
    return `
      <button type="button" data-preset-id="${escapeAttribute(preset.id)}" onclick="activatePreset(this.dataset.presetId)"
        class="w-full text-left rounded-md border ${classes} px-3 py-2.5 transition-colors">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="truncate text-sm font-medium">${escapeHtml(preset.name || 'Untitled preset')}</div>
            <div class="mt-1 truncate text-xs font-mono text-zinc-500">${escapeHtml(preset.api_url || 'No API URL')}</div>
          </div>
          <span class="shrink-0 rounded-md border ${active ? 'border-emerald-500/40 text-emerald-300' : 'border-zinc-700 text-zinc-500'} px-2 py-0.5 text-[11px] font-medium">
            ${active ? 'Active' : 'Switch'}
          </span>
        </div>
        <div class="mt-2 flex items-center justify-between gap-3 text-xs text-zinc-500">
          <span class="truncate font-mono">${escapeHtml(preset.api_path || '/v1/images/generations')}</span>
          <span class="shrink-0 font-mono">${escapeHtml(keyLabel)}</span>
        </div>
      </button>
    `;
  }).join('');
}

export async function activatePreset(presetId) {
  if (!presetId || presetId === settingsState.activePresetId) return;

  try {
    const data = await apiFetch(`/api/settings/presets/${encodeURIComponent(presetId)}/activate`, {
      method: 'POST',
    }, 'switching preset');
    syncSettingsState(data);
    showToast('Preset switched', 'success');
  } catch (e) {
    showToast('Failed to switch preset: ' + e.message, 'error');
  }
}

export async function createPreset() {
  try {
    const data = await apiFetch('/api/settings/presets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_preset_id: settingsState.activePresetId }),
    }, 'creating preset');
    syncSettingsState(data);
    showToast('Preset created', 'success');
    document.getElementById('settingsPresetName').focus();
    document.getElementById('settingsPresetName').select();
  } catch (e) {
    showToast('Failed to create preset: ' + e.message, 'error');
  }
}

export async function deleteActivePreset() {
  const activePreset = getActiveSettingsPreset();
  if (!activePreset || settingsState.presets.length <= 1) return;
  if (!confirm(`Delete preset "${activePreset.name || 'Untitled preset'}"?`)) return;

  try {
    const data = await apiFetch(`/api/settings/presets/${encodeURIComponent(activePreset.id)}`, {
      method: 'DELETE',
    }, 'deleting preset');
    syncSettingsState(data);
    showToast('Preset deleted', 'success');
  } catch (e) {
    showToast('Failed to delete preset: ' + e.message, 'error');
  }
}

export async function saveSettings() {
  const presetName = document.getElementById('settingsPresetName').value.trim();
  const apiUrl = document.getElementById('settingsApiUrl').value.trim();
  const apiKey = document.getElementById('settingsApiKey').value.trim();
  const apiPath = document.getElementById('settingsApiPath').value;
  const requestBody = {
    active_preset_id: settingsState.activePresetId,
    preset_name: presetName,
    api_url: apiUrl,
    api_key: apiKey,
    api_path: apiPath,
  };

  if (!apiUrl) {
    showToast('Please enter an API URL', 'error');
    return;
  }
  if (!apiKey) {
    showToast('Please enter an API Key', 'error');
    return;
  }
  if (apiKey === MASKED_API_KEY_VALUE) requestBody.api_key = null;

  try {
    const data = await apiFetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    }, 'saving settings');
    syncSettingsState(data);
    showToast('Preset saved', 'success');
    toggleSettings();
  } catch (e) {
    showToast('Failed to save preset: ' + e.message, 'error');
  }
}

export async function toggleSettings() {
  const drawer = document.getElementById('settingsDrawer');
  drawer.classList.toggle('hidden');
  if (!drawer.classList.contains('hidden')) {
    await loadSettings();
    document.getElementById('settingsApiUrl').focus();
  }
}

export function isResponsesApiSelected() {
  return activeApiPath === '/v1/responses';
}

export function refreshParameterControls(loading = false) {
  const responsesMode = isResponsesApiSelected();
  const lockParameters = loading || responsesMode;
  const parameterIds = ['qualitySelect', 'formatSelect', 'quantityInput', 'responseFormatSelect'];

  setControlDisabled('modelSelect', loading);
  parameterIds.forEach(id => setControlDisabled(id, lockParameters));
  setControlDisabled('sizeSelect', lockParameters);

  if (responsesMode) {
    setControlDisabled('compressionInput', true);
    document.getElementById('compressionInput').placeholder = 'Disabled for Responses';
    return;
  }

  if (loading) {
    setControlDisabled('compressionInput', true);
    return;
  }

  handleOutputFormatChange();
}

export function handleOutputFormatChange() {
  const format = document.getElementById('formatSelect').value;
  const compressionInput = document.getElementById('compressionInput');
  const isPng = format === 'png';

  if (isResponsesApiSelected()) {
    compressionInput.disabled = true;
    compressionInput.placeholder = 'Disabled for Responses';
    compressionInput.classList.add('opacity-50', 'cursor-not-allowed');
    return;
  }

  compressionInput.disabled = isPng;
  compressionInput.placeholder = isPng ? 'Disabled for PNG' : '0-100';
  compressionInput.classList.toggle('opacity-50', isPng);
  compressionInput.classList.toggle('cursor-not-allowed', isPng);

  if (isPng) {
    compressionInput.value = '';
  } else if (!compressionInput.value) {
    compressionInput.value = '100';
  } else {
    clampCompressionInput();
  }
}
