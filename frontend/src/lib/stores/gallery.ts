import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import type { GalleryBatchResponse, GalleryEntry, GalleryResponse } from '$lib/api/types';

export type GalleryFilters = {
  prompt: string;
  model: string;
  preset: string;
  size: string;
  dateFrom: string;
  dateTo: string;
  favorite: boolean;
};

export type GalleryState = {
  gallery: GalleryResponse | null;
  loading: boolean;
  page: number;
  filters: GalleryFilters;
  selectionMode: boolean;
  selectedIds: Set<string>;
};

export const defaultGalleryFilters: GalleryFilters = {
  prompt: '',
  model: '',
  preset: '',
  size: '',
  dateFrom: '',
  dateTo: '',
  favorite: false
};

const initialGalleryState: GalleryState = {
  gallery: null,
  loading: false,
  page: 1,
  filters: { ...defaultGalleryFilters },
  selectionMode: false,
  selectedIds: new Set()
};

function buildGalleryParams(page: number, filters: GalleryFilters, includeTotalBytes = false) {
  const params = new URLSearchParams({ page: String(page), page_size: '9' });
  if (filters.prompt.trim()) params.set('prompt', filters.prompt.trim());
  if (filters.model) params.set('model', filters.model);
  if (filters.preset) params.set('preset', filters.preset);
  if (filters.size) params.set('size', filters.size);
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
  if (filters.favorite) params.set('favorite', 'true');
  if (includeTotalBytes) params.set('include_total_bytes', 'true');
  return params;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError';
}

