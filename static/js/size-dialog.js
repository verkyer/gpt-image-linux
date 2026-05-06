import { unlockBodyOverflowIfIdle } from './ui.js';

const SIZE_LIMITS = {
  step: 16,
  maxSide: 3840,
  maxAspect: 3,
  minPixels: 655360,
  maxPixels: 8294400,
};
const SIZE_BASES = { '1K': 1024, '2K': 2048, '4K': 3840 };
const SIZE_RATIOS = {
  '1:1': [1, 1],
  '4:3': [4, 3],
  '3:4': [3, 4],
  '16:9': [16, 9],
  '9:16': [9, 16],
  '21:9': [21, 9],
};

let sizeState = {
  mode: 'ratio',
  base: '1K',
  ratio: '1:1',
  customWidth: '1024',
  customHeight: '1024',
};
let stagedSizeState = cloneSizeState(sizeState);

export function renderSizeButton() {
  const value = getSizeValue(sizeState);
  const button = document.getElementById('sizeSelect');
  button.value = value;
  document.getElementById('sizeSelectLabel').textContent = formatSizeValue(value);
  document.getElementById('sizeSelectMeta').textContent = getSizeMeta(sizeState);
}

export function openSizeDialog() {
  stagedSizeState = cloneSizeState(sizeState);
  renderSizeDialog(true);
  document.getElementById('sizeDialog').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

export function closeSizeDialog() {
  document.getElementById('sizeDialog').classList.add('hidden');
  unlockBodyOverflowIfIdle();
}

export function selectSizeMode(mode) {
  stagedSizeState.mode = mode;
  renderSizeDialog(mode === 'custom');
  if (mode === 'custom') {
    setTimeout(() => document.getElementById('customWidthInput').focus(), 0);
  }
}

export function selectSizeBase(base) {
  stagedSizeState.mode = 'ratio';
  stagedSizeState.base = base;
  renderSizeDialog();
}

export function selectSizeRatio(ratio) {
  stagedSizeState.mode = 'ratio';
  stagedSizeState.ratio = ratio;
  renderSizeDialog();
}

export function handleCustomSizeInput(field, value) {
  const cleanValue = String(value).replace(/[^\d]/g, '');
  if (field === 'width') {
    stagedSizeState.customWidth = cleanValue;
    if (value !== cleanValue) document.getElementById('customWidthInput').value = cleanValue;
  } else {
    stagedSizeState.customHeight = cleanValue;
    if (value !== cleanValue) document.getElementById('customHeightInput').value = cleanValue;
  }
  stagedSizeState.mode = 'custom';
  updateActiveSizeControls();
  updateSizeResult();
}

export function applySizeDialog() {
  if (stagedSizeState.mode === 'custom') {
    const resolution = getCustomResolution(stagedSizeState);
    stagedSizeState.customWidth = String(resolution.width);
    stagedSizeState.customHeight = String(resolution.height);
  }
  sizeState = cloneSizeState(stagedSizeState);
  renderSizeButton();
  closeSizeDialog();
}

function cloneSizeState(state) {
  return { ...state };
}

function formatSizeValue(value) {
  return value === 'auto' ? 'auto' : value.replace('x', ' x ');
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value).replace(/[^\d]/g, ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function roundToStep(value, mode = 'nearest') {
  const scaled = value / SIZE_LIMITS.step;
  const rounded = mode === 'up' ? Math.ceil(scaled) : mode === 'down' ? Math.floor(scaled) : Math.round(scaled);
  return Math.max(SIZE_LIMITS.step, rounded * SIZE_LIMITS.step);
}

function fitPixels(width, height, mode) {
  const pixelLimit = mode === 'min' ? SIZE_LIMITS.minPixels : SIZE_LIMITS.maxPixels;
  const scale = Math.sqrt(pixelLimit / (width * height));
  const roundMode = mode === 'min' ? 'up' : 'down';
  let nextWidth = Math.min(SIZE_LIMITS.maxSide, roundToStep(width * scale, roundMode));
  let nextHeight = Math.min(SIZE_LIMITS.maxSide, roundToStep(height * scale, roundMode));

  if (mode === 'min') {
    while (nextWidth * nextHeight < SIZE_LIMITS.minPixels) {
      if (nextWidth <= nextHeight && nextWidth < SIZE_LIMITS.maxSide) {
        nextWidth += SIZE_LIMITS.step;
      } else if (nextHeight < SIZE_LIMITS.maxSide) {
        nextHeight += SIZE_LIMITS.step;
      } else {
        break;
      }
    }
  } else {
    while (nextWidth * nextHeight > SIZE_LIMITS.maxPixels) {
      if (nextWidth >= nextHeight && nextWidth > SIZE_LIMITS.step) {
        nextWidth -= SIZE_LIMITS.step;
      } else if (nextHeight > SIZE_LIMITS.step) {
        nextHeight -= SIZE_LIMITS.step;
      } else {
        break;
      }
    }
  }

  return [nextWidth, nextHeight];
}

function normalizeDimensions(inputWidth, inputHeight) {
  let width = Math.min(roundToStep(parsePositiveInt(inputWidth, 1024)), SIZE_LIMITS.maxSide);
  let height = Math.min(roundToStep(parsePositiveInt(inputHeight, 1024)), SIZE_LIMITS.maxSide);

  if (width / height > SIZE_LIMITS.maxAspect) {
    height = roundToStep(width / SIZE_LIMITS.maxAspect, 'up');
  } else if (height / width > SIZE_LIMITS.maxAspect) {
    width = roundToStep(height / SIZE_LIMITS.maxAspect, 'up');
  }

  if (width * height < SIZE_LIMITS.minPixels) {
    [width, height] = fitPixels(width, height, 'min');
  }

  if (width * height > SIZE_LIMITS.maxPixels) {
    [width, height] = fitPixels(width, height, 'max');
  }

  if (width / height > SIZE_LIMITS.maxAspect) {
    height = roundToStep(width / SIZE_LIMITS.maxAspect, 'up');
  } else if (height / width > SIZE_LIMITS.maxAspect) {
    width = roundToStep(height / SIZE_LIMITS.maxAspect, 'up');
  }

  if (width * height > SIZE_LIMITS.maxPixels) {
    [width, height] = fitPixels(width, height, 'max');
  }

  return { width, height };
}

function getRatioResolution(state) {
  const baseSide = SIZE_BASES[state.base] || SIZE_BASES['1K'];
  const [ratioWidth, ratioHeight] = SIZE_RATIOS[state.ratio] || SIZE_RATIOS['1:1'];
  const isLandscape = ratioWidth >= ratioHeight;
  const width = isLandscape ? baseSide : Math.round(baseSide * ratioWidth / ratioHeight);
  const height = isLandscape ? Math.round(baseSide * ratioHeight / ratioWidth) : baseSide;
  return normalizeDimensions(width, height);
}

function getCustomResolution(state) {
  return normalizeDimensions(state.customWidth, state.customHeight);
}

function getSizeValue(state) {
  if (state.mode === 'auto') return 'auto';
  const resolution = state.mode === 'custom' ? getCustomResolution(state) : getRatioResolution(state);
  return `${resolution.width}x${resolution.height}`;
}

function getSizeMeta(state) {
  if (state.mode === 'auto') return 'Auto';
  if (state.mode === 'custom') return 'Custom';
  return `Ratio \u00b7 ${state.base} \u00b7 ${state.ratio}`;
}

function updateActiveSizeControls() {
  document.querySelectorAll('[data-size-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.sizeTab === stagedSizeState.mode);
  });
  document.querySelectorAll('[data-size-base]').forEach(button => {
    button.classList.toggle('active', button.dataset.sizeBase === stagedSizeState.base);
  });
  document.querySelectorAll('[data-size-ratio]').forEach(button => {
    button.classList.toggle('active', button.dataset.sizeRatio === stagedSizeState.ratio);
  });
}

function updateSizeResult() {
  const value = getSizeValue(stagedSizeState);
  document.getElementById('sizeResultValue').textContent = value;
  document.getElementById('sizeResultMeta').textContent = getSizeMeta(stagedSizeState);
}

function renderSizeDialog(syncCustomInputs = false) {
  document.getElementById('sizeCurrentValue').textContent = formatSizeValue(getSizeValue(sizeState));
  document.getElementById('sizeAutoPanel').classList.toggle('hidden', stagedSizeState.mode !== 'auto');
  document.getElementById('sizeRatioPanel').classList.toggle('hidden', stagedSizeState.mode !== 'ratio');
  document.getElementById('sizeCustomPanel').classList.toggle('hidden', stagedSizeState.mode !== 'custom');
  if (syncCustomInputs) {
    document.getElementById('customWidthInput').value = stagedSizeState.customWidth;
    document.getElementById('customHeightInput').value = stagedSizeState.customHeight;
  }
  updateActiveSizeControls();
  updateSizeResult();
}
