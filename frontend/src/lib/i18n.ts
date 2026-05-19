import { browser } from '$app/environment';
import { derived, get, writable } from 'svelte/store';

export type Language = 'en' | 'zh-CN';

const STORAGE_KEY = 'gpt-image-panel-language';

const en = {
  common: {
    active: 'Active',
    apply: 'Apply',
    clear: 'Clear',
    close: 'Close',
    completedAt: 'Completed',
    copyPrompt: 'Copy prompt',
    copyUrl: 'Copy URL',
    delete: 'Delete',
    download: 'Download',
    duration: 'Duration',
    edit: 'Edit',
    favorite: 'Favorite',
    model: 'Model',
    noApiUrl: 'No API URL',
    noKey: 'No key',
    preset: 'Preset',
    prompt: 'Prompt',
    settings: 'Settings',
    size: 'Size',
    status: 'Status',
    undo: 'Undo',
    switch: 'Switch',
    unfavorite: 'Unfavorite',
    untitledJob: 'Untitled job',
    untitledPreset: 'Untitled preset'
  },
  language: {
    button: '中文',
    current: 'English',
    toggleTitle: 'Switch to Simplified Chinese'
  },
  header: {
    subtitle: 'Image Generation Interface',
    jobs: 'Jobs',
    jobHistory: 'Job History',
    settingsShort: 'Set',
    newVersion: 'New',
    versionTitle: (version: string) => `Current ${version}`,
    versionUpdateTitle: (version: string, latestVersion: string) => `Current ${version}. Latest v${latestVersion}.`
  },
  access: {
    title: 'Access Key',
    placeholder: 'Enter access key',
    unlock: 'Unlock',
    unlocking: 'Unlocking...',
    required: 'Please enter the access key'
  },
  settings: {
    title: 'Settings',
    subtitle: 'API presets, default model, upstream path, and SOCKS5 proxy',
    closeLabel: 'Close settings',
    presets: 'Presets',
    newPreset: 'New',
    deletePreset: 'Delete',
    presetName: 'Preset name',
    apiUrl: 'API URL',
    apiPath: 'API path',
    defaultModel: 'Default model',
    apiKey: 'API key',
    apiKeyHint: 'Use ${OPENAI_API_KEY} to store only an environment-variable reference in SQLite.',
    upstreamSocks5Proxy: 'SOCKS5 proxy',
    upstreamSocks5ProxyHint: 'Optional. Use socks5://host:port or socks5://user:pass@host:port. Only generation/edit upstream API calls use it.',
    envRef: 'env ref',
    healthCheck: 'Health check',
    healthChecking: 'Checking...',
    healthStatus: 'Preset health',
    healthOk: 'OK',
    healthWarning: 'Warning',
    healthError: 'Error',
    savePreset: 'Save Preset',
    saving: 'Saving...'
  },
  jobs: {
    title: 'Job History',
    subtitle: 'Queued, running, and recent finished jobs',
    closeLabel: 'Close jobs',
    runningTab: 'Running',
    historyTab: 'History',
    selectAll: 'Select All',
    refresh: 'Refresh',
    noRunning: 'No running jobs',
    noRunningHint: 'Queued and running jobs will show here.',
    noHistory: 'No job history',
    noHistoryHint: 'Finished generation and edit jobs will show here.',
    historyLoading: 'Loading history...',
    useAsPrompt: 'Use as prompt',
    retry: 'Retry',
    retryUnavailable: 'Running jobs cannot be retried yet.',
    cancelSelected: 'Cancel Selected'
  },
  promptForm: {
    title: 'Prompt',
    subtitle: 'Generation and edit requests use the same frozen API contract.',
    responsesMode: 'Responses mode',
    chatCompletionsMode: 'Chat Completions mode',
    placeholder: 'Describe the image you want to create...',
    disabledForResponses: 'Disabled for Responses',
    disabledForChatCompletions: 'Disabled for Chat Completions',
    disabledForPng: 'Disabled for PNG',
    quality: 'Quality',
    quantity: 'Quantity',
    format: 'Format',
    compression: 'Compression',
    responseFormat: 'Response format',
    defaultResponseFormat: 'none (omit)',
    webhookUrl: 'Webhook URL',
    uploadEditImage: 'Upload edit image',
    clearEditSources: 'Clear edit sources',
    previewEditLabel: (label: string) => `Preview ${label}`,
    uploadSourceBadge: 'Upload',
    gallerySourceBadge: 'Gallery',
    edits: 'Edits',
    generate: 'Generate',
    editSourcePreview: 'Edit Source Preview',
    closeEditPreview: 'Close edit image preview'
  },
  preview: {
    title: 'Preview',
    subtitle: 'Latest generation or edit result',
    regenerate: 'Regenerate',
    working: 'Working on image',
    queued: 'Queued',
    generatedAlt: 'Generated preview',
    noPreview: 'No preview yet',
    noPreviewHint: 'Generate or edit an image to show the result.'
  },
  gallery: {
    title: 'Gallery',
    imageCount: (count: number) => `${count} image${count === 1 ? '' : 's'}`,
    noImages: 'No images',
    showSize: 'Show size',
    import: 'Import',
    exportZip: 'Export ZIP',
    select: 'Select',
    cancelSelection: 'Cancel selection',
    selectAllPage: 'Select page',
    clearSelection: 'Clear',
    selectedCount: (count: number) => `${count} selected`,
    downloadSelected: 'Download selected',
    favoriteSelected: 'Favorite selected',
    unfavoriteSelected: 'Unfavorite selected',
    deleteSelected: 'Delete selected',
    deleteAll: 'Delete All',
    filterPrompt: 'Filter prompt',
    allModels: 'All models',
    allPresets: 'All presets',
    allSizes: 'All sizes',
    favorites: 'Favorites',
    dateFrom: 'From date',
    dateTo: 'To date',
    resetFilters: 'Reset filters',
    loading: 'Loading gallery...',
    noMatch: 'No images match',
    empty: 'Your gallery is empty',
    noMatchHint: 'Adjust or reset the gallery filters.',
    emptyHint: 'Describe an image and hit Generate.',
    previous: 'Previous',
    next: 'Next',
    page: (page: number, totalPages: number) => `Page ${page} / ${totalPages}`
  },
  lightbox: {
    title: 'Image Details',
    closeLabel: 'Close lightbox'
  },
  confirm: {
    closeLabel: 'Close confirmation',
    cancel: 'Cancel',
    deleteImageTitle: 'Delete image?',
    deleteImageMessage: (filename: string) => `Delete ${filename}? The image will be hidden for 5 seconds before the server delete runs.`,
    deleteImageDetail: 'Undo is available during the 5 second window.',
    deleteSelectedTitle: (count: number) => `Delete ${count} selected image${count === 1 ? '' : 's'}?`,
    deleteSelectedMessage: (count: number) => `This removes ${count} selected gallery image${count === 1 ? '' : 's'} and any unreferenced files.`,
    deleteSelectedDetail: (count: number) => `Selected images: ${count}`,
    deleteSelectedSize: (totalBytes: string) => `Selected size: ${totalBytes}`,
    deleteAllTitle: 'Delete all gallery images?',
    deleteAllMessage: (count: number) => `This permanently removes ${count} gallery image${count === 1 ? '' : 's'} and any unreferenced files.`,
    deleteAllDetail: (totalBytes: string) => `Total size: ${totalBytes}`,
    deleteAllConfirmLabel: 'DELETE',
    deleteAllConfirmHint: 'Type DELETE to confirm',
    deletePresetTitle: 'Delete preset?',
    deletePresetMessage: (name: string) => `Delete preset "${name}"?`
  },
  sizeDialog: {
    title: 'Image Size',
    subtitle: 'Choose a preset or enter WIDTHxHEIGHT.'
  },
  messages: {
    accessCheckFailed: 'Access check failed',
    invalidAccessKey: 'Invalid access key',
    apiUrlRequired: 'Please enter an API URL',
    apiKeyRequired: 'Please enter an API Key',
    presetSaved: 'Preset saved',
    presetCreated: 'Preset created',
    presetSwitched: 'Preset switched',
    presetDeleted: 'Preset deleted',
    deletePresetConfirm: (name: string) => `Delete preset "${name}"?`,
    promptRequired: 'Please enter a prompt',
    editSourceRequired: 'Please upload an image or choose one from gallery first',
    queuedGeneration: 'Queued image generation',
    queuedEdit: 'Queued image edit',
    jobLoadFailed: 'Failed to load job',
    jobFailed: 'Job failed',
    deleteImageConfirm: 'Delete this image from gallery?',
    deleteSelectedConfirm: (count: number) => `Delete ${count} selected image${count === 1 ? '' : 's'} from gallery?`,
    imageDeleted: 'Image deleted',
    imageDeletionUndone: 'Image deletion undone',
    imageDeletionFailed: 'Failed to delete image',
    imageDeletionPending: 'Image will be deleted in 5 seconds',
    selectedImagesDeleted: (count: number) => `Deleted ${count} selected image${count === 1 ? '' : 's'}`,
    selectedImagesFavorited: (count: number) => `Updated ${count} selected image${count === 1 ? '' : 's'}`,
    deleteAllConfirm: 'This permanently deletes every gallery image stored on the server. Continue?',
    allImagesDeleted: 'All server images deleted',
    imported: (count: number) => `Imported ${count} image${count === 1 ? '' : 's'}`,
    galleryEditLabel: (filename: string) => `Gallery: ${filename}`,
    galleryImageReady: 'Gallery image ready for edits',
    galleryImageNotFound: 'Image not found',
    imageUploadRequired: 'Please upload an image file',
    promptCopied: 'Prompt copied',
    imageUrlCopied: 'Image URL copied',
    jobLoadedIntoPrompt: 'Job parameters loaded',
    editRetryNeedsSource: 'Choose an edit source before retrying this edit job',
    editSourceLimit: (max: number) => `At most ${max} edit source images are supported`,
    editSourceSomeSkipped: (max: number) => `Some selected files were skipped because the edit source limit is ${max}`,
    sessionExpired: 'Session expired. Please enter the access key.',
    failedToFetch: 'Failed to fetch',
    networkError: (message: string) => `Network error: ${message}`,
    requestFailed: 'Request failed'
  },
  operations: {
    edit: 'edit',
    generation: 'generation'
  },
  statuses: {
    queued: 'queued',
    running: 'running',
    success: 'success',
    error: 'error'
  },
  stages: {
    queued: 'Queued',
    starting_generation: 'Starting image generation',
    starting_edit: 'Starting image edit',
    building_responses_payload: 'Building Responses API payload',
    building_chat_completions_payload: 'Building Chat Completions API payload',
    building_generation_payload: 'Building image generation payload',
    building_edit_form: 'Building multipart edit request',
    waiting_for_api: 'Waiting for upstream API response',
    uploading_edit_image: 'Uploading source image and edit parameters',
    received_api_response: 'Received upstream API response',
    parsing_json_response: 'Parsing JSON response',
    extracting_response_image_output: 'Extracting image_generation_call output',
    extracting_chat_completion_image_output: 'Extracting Chat Completions image output',
    extracting_generation_data: 'Extracting image data array',
    extracting_edit_data: 'Extracting edited image data array',
    decoding_b64_json: 'Decoding b64_json image',
    downloading_image_url: 'Downloading image URL',
    extracting_image_bytes: 'Extracting image bytes',
    validating_image_bytes: 'Validating decoded image',
    saving_image_file: 'Saving image file and gallery metadata',
    saving_images: 'Saving images',
    finalizing_preview: 'Finalizing preview image',
    completed: 'Completed',
    cancelled: 'Cancelled',
    generation_failed: 'Generation failed',
    edit_failed: 'Edit failed'
  }
};