function createGalleryStore() {
  const { subscribe, update } = writable<GalleryState>(initialGalleryState);
  let state = initialGalleryState;
  let filterTimer: ReturnType<typeof setTimeout> | null = null;
  let requestSeq = 0;
  let abortController: AbortController | null = null;
  let pendingRequestKey = '';

  subscribe((value) => {
    state = value;
  });

  async function loadGallery(page = state.page, includeTotalBytes = false) {
    const filters = { ...state.filters };
    const params = buildGalleryParams(page, filters, includeTotalBytes);
    const requestKey = params.toString();
    if (state.loading && pendingRequestKey === requestKey) return;
    const seq = ++requestSeq;
    pendingRequestKey = requestKey;
    abortController?.abort();
    abortController = new AbortController();
    update((current) => ({ ...current, loading: true }));
    try {
      const gallery = await apiFetch<GalleryResponse>(
        `/api/gallery?${requestKey}`,
        { signal: abortController.signal },
        'loading gallery'
      );
      if (seq !== requestSeq) return;
      const visibleIds = new Set(gallery.images.map((image) => image.id));
      const selectedIds = new Set([...state.selectedIds].filter((id) => visibleIds.has(id)));
      update((current) => ({
        ...current,
        gallery,
        page: gallery.page,
        selectedIds,
        selectionMode: selectedIds.size > 0 ? current.selectionMode : false
      }));
    } catch (error) {
      if (seq !== requestSeq) return;
      if (isAbortError(error)) return;
      throw error;
    } finally {
      if (seq === requestSeq) {
        abortController = null;
        pendingRequestKey = '';
        update((current) => ({ ...current, loading: false }));
      }
    }
  }

  function updateFilter(key: keyof GalleryFilters, value: string | boolean) {
    update((current) => ({
      ...current,
      filters: {
        ...current.filters,
        [key]: key === 'favorite' ? Boolean(value) : String(value || '')
      }
    }));
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      void loadGallery(1);
    }, key === 'prompt' ? 250 : 0);
  }

  function resetFilters() {
    update((current) => ({ ...current, filters: { ...defaultGalleryFilters } }));
    void loadGallery(1);
  }

  function setSelectionMode(selectionMode: boolean) {
    update((current) => ({ ...current, selectionMode, selectedIds: selectionMode ? current.selectedIds : new Set() }));
  }

  function toggleSelection(image: GalleryEntry) {
    const selectedIds = new Set(state.selectedIds);
    if (selectedIds.has(image.id)) selectedIds.delete(image.id);
    else selectedIds.add(image.id);
    update((current) => ({ ...current, selectedIds }));
  }

  function selectPage() {
    update((current) => ({ ...current, selectedIds: new Set(current.gallery?.images.map((image) => image.id) || []) }));
  }

  function clearSelection() {
    update((current) => ({ ...current, selectedIds: new Set() }));
  }

  async function batchFavorite(favorite: boolean, showToast: (message: string) => void, onAffected?: (ids: string[], favorite: boolean) => void) {
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    const result = await apiFetch<GalleryBatchResponse>(
      '/api/gallery/batch/favorite',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, favorite })
      },
      'updating selected favorites'
    );
    await loadGallery(state.page);
    onAffected?.(ids, favorite);
    showToast(get(t).messages.selectedImagesFavorited(result.count));
  }

  async function batchDelete(showToast: (message: string) => void, onDeleted?: (ids: string[]) => void) {
    const ids = [...state.selectedIds];
    if (!ids.length || !confirm(get(t).messages.deleteSelectedConfirm(ids.length))) return;
    const result = await apiFetch<GalleryBatchResponse>(
      '/api/gallery/batch/delete',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
      },
      'deleting selected images'
    );
    onDeleted?.(ids);
    clearSelection();
    await loadGallery(state.page);
    showToast(get(t).messages.selectedImagesDeleted(result.count));
  }

  async function batchDownload() {
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    const response = await fetch('/api/gallery/batch/download', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/zip', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids })
    });
    if (!response.ok) {
      throw new Error(get(t).messages.requestFailed);
    }
    downloadBlob(await response.blob(), 'gpt-images-selected.zip');
  }

  async function toggleFavorite(image: GalleryEntry, onChanged?: (image: GalleryEntry) => void) {
    await apiFetch<GalleryEntry>(
      `/api/gallery/${encodeURIComponent(image.id)}/favorite`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ favorite: !image.favorite })
      },
      'updating favorite'
    );
    await loadGallery(state.page);
    onChanged?.({ ...image, favorite: !image.favorite });
  }

  async function deleteImage(image: GalleryEntry, showToast: (message: string) => void, onDeleted?: (image: GalleryEntry) => void) {
    if (!confirm(get(t).messages.deleteImageConfirm)) return;
    await apiFetch(`/api/gallery/${encodeURIComponent(image.id)}`, { method: 'DELETE' }, 'deleting image');
    onDeleted?.(image);
    await loadGallery(state.page);
    showToast(get(t).messages.imageDeleted);
  }

  async function deleteAll(showToast: (message: string) => void, onDeleted?: () => void) {
    if (!confirm(get(t).messages.deleteAllConfirm)) return;
    await apiFetch('/api/gallery', { method: 'DELETE' }, 'deleting all images');
    onDeleted?.();
    await loadGallery(1);
    showToast(get(t).messages.allImagesDeleted);
  }

  async function importArchive(file: File, showToast: (message: string) => void) {
    const formData = new FormData();
    formData.append('archive', file, file.name);
    const result = await apiFetch<{ status: string; imported: number }>(
      '/api/import',
      {
        method: 'POST',
        body: formData
      },
      'importing archive'
    );
    await loadGallery(1);
    showToast(get(t).messages.imported(result.imported));
  }

  function cleanup() {
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = null;
    requestSeq += 1;
    abortController?.abort();
    abortController = null;
    pendingRequestKey = '';
  }

  return {
    subscribe,
    loadGallery,
    updateFilter,
    resetFilters,
    setSelectionMode,
    toggleSelection,
    selectPage,
    clearSelection,
    batchFavorite,
    batchDelete,
    batchDownload,
    toggleFavorite,
    deleteImage,
    deleteAll,
    importArchive,
    cleanup
  };
}

export const galleryStore = createGalleryStore();
