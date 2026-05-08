import { apiFetch, isNetworkFetchError } from './api.js';
import { loadGallery, normalizeGalleryImage, setActiveLightboxImage } from './gallery.js';
import { isResponsesApiSelected } from './settings.js';
import {
  clampCompressionInput,
  clampQuantityInput,
  hideError,
  showError,
  showToast,
} from './ui.js';

const PREVIEW_STAGES = {
  generationImages: [
    { key: 'queued', label: 'Queued' },
    { key: 'starting_generation', label: 'Starting generation' },
    { key: 'building_generation_payload', label: 'Building generation payload' },
    { key: 'waiting_for_api', label: 'Waiting for API response' },
    { key: 'received_api_response', label: 'Received API response' },
    { key: 'parsing_json_response', label: 'Parsing JSON response' },
    { key: 'extracting_generation_data', label: 'Extracting image data' },
    { key: 'decoding_b64_json', label: 'Decoding b64_json' },
    { key: 'downloading_image_url', label: 'Downloading image URL' },
    { key: 'extracting_image_bytes', label: 'Extracting image bytes' },
    { key: 'validating_image_bytes', label: 'Validating image bytes' },
    { key: 'saving_image_file', label: 'Saving image file' },
    { key: 'updating_gallery', label: 'Updating gallery metadata' },
    { key: 'finalizing_preview', label: 'Finalizing preview' },
    { key: 'completed', label: 'Completed' },
  ],
  generationResponses: [
    { key: 'queued', label: 'Queued' },
    { key: 'starting_generation', label: 'Starting generation' },
    { key: 'building_responses_payload', label: 'Building Responses payload' },
    { key: 'waiting_for_api', label: 'Waiting for API response' },
    { key: 'received_api_response', label: 'Received API response' },
    { key: 'parsing_json_response', label: 'Parsing JSON response' },
    { key: 'extracting_response_image_output', label: 'Extracting image_generation output' },
    { key: 'decoding_b64_json', label: 'Decoding b64_json' },
    { key: 'downloading_image_url', label: 'Downloading image URL' },
    { key: 'extracting_image_bytes', label: 'Extracting image bytes' },
    { key: 'validating_image_bytes', label: 'Validating image bytes' },
    { key: 'saving_images', label: 'Saving images' },
    { key: 'finalizing_preview', label: 'Finalizing preview' },
    { key: 'completed', label: 'Completed' },
  ],
  edit: [
    { key: 'queued', label: 'Queued' },
    { key: 'starting_edit', label: 'Starting edit' },
    { key: 'building_edit_form', label: 'Building multipart edit request' },
    { key: 'uploading_edit_image', label: 'Uploading source image' },
    { key: 'received_api_response', label: 'Received API response' },
    { key: 'parsing_json_response', label: 'Parsing JSON response' },
    { key: 'extracting_edit_data', label: 'Extracting edited image data' },
    { key: 'decoding_b64_json', label: 'Decoding b64_json' },
    { key: 'downloading_image_url', label: 'Downloading image URL' },
    { key: 'extracting_image_bytes', label: 'Extracting image bytes' },
    { key: 'validating_image_bytes', label: 'Validating image bytes' },
    { key: 'saving_images', label: 'Saving edited images' },
    { key: 'finalizing_preview', label: 'Finalizing preview' },
    { key: 'completed', label: 'Completed' },
  ],
};

let currentImageUrl = null;
let currentFilename = null;
let lastRequestBody = null;
let lastAction = 'generate';
let selectedEditImage = null;
let selectedGalleryImageId = null;
let generationStartedAt = null;
let activeGenerateJobId = null;
let latestJobId = null;
const cancelledGenerateJobIds = new Set();

export function markGenerateJobCancelled(jobId) {
  if (!jobId) return;
  cancelledGenerateJobIds.add(jobId);
  if (jobId === activeGenerateJobId) {
    updatePreviewStage('cancelled', lastAction, 'Job cancelled');
  }
}

export function openEditImagePicker() {
  document.getElementById('editImageInput').click();
}