type TranslationValue = string | ((...args: any[]) => string) | Record<string, unknown>;
type TranslationSchema<T> = {
  [K in keyof T]: T[K] extends (...args: infer Args) => string
    ? (...args: Args) => string
    : T[K] extends string
      ? string
      : T[K] extends Record<string, TranslationValue>
        ? TranslationSchema<T[K]>
        : never;
};

const zh: TranslationSchema<typeof en> = {
  common: {
    active: '启用中',
    apply: '应用',
    clear: '清空',
    close: '关闭',
    completedAt: '生成时间',
    copyPrompt: '复制提示词',
    copyUrl: '复制链接',
    delete: '删除',
    download: '下载',
    duration: '耗时',
    edit: '编辑',
    favorite: '收藏',
    model: '模型',
    noApiUrl: '未配置 API URL',
    noKey: '无密钥',
    preset: '预设',
    prompt: '提示词',
    settings: '设置',
    size: '尺寸',
    status: '状态',
    undo: '撤销',
    switch: '切换',
    unfavorite: '取消收藏',
    untitledJob: '未命名任务',
    untitledPreset: '未命名预设'
  },
  language: {
    button: 'EN',
    current: '简体中文',
    toggleTitle: '切换到英文'
  },
  header: {
    subtitle: '图像生成界面',
    jobs: '任务',
    jobHistory: '任务历史',
    settingsShort: '设置',
    newVersion: '新版',
    versionTitle: (version) => `当前 ${version}`,
    versionUpdateTitle: (version, latestVersion) => `当前 ${version}，最新 v${latestVersion}。`
  },
  access: {
    title: '访问密钥',
    placeholder: '输入访问密钥',
    unlock: '解锁',
    unlocking: '解锁中...',
    required: '请输入访问密钥'
  },
  settings: {
    title: '设置',
    subtitle: 'API 预设、默认模型、上游路径和 SOCKS5 代理',
    closeLabel: '关闭设置',
    presets: '预设',
    newPreset: '新增',
    deletePreset: '删除',
    presetName: '预设名称',
    apiUrl: 'API URL',
    apiPath: 'API 路径',
    defaultModel: '默认模型',
    apiKey: 'API 密钥',
    apiKeyHint: '可填写 ${OPENAI_API_KEY}，SQLite 里只保存环境变量引用。',
    upstreamSocks5Proxy: 'SOCKS5 代理',
    upstreamSocks5ProxyHint: '可选。格式为 socks5://host:port 或 socks5://user:pass@host:port；仅生成/编辑的上游 API 请求会使用。',
    envRef: '环境变量引用',
    healthCheck: '健康检查',
    healthChecking: '检查中...',
    healthStatus: '预设健康状态',
    healthOk: '正常',
    healthWarning: '警告',
    healthError: '错误',
    savePreset: '保存预设',
    saving: '保存中...'
  },
  jobs: {
    title: '任务历史',
    subtitle: '排队中、运行中和最近完成的任务',
    closeLabel: '关闭任务历史',
    runningTab: '运行中',
    historyTab: '历史',
    selectAll: '全选',
    refresh: '刷新',
    noRunning: '没有运行中的任务',
    noRunningHint: '排队中和运行中的任务会显示在这里。',
    noHistory: '暂无任务历史',
    noHistoryHint: '已完成的生成和编辑任务会显示在这里。',
    historyLoading: '正在加载历史...',
    useAsPrompt: '复用提示词',
    retry: '重试',
    retryUnavailable: '运行中的任务暂不能重试。',
    cancelSelected: '取消所选任务'
  },
  promptForm: {
    title: '提示词',
    subtitle: '生成和编辑请求共用同一套冻结 API 契约。',
    responsesMode: 'Responses 模式',
    chatCompletionsMode: 'Chat Completions 模式',
    placeholder: '描述你想创建的图像...',
    disabledForResponses: 'Responses 模式不可用',
    disabledForChatCompletions: 'Chat Completions 模式不可用',
    disabledForPng: 'PNG 不支持压缩',
    quality: '质量',
    quantity: '数量',
    format: '格式',
    compression: '压缩率',
    responseFormat: '响应格式',
    defaultResponseFormat: 'none（省略）',
    webhookUrl: 'Webhook URL',
    uploadEditImage: '上传编辑图片',
    clearEditSources: '清空编辑源',
    previewEditLabel: (label) => `预览 ${label}`,
    uploadSourceBadge: '上传',
    gallerySourceBadge: '图库',
    edits: '编辑',
    generate: '生成',
    editSourcePreview: '编辑源预览',
    closeEditPreview: '关闭编辑图片预览'
  },
  preview: {
    title: '预览',
    subtitle: '最近一次生成或编辑结果',
    regenerate: '重新生成',
    working: '正在处理图像',
    queued: '排队中',
    generatedAlt: '生成结果预览',
    noPreview: '暂无预览',
    noPreviewHint: '生成或编辑一张图片后会在这里显示结果。'
  },
  gallery: {
    title: '图库',
    imageCount: (count) => `${count} 张图片`,
    noImages: '暂无图片',
    showSize: '显示大小',
    import: '导入',
    exportZip: '导出 ZIP',
    select: '选择',
    cancelSelection: '取消选择',
    selectAllPage: '选择本页',
    clearSelection: '清空',
    selectedCount: (count) => `已选择 ${count} 张`,
    downloadSelected: '下载所选',
    favoriteSelected: '收藏所选',
    unfavoriteSelected: '取消收藏所选',
    deleteSelected: '删除所选',
    deleteAll: '全部删除',
    filterPrompt: '筛选提示词',
    allModels: '全部模型',
    allPresets: '全部预设',
    allSizes: '全部尺寸',
    favorites: '收藏',
    dateFrom: '起始日期',
    dateTo: '截止日期',
    resetFilters: '重置筛选',
    loading: '正在加载图库...',
    noMatch: '没有匹配的图片',
    empty: '图库为空',
    noMatchHint: '调整或重置图库筛选条件。',
    emptyHint: '输入图像描述并点击生成。',
    previous: '上一页',
    next: '下一页',
    page: (page, totalPages) => `第 ${page} / ${totalPages} 页`
  },
  lightbox: {
    title: '图片详情',
    closeLabel: '关闭图片详情'
  },
  confirm: {
    closeLabel: '关闭确认',
    cancel: '取消',
    deleteImageTitle: '删除图片？',
    deleteImageMessage: (filename) => `删除 ${filename}？图片会先隐藏 5 秒，再真正发起服务器删除。`,
    deleteImageDetail: '5 秒内可以撤销。',
    deleteSelectedTitle: (count) => `删除选中的 ${count} 张图片？`,
    deleteSelectedMessage: (count) => `这会删除选中的 ${count} 张图库图片以及所有未被引用的文件。`,
    deleteSelectedDetail: (count) => `选中的图片：${count} 张`,
    deleteSelectedSize: (totalBytes) => `所选大小：${totalBytes}`,
    deleteAllTitle: '删除全部图库图片？',
    deleteAllMessage: (count) => `这会永久删除全部 ${count} 张图库图片以及所有未被引用的文件。`,
    deleteAllDetail: (totalBytes) => `总大小：${totalBytes}`,
    deleteAllConfirmLabel: 'DELETE',
    deleteAllConfirmHint: '输入 DELETE 确认',
    deletePresetTitle: '删除预设？',
    deletePresetMessage: (name) => `删除预设“${name}”？`
  },
  sizeDialog: {
    title: '图像尺寸',
    subtitle: '选择预设或输入 宽x高。'
  },
  messages: {
    accessCheckFailed: '访问状态检查失败',
    invalidAccessKey: '访问密钥无效',
    apiUrlRequired: '请输入 API URL',
    apiKeyRequired: '请输入 API 密钥',
    presetSaved: '预设已保存',
    presetCreated: '预设已创建',
    presetSwitched: '预设已切换',
    presetDeleted: '预设已删除',
    deletePresetConfirm: (name) => `删除预设“${name}”？`,
    promptRequired: '请输入提示词',
    editSourceRequired: '请先上传图片或从图库选择一张图片',
    queuedGeneration: '图像生成已排队',
    queuedEdit: '图像编辑已排队',
    jobLoadFailed: '加载任务失败',
    jobFailed: '任务失败',
    deleteImageConfirm: '从图库删除这张图片？',
    deleteSelectedConfirm: (count) => `从图库删除选中的 ${count} 张图片？`,
    imageDeleted: '图片已删除',
    imageDeletionUndone: '已撤销图片删除',
    imageDeletionFailed: '删除图片失败',
    imageDeletionPending: '图片将在 5 秒后删除',
    selectedImagesDeleted: (count) => `已删除 ${count} 张所选图片`,
    selectedImagesFavorited: (count) => `已更新 ${count} 张所选图片`,
    deleteAllConfirm: '这会永久删除服务器上存储的全部图库图片。继续？',
    allImagesDeleted: '服务器图片已全部删除',
    imported: (count) => `已导入 ${count} 张图片`,
    galleryEditLabel: (filename) => `图库：${filename}`,
    galleryImageReady: '图库图片已设为编辑源',
    galleryImageNotFound: '未找到图片',
    imageUploadRequired: '请上传图片文件',
    promptCopied: '提示词已复制',
    imageUrlCopied: '图片链接已复制',
    jobLoadedIntoPrompt: '任务参数已回填',
    editRetryNeedsSource: '重试编辑任务前，请先选择编辑源图片',
    editSourceLimit: (max) => `最多支持 ${max} 张编辑源图片`,
    editSourceSomeSkipped: (max) => `部分已选文件被跳过，因为编辑源上限是 ${max} 张`,
    sessionExpired: '会话已过期，请输入访问密钥。',
    failedToFetch: '请求失败',
    networkError: (message) => `网络错误：${message}`,
    requestFailed: '请求失败'
  },
  operations: {
    edit: '编辑',
    generation: '生成'
  },
  statuses: {
    queued: '排队中',
    running: '运行中',
    success: '成功',
    error: '错误'
  },
  stages: {
    queued: '排队中',
    starting_generation: '开始生成图像',
    starting_edit: '开始编辑图像',
    building_responses_payload: '构建 Responses API 请求',
    building_chat_completions_payload: '构建 Chat Completions API 请求',
    building_generation_payload: '构建图像生成请求',
    building_edit_form: '构建 multipart 编辑请求',
    waiting_for_api: '等待上游 API 响应',
    uploading_edit_image: '上传源图片和编辑参数',
    received_api_response: '已收到上游 API 响应',
    parsing_json_response: '解析 JSON 响应',
    extracting_response_image_output: '提取 image_generation_call 输出',
    extracting_chat_completion_image_output: '提取 Chat Completions 图像输出',
    extracting_generation_data: '提取图像数据数组',
    extracting_edit_data: '提取编辑后的图像数据数组',
    decoding_b64_json: '解码 b64_json 图像',
    downloading_image_url: '下载图像 URL',
    extracting_image_bytes: '提取图像字节',
    validating_image_bytes: '校验解码后的图像',
    saving_image_file: '保存图像文件和图库元数据',
    saving_images: '保存图像',
    finalizing_preview: '生成预览图',
    completed: '已完成',
    cancelled: '已取消',
    generation_failed: '生成失败',
    edit_failed: '编辑失败'
  }
};

export type Translation = TranslationSchema<typeof en>;

export const translations: Record<Language, Translation> = {
  en,
  'zh-CN': zh
};

function normalizeLanguage(value: string | null | undefined): Language | null {
  const normalized = String(value || '').toLowerCase();
  if (normalized.startsWith('zh')) return 'zh-CN';
  if (normalized.startsWith('en')) return 'en';
  return null;
}

function getInitialLanguage(): Language {
  if (!browser) return 'en';
  return normalizeLanguage(localStorage.getItem(STORAGE_KEY)) || normalizeLanguage(navigator.language) || 'en';
}

export const language = writable<Language>(getInitialLanguage());
export const t = derived(language, ($language) => translations[$language]);

export function setLanguage(nextLanguage: Language) {
  language.set(nextLanguage);
}

export function toggleLanguage() {
  language.update((current) => (current === 'zh-CN' ? 'en' : 'zh-CN'));
}

export function translate() {
  return translations[get(language)];
}

if (browser) {
  language.subscribe((value) => {
    localStorage.setItem(STORAGE_KEY, value);
    document.documentElement.lang = value;
  });
}
