import { apiFetch } from './api.js';

let authenticatedCallback = async () => {};

export function configureAccess({ onAuthenticated } = {}) {
  authenticatedCallback = typeof onAuthenticated === 'function' ? onAuthenticated : async () => {};
}

export function showAccessGate(message) {
  const error = document.getElementById('accessKeyError');
  if (message) {
    error.textContent = message;
    error.classList.remove('hidden');
  } else {
    error.classList.add('hidden');
  }
  document.getElementById('accessGate').classList.remove('hidden');
  document.getElementById('accessKeyInput').focus();
}

export async function checkAccess() {
  try {
    const data = await apiFetch('/api/access/status', {}, 'checking access');

    if (data.authenticated) {
      document.getElementById('accessGate').classList.add('hidden');
      await authenticatedCallback();
      return;
    }
  } catch (e) {
    if (!document.getElementById('accessGate').classList.contains('hidden')) return;
    showAccessGate(e.message || 'Access check failed');
    return;
  }

  showAccessGate();
}

export async function unlockAccess(event) {
  event?.preventDefault();
  const input = document.getElementById('accessKeyInput');
  const button = document.getElementById('accessKeyBtn');
  const error = document.getElementById('accessKeyError');
  const accessKey = input.value.trim();

  if (!accessKey) {
    error.textContent = 'Please enter the access key';
    error.classList.remove('hidden');
    return;
  }

  button.disabled = true;
  button.textContent = 'Unlocking...';
  error.classList.add('hidden');

  try {
    const data = await apiFetch('/api/access', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_key: accessKey }),
    }, 'unlocking access');
    if (!data.authenticated) throw new Error('Invalid access key');

    input.value = '';
    document.getElementById('accessGate').classList.add('hidden');
    await authenticatedCallback();
  } catch (e) {
    error.textContent = e.message || 'Invalid access key';
    error.classList.remove('hidden');
  } finally {
    button.disabled = false;
    button.textContent = 'Unlock';
  }
}
