import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import type { ToastOptions, ToastVariant } from '$lib/stores/ui';
import { formatBytes } from '$lib/utils/format';
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
  operationStatus: GalleryOperationStatus | null;
  page: number;
  filters: GalleryFilters;
  selectionMode: boolean;
  selectedIds: Set<string>;
};

export type GalleryOperationStatus = {
  kind: 'import' | 'export' | 'download';
  label: string;
  detail: string;
  progress: number | null;
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
  operationStatus: null,
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

export type GalleryUrlState = {
  page: number;
  filters: GalleryFilters;
};

function parsePage(value: string | null | undefined) {
  const parsed = Number.parseInt(String(value || ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

function parseBoolean(value: string | null | undefined) {
  const normalized = String(value || '').toLowerCase();
  return normalized === 'true' || normalized === '1';
}

export function readGalleryUrlState(searchParams: URLSearchParams): GalleryUrlState {
  return {
    page: parsePage(searchParams.get('page')),
    filters: {
      prompt: searchParams.get('prompt') || '',
      model: searchParams.get('model') || '',
      preset: searchParams.get('preset') || '',
      size: searchParams.get('size') || '',
      dateFrom: searchParams.get('date_from') || '',
      dateTo: searchParams.get('date_to') || '',
      favorite: parseBoolean(searchParams.get('favorite'))
    }
  };
}

export function writeGalleryUrlState(searchParams: URLSearchParams, page: number, filters: GalleryFilters) {
  searchParams.delete('page');
  searchParams.delete('prompt');
  searchParams.delete('model');
  searchParams.delete('preset');
  searchParams.delete('size');
  searchParams.delete('date_from');
  searchParams.delete('date_to');
  searchParams.delete('favorite');

  if (page > 1) searchParams.set('page', String(page));
  if (filters.prompt.trim()) searchParams.set('prompt', filters.prompt.trim());
  if (filters.model) searchParams.set('model', filters.model);
  if (filters.preset) searchParams.set('preset', filters.preset);
  if (filters.size) searchParams.set('size', filters.size);
  if (filters.dateFrom) searchParams.set('date_from', filters.dateFrom);
  if (filters.dateTo) searchParams.set('date_to', filters.dateTo);
  if (filters.favorite) searchParams.set('favorite', 'true');
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

function parseHeaderInt(headers: Headers, name: string) {
  const parsed = Number.parseInt(headers.get(name) || '', 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function filenameFromContentDisposition(header: string | null, fallback: string) {
  const match = header?.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
}

function operationProgressDetail(loaded: number, total: number) {
  if (total > 0) return `${formatBytes(loaded)} / ${formatBytes(total)}`;
  return loaded > 0 ? formatBytes(loaded) : '';
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
  const pendingSingleDeletes = new Map<string, { image: GalleryEntry; timer: ReturnType<typeof setTimeout> }>();

  subscribe((value) => {
    state = value;
  });

  function setOperationStatus(operationStatus: GalleryOperationStatus | null) {
    update((current) => ({ ...current, operationStatus }));
  }

  function batchToastMessage(action: 'delete' | 'favorite', result: GalleryBatchResponse) {
    const updatedCount = result.updated_count ?? result.count;
    const missingCount = result.missing_count ?? Math.max(0, (result.requested_count || 0) - updatedCount);
    if (action === 'delete') return get(t).messages.selectedImagesDeleted(updatedCount, missingCount);
    return get(t).messages.selectedImagesFavorited(updatedCount, missingCount);
  }

  async function downloadResponseBlob(
    response: Response,
    kind: GalleryOperationStatus['kind'],
    label: string,
    initialDetail: string
  ) {
    const total = Number.parseInt(response.headers.get('Content-Length') || '0', 10);
    if (!response.body) return response.blob();

    const reader = response.body.getReader();
    const chunks: BlobPart[] = [];
    let loaded = 0;
    setOperationStatus({ kind, label, detail: initialDetail, progress: total > 0 ? 0 : null });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      const chunk = new Uint8Array(value.byteLength);
      chunk.set(value);
      chunks.push(chunk);
      loaded += value.byteLength;
      setOperationStatus({
        kind,
        label,
        detail: operationProgressDetail(loaded, total) || initialDetail,
        progress: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : null
      });
    }

    return new Blob(chunks, { type: response.headers.get('Content-Type') || 'application/zip' });
  }

  function setPageAndFilters(page: number, filters: GalleryFilters) {
    update((current) => ({
      ...current,
      page,
      filters: { ...filters },
      selectedIds: new Set(),
      selectionMode: false
    }));
  }

  function pendingImageMatchesFilters(image: GalleryEntry, filters: GalleryFilters) {
    if (filters.prompt.trim() && !image.prompt.toLowerCase().includes(filters.prompt.trim().toLowerCase())) return false;
    if (filters.model && image.model !== filters.model) return false;
    if (filters.preset && image.api_preset_name !== filters.preset) return false;
    if (filters.size && image.size !== filters.size) return false;
    if (filters.favorite && !image.favorite) return false;
    if (filters.dateFrom || filters.dateTo) {
      const timestamp = image.completed_at || image.created_at;
      if (filters.dateFrom && timestamp < `${filters.dateFrom}T00:00:00`) return false;
      if (filters.dateTo && timestamp > `${filters.dateTo}T23:59:59.999999`) return false;
    }
    return true;
  }

  function filterPendingGallery(gallery: GalleryResponse, includeTotalBytes: boolean, filters: GalleryFilters) {
    if (!pendingSingleDeletes.size) return gallery;

    const pendingIds = new Set(pendingSingleDeletes.keys());
    const matchingPending = [...pendingSingleDeletes.values()].filter((pending) => pendingImageMatchesFilters(pending.image, filters));
    const hiddenBytes = matchingPending.reduce((sum, pending) => sum + (pending.image.bytes || 0), 0);

    return {
      ...gallery,
      images: gallery.images.filter((image) => !pendingIds.has(image.id)),
      total: Math.max(0, gallery.total - matchingPending.length),
      total_bytes: includeTotalBytes ? Math.max(0, gallery.total_bytes - hiddenBytes) : gallery.total_bytes
    };
  }

  async function loadGallery(page = state.page, includeTotalBytes = false) {
    const filters = { ...state.filters };
    const params = buildGalleryParams(page, filters, includeTotalBytes);
    const requestKey = params.toString();
    if (state.loading && pendingRequestKey === requestKey) return;
    const seq = ++requestSeq;
    pendingRequestKey = requestKey;
    abortController?.abort();
    abortController = new AbortController();
    update((current) => ({ ...current, loading: true, page }));
    try {
      const gallery = await apiFetch<GalleryResponse>(
        `/api/gallery?${requestKey}`,
        { signal: abortController.signal },
        'loading gallery'
      );
      if (seq !== requestSeq) return;
      const filteredGallery = filterPendingGallery(gallery, includeTotalBytes, filters);
      const visibleIds = new Set(filteredGallery.images.map((image) => image.id));
      const selectedIds = new Set([...state.selectedIds].filter((id) => visibleIds.has(id)));
      update((current) => ({
        ...current,
        gallery: filteredGallery,
        page: filteredGallery.page,
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
      page: 1,
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
    update((current) => ({ ...current, page: 1, filters: { ...defaultGalleryFilters } }));
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
    showToast(batchToastMessage('favorite', result));
  }

  async function batchDelete(
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onDeleted?: (ids: string[]) => void
  ) {
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    const selectedEntries = state.gallery?.images.filter((image) => ids.includes(image.id)) || [];
    const selectedBytes = selectedEntries.reduce((sum, image) => sum + (image.bytes || 0), 0);
    const details = [
      get(t).confirm.deleteSelectedDetail(ids.length),
      selectedEntries.length ? get(t).confirm.deleteSelectedSize(formatBytes(selectedBytes)) : ''
    ].filter(Boolean);
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deleteSelectedTitle(ids.length),
      message: get(t).confirm.deleteSelectedMessage(ids.length),
      details,
      confirmLabel: get(t).gallery.deleteSelected,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;
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
    showToast(batchToastMessage('delete', result));
  }

  async function batchDownload(showToast?: (message: string) => void) {
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    setOperationStatus({
      kind: 'download',
      label: get(t).gallery.downloadingSelected,
      detail: get(t).gallery.downloadPreparing(ids.length),
      progress: null
    });
    try {
      const response = await fetch('/api/gallery/batch/download', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/zip', 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
      });
      if (!response.ok) {
        throw new Error(get(t).messages.requestFailed);
      }
      const blob = await downloadResponseBlob(
        response,
        'download',
        get(t).gallery.downloadingSelected,
        get(t).gallery.browserSavingDownload
      );
      downloadBlob(blob, filenameFromContentDisposition(response.headers.get('Content-Disposition'), 'gpt-images-selected.zip'));
      const requestedCount = parseHeaderInt(response.headers, 'X-Gallery-Requested-Count') || ids.length;
      const exportedCount = parseHeaderInt(response.headers, 'X-Gallery-Exported-Count') || requestedCount;
      const missingCount = parseHeaderInt(response.headers, 'X-Gallery-Missing-Count');
      showToast?.(get(t).messages.selectedImagesDownloaded(exportedCount, missingCount));
    } finally {
      setOperationStatus(null);
    }
  }

  async function exportArchive(showToast?: (message: string) => void) {
    setOperationStatus({
      kind: 'export',
      label: get(t).gallery.exportingArchive,
      detail: get(t).gallery.exportPreparing,
      progress: null
    });
    try {
      const response = await fetch('/api/download-all', {
        method: 'GET',
        credentials: 'same-origin',
        headers: { Accept: 'application/zip' }
      });
      if (!response.ok) {
        throw new Error(get(t).messages.requestFailed);
      }
      const blob = await downloadResponseBlob(response, 'export', get(t).gallery.exportingArchive, get(t).gallery.browserSavingDownload);
      downloadBlob(blob, filenameFromContentDisposition(response.headers.get('Content-Disposition'), 'gpt-images.zip'));
      showToast?.(get(t).messages.exportReady);
    } finally {
      setOperationStatus(null);
    }
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

  function cancelPendingSingleDelete(imageId: string) {
    const pending = pendingSingleDeletes.get(imageId);
    if (!pending) return false;
    clearTimeout(pending.timer);
    pendingSingleDeletes.delete(imageId);
    return true;
  }

  async function deleteImage(
    image: GalleryEntry,
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onPendingHidden?: (image: GalleryEntry) => void,
    onDeleted?: (image: GalleryEntry) => void
  ) {
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deleteImageTitle,
      message: get(t).confirm.deleteImageMessage(image.filename),
      details: [get(t).confirm.deleteImageDetail],
      confirmLabel: get(t).common.delete,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;

    cancelPendingSingleDelete(image.id);
    pendingSingleDeletes.set(image.id, {
      image,
      timer: setTimeout(async () => {
        pendingSingleDeletes.delete(image.id);
        try {
          await apiFetch(`/api/gallery/${encodeURIComponent(image.id)}`, { method: 'DELETE' }, 'deleting image');
          await loadGallery(state.page);
          onDeleted?.(image);
          showToast(get(t).messages.imageDeleted);
        } catch (error) {
          if (isAbortError(error)) return;
          await loadGallery(state.page);
          showToast(get(t).messages.imageDeletionFailed, 'error');
        }
      }, 5000)
    });

    update((current) => {
      if (!current.gallery) return current;
      return {
        ...current,
        gallery: {
          ...current.gallery,
          images: current.gallery.images.filter((entry) => entry.id !== image.id),
          total: Math.max(0, current.gallery.total - 1),
          total_bytes: Math.max(0, current.gallery.total_bytes - (image.bytes || 0))
        },
        selectedIds: new Set([...current.selectedIds].filter((id) => id !== image.id))
      };
    });
    onPendingHidden?.(image);

    showToast(get(t).messages.imageDeletionPending, 'status', {
      actionLabel: get(t).common.undo,
      onAction: async () => {
        if (!cancelPendingSingleDelete(image.id)) return;
        await loadGallery(state.page);
        showToast(get(t).messages.imageDeletionUndone);
      },
      durationMs: 5000
    });
  }

  async function deleteAll(
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onDeleted?: () => void
  ) {
    const stats = await apiFetch<GalleryResponse>(
      '/api/gallery?page=1&page_size=1&include_total_bytes=true',
      {},
      'loading gallery delete impact'
    );
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deleteAllTitle,
      message: get(t).confirm.deleteAllMessage(stats.total),
      details: [get(t).confirm.deleteAllDetail(formatBytes(stats.total_bytes)), get(t).confirm.deleteAllConfirmHint],
      confirmLabel: get(t).confirm.deleteAllConfirmLabel,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      requiredText: get(t).confirm.deleteAllConfirmLabel,
      requiredTextLabel: get(t).confirm.deleteAllConfirmHint,
      variant: 'danger'
    });
    if (!confirmed) return;

    pendingSingleDeletes.forEach((pending) => clearTimeout(pending.timer));
    pendingSingleDeletes.clear();
    await apiFetch('/api/gallery', { method: 'DELETE' }, 'deleting all images');
    onDeleted?.();
    await loadGallery(1);
    showToast(get(t).messages.allImagesDeleted);
  }

  async function importArchive(file: File, showToast: (message: string) => void) {
    const formData = new FormData();
    formData.append('archive', file, file.name);
    setOperationStatus({
      kind: 'import',
      label: get(t).gallery.importingArchive,
      detail: get(t).gallery.importingArchiveDetail(formatBytes(file.size)),
      progress: null
    });
    try {
      const result = await apiFetch<{ status: string; imported: number }>(
        '/api/import',
        {
          method: 'POST',
          body: formData
        },
        'importing archive'
      );
      setOperationStatus({
        kind: 'import',
        label: get(t).gallery.importingArchive,
        detail: get(t).gallery.refreshingAfterImport,
        progress: null
      });
      await loadGallery(1);
      showToast(get(t).messages.imported(result.imported));
    } finally {
      setOperationStatus(null);
    }
  }

  function cleanup() {
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = null;
    pendingSingleDeletes.forEach((pending) => clearTimeout(pending.timer));
    pendingSingleDeletes.clear();
    requestSeq += 1;
    abortController?.abort();
    abortController = null;
    pendingRequestKey = '';
    setOperationStatus(null);
  }

  return {
    subscribe,
    loadGallery,
    setPageAndFilters,
    updateFilter,
    resetFilters,
    setSelectionMode,
    toggleSelection,
    selectPage,
    clearSelection,
    batchFavorite,
    batchDelete,
    batchDownload,
    exportArchive,
    toggleFavorite,
    deleteImage,
    deleteAll,
    importArchive,
    cancelPendingSingleDelete,
    cleanup
  };
}

export const galleryStore = createGalleryStore();
