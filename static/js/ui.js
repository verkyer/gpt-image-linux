export function showError(msg) {
  const banner = document.getElementById('errorBanner');
  document.getElementById('errorText').textContent = msg;
  banner.classList.remove('hidden');
}

export function hideError() {
  document.getElementById('errorBanner').classList.add('hidden');
}

export function showToast(msg, type = 'success') {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'fixed top-20 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-lg text-sm font-medium shadow-xl transition-all duration-300 ' +
    (type === 'success' ? 'bg-emerald-600 text-white' : 'bg-red-600 text-white');
  toast.style.opacity = '1';
  toast.style.transform = 'translate(-50%, 0)';
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translate(-50%, 8px)';
  }, 2500);
}

export function updatePromptLen() {
  const len = document.getElementById('promptInput').value.length;
  const el = document.getElementById('promptLen');
  el.textContent = `${len} / 4000`;
  el.className = len > 4000 ? 'text-xs text-red-500' : len > 3000 ? 'text-xs text-yellow-600' : 'text-xs text-zinc-600';
}

export function clampIntegerInput(input, min, max, fallback) {
  const raw = String(input.value).trim();
  if (!raw) return null;

  let value = Number.parseInt(raw, 10);
  if (!Number.isFinite(value)) value = fallback;
  value = Math.min(Math.max(value, min), max);
  input.value = String(value);
  return value;
}

export function clampCompressionInput() {
  return clampIntegerInput(document.getElementById('compressionInput'), 0, 100, 100);
}

export function clampQuantityInput() {
  return clampIntegerInput(document.getElementById('quantityInput'), 1, 10, 1);
}

export function setControlDisabled(id, disabled) {
  const el = document.getElementById(id);
  if (!el) return;
  el.disabled = disabled;
  el.classList.toggle('opacity-50', disabled);
  el.classList.toggle('cursor-not-allowed', disabled);
  el.classList.toggle('cursor-pointer', !disabled && el.tagName === 'SELECT');
}

export function unlockBodyOverflowIfIdle() {
  const overlayIds = ['lightbox', 'sizeDialog', 'settingsDrawer'];
  const allHidden = overlayIds.every(id => {
    const el = document.getElementById(id);
    return !el || el.classList.contains('hidden');
  });

  if (allHidden) {
    document.body.style.overflow = '';
  }
}

export function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
  return Promise.resolve();
}

export function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value ?? '';
  return div.innerHTML;
}

export function escapeAttribute(value) {
  return escapeHtml(value).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
