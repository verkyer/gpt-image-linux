import type { GenerateJobStatus } from '$lib/api/types';
import type { GalleryEntry } from '$lib/api/types';

export function imageUrl(filename: string) {
  return `/api/image/${encodeURIComponent(filename)}`;
}

export function thumbnailUrl(filename: string, url?: string | null) {
  return url || `/api/thumb/${encodeURIComponent(filename)}`;
}

export function downloadUrl(filename: string) {
  return `/api/download/${encodeURIComponent(filename)}`;
}

export function filenameFromImageUrl(url: string) {
  return decodeURIComponent(url.split('/').pop() || '');
}

export function formatBytes(totalBytes: number) {
  if (!Number.isFinite(totalBytes) || totalBytes <= 0) return '';
  return `${(totalBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function galleryImageSize(image: Pick<GalleryEntry, 'size' | 'image_width' | 'image_height'>) {
  if (image.size && image.size !== 'auto') return image.size;
  if (image.image_width && image.image_height) return `${image.image_width}x${image.image_height}`;
  return image.size || '-';
}

export function stageLabel(job: GenerateJobStatus | null, labels?: Record<string, string>) {
  if (!job?.stage) return '';
  if (job.status === 'error' && (job.error || job.message)) return job.error || job.message || '';

  const translated = labels?.[job.stage];
  if (!translated) return job.message || job.stage.replaceAll('_', ' ');

  const progressSuffix = job.message?.match(/\(\d+\/\d+\)$/)?.[0];
  return progressSuffix ? `${translated} ${progressSuffix}` : translated;
}

export function statusLabel(status: string | null | undefined, labels?: Record<string, string>) {
  if (!status) return '';
  return labels?.[status] || status;
}

export function operationLabel(operation: string | null | undefined, labels?: Record<string, string>) {
  if (!operation) return labels?.generation || 'generation';
  return labels?.[operation] || operation;
}

export async function copyText(text: string) {
  if (!text) return;
  await navigator.clipboard.writeText(text);
}
