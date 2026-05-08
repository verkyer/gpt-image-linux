import { apiFetch } from './api.js';
import {
  copyText,
  escapeAttribute,
  escapeHtml,
  showToast,
} from './ui.js';

const GALLERY_PAGE_SIZE = 9;

let galleryById = new Map();
let galleryPage = 1;
let galleryTotalPages = 1;
let activeLightboxImage = null;

function formatGalleryTotalSize(totalBytes) {
  const bytes = Number(totalBytes);
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export async function loadGallery(page = galleryPage, options = {}) {
  const { throwOnError = false } = options;
  try {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(GALLERY_PAGE_SIZE),
    });
    const data = await apiFetch('/api/gallery?' + params.toString(), {}, 'loading gallery');
    const grid = document.getElementById('galleryGrid');
    const empty = document.getElementById('galleryEmpty');
    const count = document.getElementById('galleryCount');
    const totalSize = document.getElementById('galleryTotalSize');
    const pagination = document.getElementById('galleryPagination');

    galleryPage = data.page || 1;
    galleryTotalPages = data.total_pages || 1;

    count.textContent = data.total > 0 ? `${data.total} image${data.total !== 1 ? 's' : ''}` : '';
    totalSize.textContent = data.total > 0 ? formatGalleryTotalSize(data.total_bytes) : '';
    renderGalleryPagination(data);

    if (!data.images || data.images.length === 0) {
      galleryById = new Map();
      grid.innerHTML = '';
      empty.classList.remove('hidden');
      pagination.classList.add('hidden');
      return;
    }

    empty.classList.add('hidden');
    pagination.classList.toggle('hidden', galleryTotalPages <= 1);
    galleryById = new Map(data.images.map(img => [img.id, normalizeGalleryImage(img)]));
    grid.innerHTML = data.images.map(renderGalleryCard).join('');
  } catch (e) {
    console.error('Failed to load gallery:', e);
    if (throwOnError) throw e;
  }
}

export function renderGalleryPagination(data = {}) {
  const pagination = document.getElementById('galleryPagination');
  const prevBtn = document.getElementById('galleryPrevBtn');
  const nextBtn = document.getElementById('galleryNextBtn');
  const pageInfo = document.getElementById('galleryPageInfo');
  const totalPages = data.total_pages || galleryTotalPages || 1;
  const page = data.page || galleryPage || 1;

  pagination.classList.toggle('hidden', totalPages <= 1);
  prevBtn.disabled = !(data.has_prev ?? page > 1);
  nextBtn.disabled = !(data.has_next ?? page < totalPages);
  pageInfo.textContent = `Page ${page} / ${totalPages}`;
}

export function changeGalleryPage(delta) {
  const nextPage = Math.min(Math.max(galleryPage + delta, 1), galleryTotalPages);
  if (nextPage === galleryPage) return;
  loadGallery(nextPage);
}

export async function deleteImage(id) {
  if (!confirm('Delete this image from gallery?')) return;
  try {
    await apiFetch('/api/gallery/' + encodeURIComponent(id), {
      method: 'DELETE',
    }, 'deleting image');
    await loadGallery(galleryPage);
    showToast('Image deleted', 'success');
  } catch (e) {
    showToast('Failed to delete: ' + e.message, 'error');
  }
}

