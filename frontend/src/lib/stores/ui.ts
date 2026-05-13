import { writable } from 'svelte/store';

export type UiState = {
  settingsOpen: boolean;
  jobsOpen: boolean;
  lightboxOpen: boolean;
  sizeDialogOpen: boolean;
  editPreviewOpen: boolean;
  toast: string;
};

const initialUiState: UiState = {
  settingsOpen: false,
  jobsOpen: false,
  lightboxOpen: false,
  sizeDialogOpen: false,
  editPreviewOpen: false,
  toast: ''
};

function createUiStore() {
  const { subscribe, set, update } = writable<UiState>(initialUiState);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function setKey<K extends keyof UiState>(key: K, value: UiState[K]) {
    update((state) => ({ ...state, [key]: value }));
  }

  function showToast(message: string) {
    setKey('toast', message);
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      setKey('toast', '');
    }, 2500);
  }

  function cleanup() {
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = null;
  }

  return {
    subscribe,
    set,
    setKey,
    showToast,
    cleanup
  };
}

export const uiStore = createUiStore();
