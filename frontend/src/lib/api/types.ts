export type ApiPath = '/v1/images/generations' | '/v1/responses';
export type ApiKeySource = 'empty' | 'stored' | 'env';
export type PresetHealthStatus = 'ok' | 'warning' | 'error';

export type ApiPreset = {
  id: string;
  name: string;
  api_url: string;
  api_path: ApiPath;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
};

export type SettingsResponse = {
  active_preset_id: string;
  api_url: string;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
  api_path: ApiPath;
  has_upstream_socks5_proxy: boolean;
  upstream_socks5_proxy_masked: string;
  presets: ApiPreset[];
};

export type PresetHealthCheck = {
  name: string;
  status: PresetHealthStatus;
  message: string;
};

export type PresetHealthResponse = {
  status: PresetHealthStatus;
  checks: PresetHealthCheck[];
};

export type AccessStatus = {
  authenticated: boolean;
  expires_at?: string | null;
};

export type GenerateRequestBody = {
  prompt: string;
  size: string;
  model: string;
  n: number;
  quality: 'auto' | 'low' | 'medium' | 'high';
  output_format: 'png' | 'jpeg' | 'webp';
  output_compression?: number | null;
  response_format?: 'url' | 'b64_json' | null;
  webhook_url?: string | null;
};

export type GenerateJobResponse = {
  job_id: string;
  status: 'queued' | 'running' | 'success' | 'error';
  message?: string | null;
  stage?: string | null;
  operation?: 'generation' | 'edit' | null;
};

export type GenerateJobStatus = GenerateJobResponse & {
  id?: string | null;
  image_id?: string | null;
  image_url?: string | null;
  prompt?: string | null;
  size?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  model?: string | null;
  quality?: string | null;
  output_format?: string | null;
  output_compression?: number | null;
  response_format?: string | null;
  n?: number | null;
  api_path?: string | null;
  api_preset_name?: string | null;
  duration?: string | null;
  error?: string | null;
};

export type GalleryEntry = {
  id: string;
  prompt: string;
  size: string;
  filename: string;
  thumbnail_filename?: string | null;
  thumbnail_url?: string | null;
  created_at: string;
  image_width?: number | null;
  image_height?: number | null;
  model?: string | null;
  quality?: string | null;
  output_format?: string | null;
  output_compression?: number | null;
  response_format?: string | null;
  n?: number | null;
  api_path?: string | null;
  api_preset_name?: string | null;
  duration?: string | null;
  favorite: boolean;
  bytes?: number | null;
};

export type GalleryResponse = {
  total: number;
  total_bytes: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
  images: GalleryEntry[];
  filter_options: {
    models: string[];
    presets: string[];
    sizes: string[];
  };
};

export type MessageResponse = {
  status: string;
  message: string;
};

export type GalleryBatchResponse = {
  status: string;
  count: number;
  file_count: number;
};