export function prepareGalleryImageForEdit(imageId, filename = '') {
  const normalizedId = String(imageId || '').trim();
  if (!normalizedId) return;

  selectedEditImage = null;
  selectedGalleryImageId = normalizedId;

  const editInput = document.getElementById('editImageInput');
  const editBtn = document.getElementById('editBtn');
  const imageName = document.getElementById('editImageName');
  if (editInput) editInput.value = '';

  const displayName = String(filename || normalizedId);
  imageName.textContent = `Gallery: ${displayName}`;
  imageName.title = `Gallery: ${displayName}`;
  imageName.classList.remove('hidden');
  editBtn.disabled = false;

  hideError();
  showToast('Gallery image ready for edits', 'success');
}

export function handleEditImageSelected(event) {
  const file = event.target.files?.[0] || null;
  const editBtn = document.getElementById('editBtn');
  const imageName = document.getElementById('editImageName');

  if (!file) {
    selectedEditImage = null;
    selectedGalleryImageId = null;
    editBtn.disabled = true;
    imageName.classList.add('hidden');
    imageName.textContent = '';
    return;
  }

  if (!isImageFile(file)) {
    selectedEditImage = null;
    selectedGalleryImageId = null;
    editBtn.disabled = true;
    imageName.classList.add('hidden');
    imageName.textContent = '';
    event.target.value = '';
    showError('Please upload an image file');
    return;
  }

  selectedEditImage = file;
  selectedGalleryImageId = null;
  editBtn.disabled = false;
  imageName.textContent = file.name;
  imageName.title = file.name;
  imageName.classList.remove('hidden');
  hideError();
  showToast('Image ready for edits', 'success');
}

export async function generateImage() {
  hideError();
  const requestBody = getImageRequestBody();
  const prompt = requestBody.prompt;
  if (!prompt) {
    showError('Please enter a prompt');
    return;
  }

  lastRequestBody = requestBody;
  lastAction = 'generate';

  generationStartedAt = performance.now();
  showPreviewLoading('generate', prompt);

  try {
    const job = await apiFetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    }, 'starting image generation');
    const jobId = job.job_id || null;
    activeGenerateJobId = jobId;
    latestJobId = jobId;
    pollAndShow(jobId, 'generate', prompt);
  } catch (e) {
    handleJobError(e, 'Failed to generate image');
  }
}

export async function editImage() {
  hideError();
  if (!selectedEditImage && !selectedGalleryImageId) {
    showError('Please upload an image or choose one from gallery first');
    return;
  }

  const requestBody = getImageRequestBody();
  const prompt = requestBody.prompt;
  if (!prompt) {
    showError('Please enter a prompt');
    return;
  }

  const formData = new FormData();
  Object.entries(requestBody).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      formData.append(key, String(value));
    }
  });
  let endpoint = '/api/edits';
  if (selectedEditImage) {
    formData.append('image', selectedEditImage, selectedEditImage.name);
  } else {
    endpoint = '/api/edits/from-gallery/' + encodeURIComponent(selectedGalleryImageId);
  }

  lastRequestBody = requestBody;
  lastAction = 'edit';
  generationStartedAt = performance.now();
  showPreviewLoading('edit', prompt);

  try {
    const job = await apiFetch(endpoint, {
      method: 'POST',
      body: formData,
    }, 'starting image edit');
    const jobId = job.job_id || null;
    activeGenerateJobId = jobId;
    latestJobId = jobId;
    pollAndShow(jobId, 'edit', prompt);
  } catch (e) {
    handleJobError(e, 'Failed to edit image');
  }
}

export function regenerate() {
  if (!lastRequestBody) return;
  if (lastAction === 'edit' && (selectedEditImage || selectedGalleryImageId)) {
    editImage();
  } else {
    generateImage();
  }
}

