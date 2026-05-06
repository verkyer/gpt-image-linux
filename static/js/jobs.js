import { apiFetch, isNetworkFetchError } from './api.js';
import { loadGallery, normalizeGalleryImage, setActiveLightboxImage } from './gallery.js';
import { isResponsesApiSelected, refreshParameterControls } from './settings.js';
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
let generationStartedAt = null;

export function openEditImagePicker() {
  document.getElementById('editImageInput').click();
}

export function handleEditImageSelected(event) {
  const file = event.target.files?.[0] || null;
  const editBtn = document.getElementById('editBtn');
  const imageName = document.getElementById('editImageName');

  if (!file) {
    selectedEditImage = null;
    editBtn.disabled = true;
    imageName.classList.add('hidden');
    imageName.textContent = '';
    return;
  }

  if (!isImageFile(file)) {
    selectedEditImage = null;
    editBtn.disabled = true;
    imageName.classList.add('hidden');
    imageName.textContent = '';
    event.target.value = '';
    showError('Please upload an image file');
    return;
  }

  selectedEditImage = file;
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
  setLoading(true, 'generate');
  document.getElementById('previewPrompt').textContent = prompt;
  updatePreviewTime('Elapsed');
  try {
    const job = await apiFetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    }, 'starting image generation');
    const data = job.image_url ? job : await pollGenerateJob(job.job_id, 'generate');
    await showGeneratedImage(data);
    setLoading(false);
  } catch (e) {
    setLoading(false);
    updatePreviewTime('Elapsed');
    showError(e.message || 'Failed to generate image');
  }
}

export async function editImage() {
  hideError();
  if (!selectedEditImage) {
    showError('Please upload an image first');
    return;
  }

  const requestBody = getImageRequestBody();
  const prompt = requestBody.prompt;
  if (!prompt) {
    showError('Please enter a prompt');
    return;
  }

  const formData = new FormData();
  formData.append('image', selectedEditImage, selectedEditImage.name);
  Object.entries(requestBody).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      formData.append(key, String(value));
    }
  });

  lastRequestBody = requestBody;
  lastAction = 'edit';
  generationStartedAt = performance.now();
  setLoading(true, 'edit');
  document.getElementById('previewPrompt').textContent = prompt;
  updatePreviewTime('Elapsed');

  try {
    const job = await apiFetch('/api/edits', {
      method: 'POST',
      body: formData,
    }, 'starting image edit');
    const data = job.image_url ? job : await pollGenerateJob(job.job_id, 'edit');
    await showGeneratedImage(data);
    setLoading(false);
  } catch (e) {
    setLoading(false);
    updatePreviewTime('Elapsed');
    showError(e.message || 'Failed to edit image');
  }
}

export function regenerate() {
  if (!lastRequestBody) return;
  if (lastAction === 'edit' && selectedEditImage) {
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

function setLoading(loading, mode = 'generate') {
  const btn = document.getElementById('generateBtn');
  const uploadBtn = document.getElementById('uploadBtn');
  const editBtn = document.getElementById('editBtn');
  const preview = document.getElementById('previewSection');
  const previewImageWrapper = document.getElementById('previewImageWrapper');
  const previewLoading = document.getElementById('previewLoading');
  const previewImg = document.getElementById('previewImage');
  const isEditing = mode === 'edit';
  const generateSpinnerHTML = '<span class="spinner"></span> Generating...';
  const editSpinnerHTML = '<span class="spinner"></span> Editing...';

  btn.disabled = loading;
  btn.innerHTML = loading && !isEditing ? generateSpinnerHTML : '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg> Generate';
  uploadBtn.disabled = loading;
  editBtn.disabled = loading || !selectedEditImage;
  editBtn.innerHTML = loading && isEditing ? editSpinnerHTML : '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L12 14l-4 1 1-4 8.586-8.586z"/></svg> Edits';
  document.getElementById('promptInput').disabled = loading;
  refreshParameterControls(loading);

  if (loading) {
    preview.classList.remove('hidden');
    previewImageWrapper.classList.add('preview-loading-active');
    previewLoading.classList.remove('hidden');
    updatePreviewStage('queued', mode);
    previewImg.removeAttribute('src');
    previewImg.classList.add('hidden');
    previewImg.style.opacity = '0';
  } else {
    previewImageWrapper.classList.remove('preview-loading-active');
    previewLoading.classList.add('hidden');
  }
}

async function pollGenerateJob(jobId, mode = 'generate') {
  const loadingText = document.getElementById('previewLoadingText');
  const failedText = mode === 'edit' ? 'Image edit failed' : 'Image generation failed';
  const startedAt = Date.now();
  const timeoutMs = 10 * 60 * 1000;
  let networkFailures = 0;
  let lastNetworkError = null;

  while (Date.now() - startedAt < timeoutMs) {
    await sleep(2000);
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
      loadingText.textContent = 'Reconnecting...';
      if (networkFailures >= 8) {
        throw new Error(`${error.message}. The image may still finish; refresh the gallery to check.`);
      }
      continue;
    }

    if (data.status === 'success') return data;
    if (data.status === 'error') {
      throw new Error(data.message || failedText);
    }

    updatePreviewStage(data.stage || data.status, data.operation || mode, data.message);
  }

  if (lastNetworkError) {
    throw new Error(`${lastNetworkError.message}. The image may still finish; refresh the gallery to check.`);
  }
  throw new Error('Image generation is still running. Check the gallery in a bit, or try again.');
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
