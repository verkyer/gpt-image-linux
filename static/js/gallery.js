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
let galleryFilters = {
  prompt: '',
  model: '',
  preset: '',
  size: '',
  dateFrom: '',
  dateTo: '',
  favorite: false,
};
let galleryFilterDebounce = null;

function formatGalleryTotalSize(totalBytes) {
  const bytes = Number(totalBytes);
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export async function loadGallery(page = galleryPage, options = {}) {
  const { throwOnError = false } = options;
  try {
    const params = buildGalleryQueryParams(page);
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
    renderGalleryFilterOptions(data.filter_options || {});
    renderGalleryFilterState();
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

function buildGalleryQueryParams(page) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(GALLERY_PAGE_SIZE),
  });
  const trimmedPrompt = galleryFilters.prompt.trim();
  if (trimmedPrompt) params.set('prompt', trimmedPrompt);
  if (galleryFilters.model) params.set('model', galleryFilters.model);
  if (galleryFilters.preset) params.set('preset', galleryFilters.preset);
  if (galleryFilters.size) params.set('size', galleryFilters.size);
  if (galleryFilters.dateFrom) params.set('date_from', galleryFilters.dateFrom);
  if (galleryFilters.dateTo) params.set('date_to', galleryFilters.dateTo);
  if (galleryFilters.favorite) params.set('favorite', 'true');
  return params;
}

function hasGalleryFilters() {
  return Boolean(
    galleryFilters.prompt.trim() ||
    galleryFilters.model ||
    galleryFilters.preset ||
    galleryFilters.size ||
    galleryFilters.dateFrom ||
    galleryFilters.dateTo ||
    galleryFilters.favorite
  );
}

function renderGalleryFilterState() {
  const resetBtn = document.getElementById('galleryResetFiltersBtn');
  const emptyTitle = document.getElementById('galleryEmptyTitle');
  const emptyText = document.getElementById('galleryEmptyText');
  const emptyHint = document.getElementById('galleryEmptyHint');

  resetBtn?.classList.toggle('hidden', !hasGalleryFilters());
  if (!emptyTitle || !emptyText || !emptyHint) return;

  if (hasGalleryFilters()) {
    emptyTitle.textContent = 'No images match';
    emptyText.textContent = 'Adjust or reset the gallery filters.';
    emptyHint.textContent = 'Filtered by prompt, model, preset, size, date, or favorites';
  } else {
    emptyTitle.textContent = 'Your gallery is empty';
    emptyText.textContent = 'Describe the image you want to create and hit Generate to get started';
    emptyHint.textContent = 'Try a prompt like "A serene mountain lake at sunrise"';
  }
}

function renderGalleryFilterOptions(options = {}) {
  updateGallerySelectOptions('galleryModelFilter', options.models || [], galleryFilters.model, 'All models');
  updateGallerySelectOptions('galleryPresetFilter', options.presets || [], galleryFilters.preset, 'All presets');
  updateGallerySelectOptions('gallerySizeFilter', options.sizes || [], galleryFilters.size, 'All sizes');
}

function updateGallerySelectOptions(id, values, selectedValue, emptyLabel) {
  const select = document.getElementById(id);
  if (!select) return;

  const normalizedValues = Array.from(new Set(values.filter(Boolean).map(String)));
  if (selectedValue && !normalizedValues.includes(selectedValue)) {
    normalizedValues.unshift(selectedValue);
  }

  select.innerHTML = [
    `<option value="">${escapeHtml(emptyLabel)}</option>`,
    ...normalizedValues.map(value => {
      const selected = value === selectedValue ? ' selected' : '';
      return `<option value="${escapeAttribute(value)}"${selected}>${escapeHtml(value)}</option>`;
    }),
  ].join('');
}

export function updateGalleryFilter(key, value, options = {}) {
  if (!Object.prototype.hasOwnProperty.call(galleryFilters, key)) return;
  const { debounce = false } = options;
  galleryFilters = {
    ...galleryFilters,
    [key]: key === 'favorite' ? Boolean(value) : String(value || ''),
  };

  if (galleryFilterDebounce) {
    clearTimeout(galleryFilterDebounce);
    galleryFilterDebounce = null;
  }

  if (debounce) {
    galleryFilterDebounce = setTimeout(() => {
      galleryFilterDebounce = null;
      loadGallery(1);
    }, 250);
    return;
  }

  loadGallery(1);
}