export function downloadCurrent() {
  if (!currentFilename) return;
  const a = document.createElement('a');
  a.href = '/api/download/' + currentFilename;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

export function clearCurrentImage() {
  currentImageUrl = null;
  currentFilename = null;
  setActiveLightboxImage(null);
  document.getElementById('previewSection').classList.add('hidden');
}

function getPreviewStages(mode) {
  if (mode === 'edit') return PREVIEW_STAGES.edit;
  return isResponsesApiSelected() ? PREVIEW_STAGES.generationResponses : PREVIEW_STAGES.generationImages;
}

function getStageLabel(mode, stage) {
  const primaryMatch = getPreviewStages(mode).find(item => item.key === stage);
  if (primaryMatch) return primaryMatch.label;

  for (const stages of Object.values(PREVIEW_STAGES)) {
    const fallbackMatch = stages.find(item => item.key === stage);
    if (fallbackMatch) return fallbackMatch.label;
  }

  return '';
}

function formatStageMessage(stage, mode, fallback = '') {
  if (fallback) return fallback;
  return getStageLabel(mode, stage) || (mode === 'edit' ? 'Editing image' : 'Generating image');
}

function updatePreviewStage(stage = 'queued', mode = 'generate', message = '') {
  const operation = mode === 'edit' ? 'edit' : 'generation';
  const currentStageLabel = formatStageMessage(stage, operation, message);
  const previewLoadingText = document.getElementById('previewLoadingText');
  const previewCurrentStage = document.getElementById('previewCurrentStage');
  previewLoadingText.textContent = operation === 'edit' ? 'Editing...' : 'Generating...';
  previewCurrentStage.textContent = currentStageLabel;
  previewCurrentStage.title = currentStageLabel;
}

function showPreviewLoading(mode, prompt) {
  const preview = document.getElementById('previewSection');
  const previewImageWrapper = document.getElementById('previewImageWrapper');
  const previewLoading = document.getElementById('previewLoading');
  const previewImg = document.getElementById('previewImage');

  document.getElementById('previewPrompt').textContent = prompt;
  updatePreviewTime('Elapsed');
  preview.classList.remove('hidden');
  previewImageWrapper.classList.add('preview-loading-active');
  previewLoading.classList.remove('hidden');
  updatePreviewStage('queued', mode);
  previewImg.removeAttribute('src');
  previewImg.classList.add('hidden');
  previewImg.style.opacity = '0';
}

async function pollAndShow(jobId, mode, prompt) {
  try {
    const data = await pollGenerateJob(jobId, mode);
    if (latestJobId === jobId) {
      await showGeneratedImage(data);
    } else {
      try { await loadGallery(1, { throwOnError: true }); } catch (_) {}
    }
  } catch (e) {
    if (latestJobId === jobId) {
      updatePreviewTime('Elapsed');
      document.getElementById('previewImageWrapper').classList.remove('preview-loading-active');
      document.getElementById('previewLoading').classList.add('hidden');
    }
    handleJobError(e, mode === 'edit' ? 'Failed to edit image' : 'Failed to generate image');
  } finally {
    if (activeGenerateJobId === jobId) {
      activeGenerateJobId = null;
    }
  }
}

async function pollGenerateJob(jobId, mode = 'generate') {
  const failedText = mode === 'edit' ? 'Image edit failed' : 'Image generation failed';
  const startedAt = Date.now();
  const timeoutMs = 10 * 60 * 1000;
  let networkFailures = 0;
  let lastNetworkError = null;

  while (Date.now() - startedAt < timeoutMs) {
    await sleep(2000);
    if (cancelledGenerateJobIds.has(jobId)) {
      cancelledGenerateJobIds.delete(jobId);
      throw new Error('Generation job cancelled');
    }

    let data;
    try {
      data = await apiFetch(
        '/api/generate/' + encodeURIComponent(jobId),
        {},
        'checking generation status',
      );
      networkFailures = 0;
      lastNetworkError = null;
    } catch (error) {
      if (!isNetworkFetchError(error)) throw error;

      networkFailures += 1;
      lastNetworkError = error;
      if (latestJobId === jobId) {
        document.getElementById('previewLoadingText').textContent = 'Reconnecting...';
      }
      if (networkFailures >= 8) {
        throw new Error(`${error.message}. The image may still finish; refresh the gallery to check.`);
      }
      continue;
    }

    if (cancelledGenerateJobIds.has(jobId)) {
      cancelledGenerateJobIds.delete(jobId);
      throw new Error('Generation job cancelled');
    }

    if (data.status === 'success') return data;
    if (data.status === 'error') {
      throw new Error(data.message || failedText);
    }

    if (latestJobId === jobId) {
      updatePreviewStage(data.stage || data.status, data.operation || mode, data.message);
    }
  }

  if (lastNetworkError) {
    throw new Error(`${lastNetworkError.message}. The image may still finish; refresh the gallery to check.`);
  }
  throw new Error('Image generation is still running. Check the gallery in a bit, or try again.');
}

function handleJobError(error, fallbackMessage) {
  const message = error?.message || fallbackMessage;
  if (/Generation job (cancelled|not found)/i.test(message)) {
    if (activeGenerateJobId) {
      cancelledGenerateJobIds.delete(activeGenerateJobId);
    }
    return;
  }

  showError(message);
}

async function showGeneratedImage(data) {
  currentImageUrl = data.image_url;
  currentFilename = data.image_url.replace('/api/image/', '');
  setActiveLightboxImage(normalizeGalleryImage({
    ...data,
    filename: currentFilename,
  }));

  const previewImg = document.getElementById('previewImage');
  const previewLoading = document.getElementById('previewLoading');
  const previewImageWrapper = document.getElementById('previewImageWrapper');
  const imageUrl = data.image_url + '?t=' + Date.now();

  document.getElementById('previewPrompt').textContent = data.prompt;
  updatePreviewTime('Elapsed');
  document.getElementById('previewSection').classList.remove('hidden');

  await new Promise((resolve, reject) => {
    previewImg.onload = resolve;
    previewImg.onerror = () => reject(new Error('Generated image could not be loaded'));
    previewImg.src = imageUrl;
  });

  previewImg.onload = null;
  previewImg.onerror = null;
  previewImg.alt = data.prompt || 'Generated image';
  previewImg.className = 'max-w-full max-h-[500px] object-contain fade-in';
  previewImg.style.opacity = '1';
  previewImageWrapper.classList.remove('preview-loading-active');
  previewLoading.classList.add('hidden');

  try {
    await loadGallery(1, { throwOnError: true });
  } catch (error) {
    console.error('Generated image loaded, but gallery refresh failed:', error);
    showToast('Image generated, but gallery refresh failed', 'error');
  }
}

function getImageRequestBody() {
  if (isResponsesApiSelected()) {
    return {
      prompt: document.getElementById('promptInput').value.trim(),
      model: document.getElementById('modelSelect').value,
    };
  }

  const outputFormat = document.getElementById('formatSelect').value;
  const responseFormat = document.getElementById('responseFormatSelect').value;
  const requestBody = {
    prompt: document.getElementById('promptInput').value.trim(),
    size: document.getElementById('sizeSelect').value,
    model: document.getElementById('modelSelect').value,
    n: clampQuantityInput() || 1,
    quality: document.getElementById('qualitySelect').value,
    output_format: outputFormat,
  };

  if (responseFormat) {
    requestBody.response_format = responseFormat;
  }

  if (outputFormat !== 'png') {
    requestBody.output_compression = clampCompressionInput() ?? 100;
  }

  return requestBody;
}

function isImageFile(file) {
  if (file.type && file.type.startsWith('image/')) return true;
  return /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|svg|tiff?|webp)$/i.test(file.name || '');
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function formatElapsedTime(startedAt = generationStartedAt) {
  if (typeof startedAt !== 'number') return '0.00s';
  return `${((performance.now() - startedAt) / 1000).toFixed(2)}s`;
}

function updatePreviewTime(label, startedAt = generationStartedAt) {
  const previewTime = document.getElementById('previewTime');
  previewTime.textContent = `${label}: ${formatElapsedTime(startedAt)}`;
  previewTime.classList.remove('hidden');
}
