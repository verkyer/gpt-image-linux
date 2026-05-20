import { get } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import type { ToastOptions, ToastVariant } from '$lib/stores/ui';
import { formatBytes } from '$lib/utils/format';
import type { GalleryBatchResponse, GalleryResponse } from '$lib/api/types';
import type { GalleryOperationStatus, GalleryState } from '$lib/stores/gallery';

type GalleryActionDeps = {
  getState: () => GalleryState;
  loadGallery: (page?: number, includeTotalBytes?: boolean) => Promise<void>;
  clearSelection: () => void;
  setOperationStatus: (operationStatus: GalleryOperationStatus | null) => void;
  clearPendingSingleDeletes: () => void;
};

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

function batchToastMessage(action: 'delete' | 'favorite', result: GalleryBatchResponse) {
  const updatedCount = result.updated_count ?? result.count;
  const missingCount = result.missing_count ?? Math.max(0, (result.requested_count || 0) - updatedCount);
  if (action === 'delete') return get(t).messages.selectedImagesDeleted(updatedCount, missingCount);
  return get(t).messages.selectedImagesFavorited(updatedCount, missingCount);
}

export function createGalleryActions(deps: GalleryActionDeps) {
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
    deps.setOperationStatus({ kind, label, detail: initialDetail, progress: total > 0 ? 0 : null });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      const chunk = new Uint8Array(value.byteLength);
      chunk.set(value);
      chunks.push(chunk);
      loaded += value.byteLength;
      deps.setOperationStatus({
        kind,
        label,
        detail: operationProgressDetail(loaded, total) || initialDetail,
        progress: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : null
      });
    }

    return new Blob(chunks, { type: response.headers.get('Content-Type') || 'application/zip' });
  }

  async function batchFavorite(favorite: boolean, showToast: (message: string) => void, onAffected?: (ids: string[], favorite: boolean) => void) {
    const state = deps.getState();
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
    await deps.loadGallery(state.page);
    onAffected?.(ids, favorite);
    showToast(batchToastMessage('favorite', result));
  }

  async function batchDelete(
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onDeleted?: (ids: string[]) => void
  ) {
    const state = deps.getState();
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
    deps.clearSelection();
    await deps.loadGallery(state.page);
    showToast(batchToastMessage('delete', result));
  }

  async function batchDownload(showToast?: (message: string) => void) {
    const ids = [...deps.getState().selectedIds];
    if (!ids.length) return;
    deps.setOperationStatus({
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
      if (!response.ok) throw new Error(get(t).messages.requestFailed);
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
      deps.setOperationStatus(null);
    }
  }

  async function exportArchive(showToast?: (message: string) => void) {
    deps.setOperationStatus({
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
      if (!response.ok) throw new Error(get(t).messages.requestFailed);
      const blob = await downloadResponseBlob(response, 'export', get(t).gallery.exportingArchive, get(t).gallery.browserSavingDownload);
      downloadBlob(blob, filenameFromContentDisposition(response.headers.get('Content-Disposition'), 'gpt-images.zip'));
      showToast?.(get(t).messages.exportReady);
    } finally {
      deps.setOperationStatus(null);
    }
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

    deps.clearPendingSingleDeletes();
    await apiFetch('/api/gallery', { method: 'DELETE' }, 'deleting all images');
    onDeleted?.();
    await deps.loadGallery(1);
    showToast(get(t).messages.allImagesDeleted);
  }

  async function importArchive(file: File, showToast: (message: string) => void) {
    const formData = new FormData();
    formData.append('archive', file, file.name);
    deps.setOperationStatus({
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
      deps.setOperationStatus({
        kind: 'import',
        label: get(t).gallery.importingArchive,
        detail: get(t).gallery.refreshingAfterImport,
        progress: null
      });
      await deps.loadGallery(1);
      showToast(get(t).messages.imported(result.imported));
    } finally {
      deps.setOperationStatus(null);
    }
  }

  return {
    batchFavorite,
    batchDelete,
    batchDownload,
    exportArchive,
    deleteAll,
    importArchive
  };
}