export function resetGalleryFilters() {
  if (galleryFilterDebounce) {
    clearTimeout(galleryFilterDebounce);
    galleryFilterDebounce = null;
  }
  galleryFilters = {
    prompt: '',
    model: '',
    preset: '',
    size: '',
    dateFrom: '',
    dateTo: '',
    favorite: false,
  };

  const promptInput = document.getElementById('galleryPromptFilter');
  const modelSelect = document.getElementById('galleryModelFilter');
  const presetSelect = document.getElementById('galleryPresetFilter');
  const sizeSelect = document.getElementById('gallerySizeFilter');
  const dateFromInput = document.getElementById('galleryDateFromFilter');
  const dateToInput = document.getElementById('galleryDateToFilter');
  const favoriteInput = document.getElementById('galleryFavoriteFilter');

  if (promptInput) promptInput.value = '';
  if (modelSelect) modelSelect.value = '';
  if (presetSelect) presetSelect.value = '';
  if (sizeSelect) sizeSelect.value = '';
  if (dateFromInput) dateFromInput.value = '';
  if (dateToInput) dateToInput.value = '';
  if (favoriteInput) favoriteInput.checked = false;

  loadGallery(1);
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

export async function deleteImage(eventOrId, maybeId) {
  if (eventOrId?.preventDefault) {
    eventOrId.preventDefault();
    eventOrId.stopPropagation();
    eventOrId.stopImmediatePropagation?.();
  }

  const id = maybeId || eventOrId;
  if (!id || !confirm('Delete this image from gallery?')) return;

  try {
    await apiFetch('/api/gallery/' + encodeURIComponent(id), {
      method: 'DELETE',
    }, 'deleting image');
    if (activeLightboxImage?.id === id) closeLightbox();
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

export function openImportArchivePicker() {
  document.getElementById('importArchiveInput')?.click();
}

export async function importArchive(event) {
  const file = event.target.files?.[0] || null;
  if (!file) return;

  const input = event.target;
  const btn = document.getElementById('importArchiveBtn');
  const original = btn?.innerHTML || '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> Importing...';
  }

  try {
    const formData = new FormData();
    formData.append('archive', file, file.name || 'gpt-images.zip');
    const result = await apiFetch('/api/import', {
      method: 'POST',
      body: formData,
    }, 'importing gallery archive');
    await loadGallery(1);
    showToast(`Imported ${result.imported || 0} image${result.imported === 1 ? '' : 's'}`, 'success');
  } catch (e) {
    showToast('Failed to import: ' + e.message, 'error');
  } finally {
    input.value = '';
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }
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

export async function toggleGalleryFavorite(event, button) {
  event.stopPropagation();
  const imageId = button.dataset.imageId || '';
  const nextFavorite = button.dataset.favorite !== 'true';
  if (!imageId) return;

  button.disabled = true;
  try {
    const updated = await apiFetch('/api/gallery/' + encodeURIComponent(imageId) + '/favorite', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ favorite: nextFavorite }),
    }, 'updating favorite');

    const normalized = normalizeGalleryImage(updated);
    galleryById.set(normalized.id, normalized);
    if (activeLightboxImage?.id === normalized.id) {
      activeLightboxImage = normalized;
      renderFavoriteButton(document.getElementById('lightboxFavoriteBtn'), normalized);
    }
    await loadGallery(galleryPage);
    showToast(nextFavorite ? 'Added to favorites' : 'Removed from favorites', 'success');
  } catch (e) {
    showToast('Failed to update favorite: ' + e.message, 'error');
  } finally {
    button.disabled = false;
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
    favorite: Boolean(img.favorite),
  };
}

export function setActiveLightboxImage(image) {
  activeLightboxImage = image;
}

export function clearGalleryState() {
  galleryById = new Map();
  activeLightboxImage = null;
}

function renderFavoriteButton(button, image) {
  if (!button) return;
  const favorite = Boolean(image?.favorite);
  const title = favorite ? 'Remove from favorites' : 'Add to favorites';
  button.dataset.imageId = image?.id || '';
  button.dataset.favorite = favorite ? 'true' : 'false';
  button.disabled = !image?.id;
  button.title = title;
  button.setAttribute('aria-label', title);
  button.classList.toggle('text-amber-300', favorite);
  button.classList.toggle('bg-amber-400/10', favorite);
  button.classList.toggle('border-amber-500/30', favorite);
  button.classList.toggle('text-zinc-300', !favorite);
  button.classList.toggle('bg-zinc-800', !favorite);
  button.classList.toggle('border-zinc-800', !favorite);
  button.querySelector('svg')?.setAttribute('fill', favorite ? 'currentColor' : 'none');
}

