import { writable } from 'svelte/store';

export type ToastVariant = 'status' | 'error';

export type ToastMessage = {
  message: string;
  variant: ToastVariant;
};

export type UiState = {
  settingsOpen: boolean;
  jobsOpen: boolean;
  sizeDialogOpen: boolean;
  editPreviewOpen: boolean;
  toast: ToastMessage | null;
};

const initialUiState: UiState = {
  settingsOpen: false,
  jobsOpen: false,
  sizeDialogOpen: false,
  editPreviewOpen: false,
  toast: null
};

function createUiStore() {
  const { subscribe, update } = writable<UiState>(initialUiState);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function setKey<K extends keyof UiState>(key: K, value: UiState[K]) {
    update((state) => ({ ...state, [key]: value }));
  }

  function showToast(message: string, variant: ToastVariant = 'status') {
    setKey('toast', { message, variant });
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      setKey('toast', null);
    }, 2500);
  }

  function cleanup() {
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = null;
  }

  return {
    subscribe,
    setKey,
    showToast,
    cleanup
  };
}

export const uiStore = createUiStore();