export async function deleteAllImages({ onDeleted } = {}) {
  const confirmed = confirm(
    'Please back up your images before continuing. This will permanently delete every gallery image stored on the server. Continue?'
  );
  if (!confirmed) return;

  const btn = document.getElementById('deleteAllBtn');
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> Deleting...';

  try {
    await apiFetch('/api/gallery', {
      method: 'DELETE',
    }, 'deleting all images');

    galleryById = new Map();
    closeLightbox();
    await onDeleted?.();
    await loadGallery(1);
    showToast('All server images deleted', 'success');
  } catch (e) {
    showToast('Failed to delete all: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

export function downloadAll() {
  const btn = document.getElementById('downloadAllBtn');
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> Packing...';

  const a = document.createElement('a');
  a.href = '/api/download-all';
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  setTimeout(() => {
    btn.disabled = false;
    btn.innerHTML = original;
  }, 3000);
}

export async function copyPrompt(event, button) {
  event.stopPropagation();
  const prompt = button.dataset.prompt || '';

  try {
    await copyText(prompt);
    showToast('Copied the prompt', 'success');
  } catch (e) {
    showToast('Failed to copy prompt', 'error');
  }
}

export async function copyImageUrl(event, button) {
  event.stopPropagation();
  const filename = button.dataset.filename || '';

  try {
    await copyText(getImageUrl(filename));
    showToast('Copied the URL', 'success');
  } catch (e) {
    showToast('Failed to copy URL', 'error');
  }
}

export async function copyLightboxPrompt(event) {
  event.stopPropagation();
  try {
    await copyText(activeLightboxImage?.prompt || '');
    showToast('Copied the prompt', 'success');
  } catch (e) {
    showToast('Failed to copy prompt', 'error');
  }
}

export async function copyLightboxImageUrl(event) {
  event.stopPropagation();
  try {
    await copyText(getImageUrl(activeLightboxImage?.filename || ''));
    showToast('Copied the URL', 'success');
  } catch (e) {
    showToast('Failed to copy URL', 'error');
  }
}

export function normalizeGalleryImage(img) {
  return {
    id: img.id || '',
    filename: img.filename || (img.image_url ? img.image_url.replace('/api/image/', '') : ''),
    prompt: img.prompt || '',
    created_at: img.created_at || '',
    size: img.size || 'Unknown',
    image_width: Number.isFinite(Number(img.image_width)) ? Number(img.image_width) : null,
    image_height: Number.isFinite(Number(img.image_height)) ? Number(img.image_height) : null,
    image_dimensions_loaded: Boolean(img.image_width && img.image_height),
    model: img.model || 'Unknown',
    quality: img.quality || 'Unknown',
    output_format: img.output_format || guessFormatFromFilename(img.filename || img.image_url),
    output_compression: img.output_compression,
    response_format: img.response_format || 'Unknown',
    n: img.n,
    api_path: img.api_path || 'Unknown',
    api_preset_name: img.api_preset_name || 'Unknown',
    duration: img.duration || null,
  };
}

export function setActiveLightboxImage(image) {
  activeLightboxImage = image;
}

export function clearGalleryState() {
  galleryById = new Map();
  activeLightboxImage = null;
}

export function openLightbox(imageId) {
  const image = galleryById.get(imageId);
  if (!image) return;

  activeLightboxImage = image;
  const lightboxEditBtn = document.getElementById('lightboxEditBtn');
  if (lightboxEditBtn) {
    lightboxEditBtn.dataset.imageId = image.id || '';
    lightboxEditBtn.dataset.filename = image.filename || '';
    lightboxEditBtn.disabled = !image.id;
  }
  const lightboxImg = document.getElementById('lightboxImg');
  lightboxImg.onload = () => {
    if (activeLightboxImage?.id !== image.id) return;
    image.image_width = lightboxImg.naturalWidth || image.image_width;
    image.image_height = lightboxImg.naturalHeight || image.image_height;
    image.image_dimensions_loaded = true;
    galleryById.set(image.id, image);
    renderLightboxParams(image);
  };
  lightboxImg.onerror = () => {
    if (activeLightboxImage?.id !== image.id) return;
    image.image_dimensions_loaded = true;
    galleryById.set(image.id, image);
    renderLightboxParams(image);
  };
  lightboxImg.src = getImagePath(image.filename);
  lightboxImg.alt = image.prompt || 'Generated image';
  document.getElementById('lightboxPrompt').textContent = image.prompt || 'Not stored';
  document.getElementById('lightboxCreatedAt').textContent = image.created_at
    ? new Date(image.created_at).toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';
  renderLightboxParams(image);

  document.getElementById('lightbox').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

export function closeLightbox() {
  document.getElementById('lightboxImg').onload = null;
  document.getElementById('lightboxImg').onerror = null;
  document.getElementById('lightbox').classList.add('hidden');
  document.getElementById('lightboxImg').removeAttribute('src');
  const lightboxEditBtn = document.getElementById('lightboxEditBtn');
  if (lightboxEditBtn) {
    lightboxEditBtn.dataset.imageId = '';
    lightboxEditBtn.dataset.filename = '';
    lightboxEditBtn.disabled = true;
  }
  activeLightboxImage = null;
  document.body.style.overflow = '';
}

function renderGalleryCard(img) {
  const date = new Date(img.created_at);
  const timeStr = date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const prompt = img.prompt || '';
  const filename = img.filename || '';
  const imagePath = getImagePath(filename);
  const downloadPath = getDownloadPath(filename);
  const shortPrompt = prompt.length > 80 ? prompt.substring(0, 80) + '...' : prompt;
  const escapedImageIdAttr = escapeAttribute(img.id);
  const escapedPromptAttr = escapeAttribute(prompt);
  const escapedFilenameAttr = escapeAttribute(filename);
  const escapedShortPrompt = escapeHtml(shortPrompt);

  return `
    <div class="gallery-card bg-zinc-900/60 border border-zinc-800 rounded-xl overflow-hidden">
      <div class="relative cursor-pointer group" data-image-id="${escapedImageIdAttr}" onclick="openLightbox(this.dataset.imageId)">
        <img src="${imagePath}" alt="${escapedPromptAttr}"
          class="w-full aspect-square object-cover bg-zinc-800" loading="lazy">
        <button type="button"
          onclick="event.stopPropagation(); prepareGalleryImageForEdit(this.dataset.imageId, this.dataset.filename)"
          data-image-id="${escapedImageIdAttr}"
          data-filename="${escapedFilenameAttr}"
          class="absolute left-2 bottom-2 z-10 p-1.5 rounded-lg bg-zinc-950/80 text-zinc-200 border border-zinc-700/80 hover:text-sky-300 hover:border-sky-400/40 hover:bg-zinc-900 transition-colors"
          title="Edit this image" aria-label="Edit this image">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L12 14l-4 1 1-4 8.586-8.586z"/>
          </svg>
        </button>
        <div class="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex items-end p-3 pl-12">
          <span class="text-xs text-white truncate">${escapedShortPrompt}</span>
        </div>
      </div>
      <div class="p-3">
        <p class="text-xs text-zinc-500 truncate" title="${escapedPromptAttr}">${escapedShortPrompt}</p>
        <div class="flex items-center justify-between mt-2">
          <span class="text-xs text-zinc-600">${timeStr}</span>
          <div class="flex gap-1">
            <a href="${downloadPath}" download
              class="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
              onclick="event.stopPropagation()">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
            </a>
            <button onclick="event.stopPropagation(); deleteImage(this.dataset.imageId)"
              data-image-id="${escapedImageIdAttr}"
              class="p-1.5 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-red-900/20 transition-colors"
              title="Delete">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
              </svg>
            </button>
            <button onclick="copyPrompt(event, this)"
              data-prompt="${escapedPromptAttr}"
              class="p-1.5 rounded-lg text-zinc-500 hover:text-emerald-400 hover:bg-emerald-900/20 transition-colors"
              title="Copy prompt">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
              </svg>
            </button>
            <button onclick="copyImageUrl(event, this)"
              data-filename="${escapeAttribute(img.filename)}"
              class="p-1.5 rounded-lg text-zinc-500 hover:text-sky-300 hover:bg-sky-900/20 transition-colors"
              title="Copy image URL" aria-label="Copy image URL">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 010 5.656l-2 2a4 4 0 01-5.656-5.656l1-1m8.656 2.656l1-1a4 4 0 00-5.656-5.656l-2 2a4 4 0 000 5.656"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>`;
}

function getImageUrl(filename) {
  return new URL(getImagePath(filename), window.location.origin).href;
}

function getImagePath(filename) {
  return '/api/image/' + encodeURIComponent(filename || '');
}

function getDownloadPath(filename) {
  return '/api/download/' + encodeURIComponent(filename || '');
}

function guessFormatFromFilename(filename) {
  const ext = String(filename || '').split('.').pop()?.toLowerCase();
  if (ext === 'jpg') return 'jpeg';
  return ['png', 'jpeg', 'webp'].includes(ext) ? ext : 'Unknown';
}

function formatImageResolution(image) {
  if (image?.image_width && image?.image_height) {
    return `${image.image_width}x${image.image_height}`;
  }
  return image?.image_dimensions_loaded ? 'Unknown' : 'Loading...';
}

function formatParameterValue(value) {
  if (value === null || value === undefined || value === '') return 'Not stored';
  return String(value);
}

function renderParameter(label, value) {
  return `
    <div class="rounded-xl border border-zinc-800 bg-zinc-950/45 px-3 py-2.5">
      <dt class="text-[11px] font-semibold uppercase tracking-wider text-zinc-600">${escapeHtml(label)}</dt>
      <dd class="mt-1 break-words font-mono text-xs text-zinc-200">${escapeHtml(formatParameterValue(value))}</dd>
    </div>`;
}

function renderLightboxParams(image) {
  document.getElementById('lightboxParams').innerHTML = [
    renderParameter('Duration', image.duration),
    renderParameter('Model', image.model),
    renderParameter('Size', formatImageResolution(image)),
    renderParameter('Quality', image.quality),
    renderParameter('Output Format', image.output_format),
    renderParameter('Output Compression', image.output_compression),
    renderParameter('Quantity', image.n),
    renderParameter('Response Format', image.response_format),
    renderParameter('Preset', image.api_preset_name),
    renderParameter('API Path', image.api_path),
  ].join('');
}