export function openLightbox(eventOrImageId) {
  const clickTarget = eventOrImageId?.target;
  if (clickTarget?.closest?.('[data-gallery-action]')) return;

  const imageId = typeof eventOrImageId === 'string'
    ? eventOrImageId
    : eventOrImageId?.currentTarget?.dataset?.imageId;
  const image = galleryById.get(imageId);
  if (!image) return;

  activeLightboxImage = image;
  const lightboxEditBtn = document.getElementById('lightboxEditBtn');
  if (lightboxEditBtn) {
    lightboxEditBtn.dataset.imageId = image.id || '';
    lightboxEditBtn.dataset.filename = image.filename || '';
    lightboxEditBtn.disabled = !image.id;
  }
  const lightboxFavoriteBtn = document.getElementById('lightboxFavoriteBtn');
  if (lightboxFavoriteBtn) {
    renderFavoriteButton(lightboxFavoriteBtn, image);
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
  const lightboxFavoriteBtn = document.getElementById('lightboxFavoriteBtn');
  if (lightboxFavoriteBtn) {
    renderFavoriteButton(lightboxFavoriteBtn, null);
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
  const favoriteClass = img.favorite
    ? 'text-amber-300 border-amber-500/30 bg-amber-400/10 hover:bg-amber-400/15'
    : 'text-zinc-300 border-zinc-700/80 bg-zinc-950/80 hover:text-amber-300 hover:border-amber-300/70 hover:bg-zinc-900';
  const favoriteTitle = img.favorite ? 'Remove from favorites' : 'Add to favorites';

  return `
    <div class="gallery-card bg-zinc-900/60 border border-zinc-800 rounded-xl overflow-hidden">
      <div class="relative cursor-pointer group" data-image-id="${escapedImageIdAttr}" onclick="openLightbox(event)">
        <img src="${imagePath}" alt="${escapedPromptAttr}"
          class="w-full aspect-square object-cover bg-zinc-800" loading="lazy">
        <button type="button"
          onclick="toggleGalleryFavorite(event, this)"
          data-gallery-action="favorite"
          data-image-id="${escapedImageIdAttr}"
          data-favorite="${img.favorite ? 'true' : 'false'}"
          class="absolute right-2 top-2 z-10 p-1.5 rounded-lg border transition-colors ${favoriteClass}"
          title="${favoriteTitle}" aria-label="${favoriteTitle}">
          <svg class="w-3.5 h-3.5" fill="${img.favorite ? 'currentColor' : 'none'}" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M11.48 3.499a.6.6 0 011.04 0l2.32 4.701a.6.6 0 00.452.329l5.19.754a.6.6 0 01.333 1.023l-3.755 3.66a.6.6 0 00-.173.531l.887 5.169a.6.6 0 01-.87.632l-4.642-2.441a.6.6 0 00-.558 0l-4.642 2.441a.6.6 0 01-.87-.632l.887-5.169a.6.6 0 00-.173-.531l-3.755-3.66a.6.6 0 01.333-1.023l5.19-.754a.6.6 0 00.452-.329l2.32-4.701z"/>
          </svg>
        </button>
        <button type="button"
          onclick="event.preventDefault(); event.stopPropagation(); prepareGalleryImageForEdit(this.dataset.imageId, this.dataset.filename)"
          data-gallery-action="edit"
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
              data-gallery-action="download"
              class="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
              onclick="event.stopPropagation()">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
            </a>
            <button type="button" onclick="deleteImage(event, this.dataset.imageId)"
              data-gallery-action="delete"
              data-image-id="${escapedImageIdAttr}"
              class="p-1.5 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-red-900/20 transition-colors"
              title="Delete" aria-label="Delete">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
              </svg>
            </button>
            <button type="button" onclick="copyPrompt(event, this)"
              data-gallery-action="copy-prompt"
              data-prompt="${escapedPromptAttr}"
              class="p-1.5 rounded-lg text-zinc-500 hover:text-emerald-400 hover:bg-emerald-900/20 transition-colors"
              title="Copy prompt" aria-label="Copy prompt">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
              </svg>
            </button>
            <button type="button" onclick="copyImageUrl(event, this)"
              data-gallery-action="copy-url"
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
