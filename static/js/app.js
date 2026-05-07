import { setUnauthorizedHandler } from './api.js';
import { checkAccess, configureAccess, showAccessGate, unlockAccess } from './access.js';
import {
  activatePreset,
  createPreset,
  deleteActivePreset,
  handleOutputFormatChange,
  loadSettings,
  refreshParameterControls,
  saveSettings,
  toggleSettings,
} from './settings.js';
import {
  applySizeDialog,
  closeSizeDialog,
  handleCustomSizeInput,
  openSizeDialog,
  renderSizeButton,
  selectSizeBase,
  selectSizeMode,
  selectSizeRatio,
} from './size-dialog.js';
import {
  changeGalleryPage,
  clearGalleryState,
  closeLightbox,
  copyImageUrl,
  copyLightboxImageUrl,
  copyLightboxPrompt,
  copyPrompt,
  deleteAllImages as deleteAllGalleryImages,
  deleteImage,
  downloadAll,
  loadGallery,
  openLightbox,
} from './gallery.js';
import {
  clearCurrentImage,
  downloadCurrent,
  editImage,
  generateImage,
  handleEditImageSelected,
  openEditImagePicker,
  regenerate,
} from './jobs.js';
import {
  deleteSelectedGenerateJobs,
  refreshJobHistory,
  startJobHistoryPolling,
  toggleGenerateJobSelection,
  toggleJobHistory,
} from './job-history.js';
import {
  clampCompressionInput,
  clampQuantityInput,
  hideError,
  updatePromptLen,
} from './ui.js';

function exposeGlobals() {
  Object.assign(window, {
    activatePreset,
    applySizeDialog,
    changeGalleryPage,
    closeLightbox,
    closeSizeDialog,
    copyImageUrl,
    copyLightboxImageUrl,
    copyLightboxPrompt,
    copyPrompt,
    createPreset,
    deleteActivePreset,
    deleteAllImages,
    deleteImage,
    deleteSelectedGenerateJobs,
    downloadAll,
    downloadCurrent,
    editImage,
    generateImage,
    handleCustomSizeInput,
    handleEditImageSelected,
    handleOutputFormatChange,
    hideError,
    openEditImagePicker,
    openLightbox,
    openSizeDialog,
    refreshJobHistory,
    regenerate,
    saveSettings,
    selectSizeBase,
    selectSizeMode,
    selectSizeRatio,
    startJobHistoryPolling,
    toggleGenerateJobSelection,
    toggleJobHistory,
    toggleSettings,
    unlockAccess,
  });
}

async function init() {
  exposeGlobals();
  setUnauthorizedHandler(showAccessGate);
  configureAccess({
    onAuthenticated: async () => {
      await loadGallery();
      await loadSettings();
      startJobHistoryPolling();
    },
  });

  document.getElementById('promptInput').addEventListener('input', updatePromptLen);
  document.getElementById('compressionInput').addEventListener('input', clampCompressionInput);
  document.getElementById('quantityInput').addEventListener('input', clampQuantityInput);
  refreshParameterControls();
  renderSizeButton();
  await checkAccess();
}

async function deleteAllImages() {
  await deleteAllGalleryImages({
    onDeleted: async () => {
      clearCurrentImage();
      clearGalleryState();
    },
  });
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (!document.getElementById('lightbox').classList.contains('hidden')) {
      closeLightbox();
    } else if (!document.getElementById('sizeDialog').classList.contains('hidden')) {
      closeSizeDialog();
    } else if (!document.getElementById('jobHistoryDrawer').classList.contains('hidden')) {
      toggleJobHistory();
    } else if (!document.getElementById('settingsDrawer').classList.contains('hidden')) {
      toggleSettings();
    }
  }
});

init();
