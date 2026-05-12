# GPT Image Panel

![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi)
![SvelteKit](https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite)
![Docker](https://img.shields.io/badge/Docker-24.0+-2496ED?logo=docker)

Web panel for GPT Image 2 API image generation and editing.

English | [中文](#中文文档)

## Overview

GPT Image Panel is a lightweight FastAPI web UI for image generation and image editing. It is designed as a self-hosted panel that connects to an external GPT-compatible image API and stores generated images locally.

Key characteristics:

- SvelteKit + TypeScript frontend in `frontend/`
- FastAPI backend in `backend/app/`; use `backend.app.main:app` as the ASGI entrypoint
- public API paths, methods, status codes, SSE event names, cookies, and response shapes are contract-tested and kept stable
- API presets persisted to SQLite at `data/app.sqlite3`
- background image-generation jobs executed with `asyncio.create_task`
- local image storage under `images/`
- gallery metadata stored in SQLite at `data/app.sqlite3`
- legacy `data/gallery.json` and `data/settings.json` are imported once on startup when the database is empty
- Docker and Docker Compose deployment support with frontend static build baked into the image
- pytest API contract tests under `backend/tests/`

## Features

- settings UI for API presets, API base URL, API path, API key, and model
- generation and edit options for size, quality, format, compression, quantity, and response format
- image-to-image edits via OpenAI-compatible `/v1/images/edits`
- auto, ratio-based, and custom image sizes
- persistent top-left language switch between English and Simplified Chinese
- preview UI with prompt, parameters, elapsed time, and localized generation/edit stages
- SSE progress updates for preview state with a polling fallback on stream reconnect failure
- active job history drawer backed by SSE with selectable cancellation for queued/running generation and edit jobs
- configurable concurrency and queue limits shared by generation and edit jobs
- optional per-job webhook callback (`webhook_url`) with HTTPS validation, SSRF protection, signed delivery, and retry
- upload or gallery-based edit source selection with clickable source preview
- gallery with pagination, prompt/model/preset/size/date/favorites filters, favorite toggles, lightbox, per-image "Edit this image" actions, download, low-memory ZIP export/import with `metadata.json`, delete, delete all, copy prompt, copy image URL, and total image size display backed by persisted file-size metadata
- optional site access key with session unlock
- optional IP allowlist and reverse proxy header support
- app version badge with GitHub `VERSION` update detection
- nonce-based Content Security Policy for the served frontend index

## Architecture

### Backend

The backend is a FastAPI application under `backend/app/`.

Responsibilities are split into a few modules:

- `backend/app/main.py` — thin ASGI entrypoint
- `backend/app/api/contract_app.py` — frozen public API surface and current route wiring
- `backend/app/schemas/` — Pydantic request/response DTOs
- `backend/app/repositories/` — SQLite, gallery, image file, settings, and job persistence
- `backend/app/integrations/` — upstream GPT-compatible image API client
- `backend/app/core/` — settings, access tokens, IP allowlist, proxy headers, and URL validators
- `backend/app/services/` — webhook signing, retry, and async delivery

When serving the static frontend, the backend injects a per-response script nonce into `frontend/build/index.html` and sends a matching Content Security Policy. Asset and index serving are covered by the backend contract tests.

### Frontend

The frontend is a SvelteKit static application in `frontend/`.
The backend serves only `frontend/build`; run a frontend build before starting the production backend.

It uses:

- Tailwind CSS
- `src/lib/api/client.ts` for same-origin fetch calls to existing `/api/*` endpoints
- `src/lib/api/events.ts` for SSE wrappers
- stores split across access, settings, gallery, jobs, preview, and UI state
- components for access, header, settings drawer, job history drawer, preview, gallery, lightbox, and size selection

Frontend build:

```bash
npm --prefix frontend install
npm --prefix frontend run build
```

### Storage

Runtime persistent storage is minimal:

- generated images are saved in the `images/` directory
- gallery metadata, image byte sizes, and API presets are stored in SQLite at `data/app.sqlite3`, including generation duration
- generation job status, errors, timing, and result metadata are stored in SQLite at `data/app.sqlite3`
- active `asyncio.Task` handles live only in process memory; queued/running jobs from a previous process are marked interrupted on startup

### Generation flow

1. the frontend sends a request to `/api/generate`
2. the backend validates settings and creates a SQLite-backed job
3. the backend starts `run_generate_job(...)` with `asyncio.create_task` and tracks the task handle while it is active
4. the backend enforces shared queue-capacity and concurrency limits before starting work, then reports detailed stages while building the payload, waiting for the upstream API, parsing JSON, extracting image data, decoding `b64_json`, validating bytes, saving files, and updating gallery metadata
5. image data is decoded from base64 or downloaded from URL
6. the backend saves the file and appends the gallery metadata entry
7. the frontend listens to `/api/generate/{job_id}/events` and renders each SSE progress update in Preview
8. the job history drawer listens to `/api/generate/jobs/events`, can refresh via `/api/generate/jobs`, and cancels selected jobs via `DELETE /api/generate/{job_id}`
9. if `webhook_url` is provided, the backend validates it and asynchronously delivers `image.job.finished` after job completion (with signature headers when configured)

### Edit flow

1. the frontend lets the user upload an image file or choose an existing gallery image via "Edit this image" on cards/lightbox; the selected source can be previewed before submitting the edit request
2. the frontend sends current parameters to `/api/edits` (upload flow) or `/api/edits/from-gallery/{image_id}` (gallery flow)
3. the backend creates a SQLite-backed job and calls upstream `/v1/images/edits`
4. the source image is forwarded as multipart `image` (from upload bytes or gallery file bytes); SVG uploads are rejected
5. supported parameters are forwarded as multipart fields: `prompt`, `model`, `n`, `size`, `quality`, `output_format`, optional `response_format`, and `output_compression` when applicable
6. the backend reports detailed stages while building multipart form data, uploading the source image, waiting for the upstream API, parsing JSON, extracting edited image data, decoding `b64_json`, validating bytes, and saving files
7. returned image data is decoded from base64 or downloaded from URL
8. the backend saves the edited image and appends the gallery metadata entry
9. the frontend listens to `/api/generate/{job_id}/events` and renders preview/gallery like normal generation

## Tech stack

- Python 3.11+
- FastAPI
- Uvicorn
- aiohttp
- SQLite
- Pydantic v2
- SvelteKit
- TypeScript
- Tailwind CSS

## Project structure

```text
LICENSE
README.md
Dockerfile
docker-compose.yml
.env.example
VERSION
requirements.txt
package.json
backend/
  requirements.txt
  requirements-dev.txt
  app/
    main.py
    api/
    core/
    schemas/
    repositories/
    integrations/
    services/
  tests/
frontend/
  package.json
  svelte.config.js
  vite.config.ts
  src/
    routes/
    lib/
      api/
      stores/
      components/
      utils/
deploy/
  nginx.conf
images/
data/
```

## Getting started

### Prerequisites

You need one of the following:

- Python 3.11 or newer
- Docker
- Docker Compose

An external GPT-compatible image API is required for actual image generation.

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

If Docker Hub times out while resolving `python:3.11-slim`, use a reachable mirror image:

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### Docker Compose

```bash
cp .env.example .env
# edit .env if needed
# ACCESS_KEY is required by default unless ALLOW_UNAUTHENTICATED=true
docker-compose up -d --build --force-recreate
```

For Docker Hub timeout issues with Compose, set this in `.env` before building:

```bash
PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine
```

### Local development

```bash
pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

In another terminal:

```bash
npm run frontend:dev
```

Then open `http://localhost:5173`. The Vite dev server proxies `/api` and `/health` to FastAPI at `127.0.0.1:9090`, so browser requests stay same-origin in development.

For a single-process local smoke test, build the frontend first and run FastAPI:

```bash
npm run frontend:build
uvicorn backend.app.main:app --host 0.0.0.0 --port 9090 --reload
```

Then open `http://localhost:9090`.

If you want to run without access auth during local dev, set `ALLOW_UNAUTHENTICATED=true`.

### Health check

```bash
curl http://localhost:9090/health
```

## Usage

1. open the site
2. optionally use the top-left language switch to toggle English / Simplified Chinese
3. click the settings gear icon
4. choose an existing preset or click New
5. enter the API base URL
6. choose the API path
7. enter the API key, or an env ref such as `${OPENAI_API_KEY}`
8. optionally run Health check for the saved preset
9. click Save Preset
10. enter a prompt
11. choose generation options
12. click Generate
13. optionally click Upload (or pick "Edit this image" in Gallery/Lightbox), then click Edits to run image-to-image
14. view preview and gallery

## API paths

The panel supports these upstream paths:

### `/v1/images/generations`

- sends generation requests to the Images API
- reads image data from `data[]`

### `/v1/responses`

- sends generation requests to the Responses API
- sends only `prompt` and `model` in the upstream request body
- reads base64 image data from `output[]` items of type `image_generation_call`
- size, quality, format, compression, quantity, and response format controls are disabled in the UI for this path

### `/v1/images/edits`

- used by the Edits button after either image upload or gallery-image selection
- always calls `/v1/images/edits` on the configured API base URL
- sends multipart/form-data with `image` plus supported edit parameters (source image can come from upload or gallery file)
- uploaded source files must be supported raster image formats; SVG uploads are rejected
- if the upstream returns `404`, `405`, or `501`, the UI reports that `/v1/images/edits` is not supported and stops the edit request

## API preset health checks

- `POST /api/settings/presets/{preset_id}/health` validates the saved preset without sending a generation request
- checks include API path allowability, HTTPS URL/hostname validation, upstream host allowlist and SSRF DNS/private-IP validation, API key/env-ref presence, and a low-cost `OPTIONS`/`HEAD` upstream probe
- returned shape is `{ status, checks: [{ name, status, message }] }`, where each status is `ok`, `warning`, or `error`
- API key env refs use the exact `${ENV_VAR_NAME}` form; the database stores the reference string and generation/edit calls resolve it from the server environment at request time

## Image size modes

- `auto` — default; let the model choose the output size
- ratio presets — 1K, 2K, or 4K with ratios `1:1`, `4:3`, `3:4`, `16:9`, `9:16`, or `21:9`
- custom width and height — values are normalized to multiples of 16, max side `3840px`, aspect ratio up to `3:1`, and total pixels between `655360` and `8294400`

## Generation options

- Quality: `auto`, `low`, `medium`, or `high`
- Format: `PNG`, `JPEG`, or `WebP`
- Compression: disabled for `PNG`; `0-100` for `JPEG` and `WebP`
- Quantity: integer from `1` to `10`
- Response Format: `none`, `b64_json`, or `url`; `none` omits the `response_format` parameter

## Import and upload limits

- uploaded source images are limited by `MAX_FILE_SIZE_MB` and must be supported raster formats (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.avif`, `.bmp`, `.heic`, `.heif`, `.ico`, `.tif`, `.tiff`); SVG is rejected
- `/api/import` accepts ZIP archives created by `/api/download-all`
- import archives must include `metadata.json`
- import archives are validated for uploaded size, file count, total uncompressed size, metadata size, member-path safety, and compression ratio
- imported image entries must pass file extension and magic-byte validation before they are stored

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_API_URL` | empty | Pre-fill API base URL |
| `DEFAULT_API_KEY` | empty | Pre-fill API key; may be a literal key or an env ref such as `${OPENAI_API_KEY}` |
| `DEFAULT_API_PATH` | `/v1/images/generations` | Default upstream path |
| `DEFAULT_RESPONSES_MODEL` | `gpt-5.4` | Top-level model used when calling `/v1/responses` |
| `APP_VERSION` | `VERSION` file | Override the app version shown in the UI and returned by `/api/version` |
| `GITHUB_REPO` | `Z1rconium/gpt-image-linux` | GitHub `owner/repo` used for release update detection; set empty to disable latest-version checks |
| `ACCESS_KEY` | empty | Required by default; all non-health routes require unlock when set |
| `ALLOW_UNAUTHENTICATED` | `false` | Set `true` to explicitly allow startup without `ACCESS_KEY` |
| `IP_ALLOWLIST` | empty | Comma-separated allowed IPs/CIDRs |
| `TRUST_PROXY_HEADERS` | `false` | Read `X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto`, or `X-Forwarded-Host` from a trusted reverse proxy |
| `CSRF_ORIGIN_CHECK_ENABLED` | `true` | Reject cross-origin `POST`, `PATCH`, and `DELETE` requests using `Origin` or `Referer` checks |
| `MAX_FILE_SIZE_MB` | `50` | Max uploaded image size in MB for edit source images and imported image files |
| `IMPORT_ARCHIVE_MAX_MB` | `1000` | Max uploaded ZIP size in MB for `/api/import` |
| `IMPORT_MAX_FILES` | `500` | Max number of files allowed inside one import archive |
| `IMPORT_MAX_UNCOMPRESSED_MB` | `1024` | Max total uncompressed size in MB across all files in an import archive |
| `IMPORT_MAX_METADATA_BYTES` | `2097152` | Max `metadata.json` size in bytes for an import archive |
| `IMPORT_MAX_COMPRESSION_RATIO` | `100` | Max allowed uncompressed/compressed ratio for any imported file |
| `MAX_ACTIVE_GENERATE_JOBS` | `2` | Max number of generation and edit jobs running concurrently |
| `MAX_QUEUED_GENERATE_JOBS` | `20` | Max additional queued generation and edit jobs before new requests are rejected with `429` |
| `IMAGES_DIR` | `./images` | Directory for saved images |
| `THUMBNAILS_DIR` | `./images/thumbs` | Directory for generated gallery thumbnails |
| `THUMBNAIL_MAX_SIDE` | `512` | Max thumbnail width/height in pixels |
| `DATA_DIR` | `./data` | Directory for SQLite runtime data |
| `DATABASE_FILE` | `./data/app.sqlite3` | SQLite database for gallery metadata and API presets |
| `PYTHON_BASE_IMAGE` | `python:3.11-slim` | Docker build base image; override when Docker Hub is slow or blocked |
| `NODE_BASE_IMAGE` | `node:24-alpine` | Docker frontend build base image; override when Docker Hub is slow or blocked |
| `WEBHOOK_SIGNING_SECRET` | empty | Required when `webhook_url` is used; used to sign webhook payloads (`X-Webhook-Signature`) |
| `WEBHOOK_HOST_ALLOWLIST` | empty | Optional comma-separated webhook hostname allowlist |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` | Webhook delivery timeout per attempt (seconds) |
| `WEBHOOK_MAX_ATTEMPTS` | `3` | Max webhook delivery retry attempts |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Frontend UI |
| `GET` | `/health` | Health check |
| `GET` | `/api/version` | Current app version, configured GitHub repo, and latest-release URL |
| `GET` | `/api/access/status` | Check access-key session status |
| `POST` | `/api/access` | Unlock access for 3 hours |
| `POST` | `/api/settings` | Save the active API preset |
| `GET` | `/api/settings` | Get current settings and presets |
| `POST` | `/api/settings/presets` | Create and activate an API preset |
| `POST` | `/api/settings/presets/{preset_id}/activate` | Activate an API preset |
| `POST` | `/api/settings/presets/{preset_id}/health` | Validate a saved API preset and run a low-cost upstream probe |
| `DELETE` | `/api/settings/presets/{preset_id}` | Delete an API preset |
| `POST` | `/api/generate` | Start an image generation job |
| `POST` | `/api/edits` | Start an image edit job with multipart image upload |
| `POST` | `/api/edits/from-gallery/{image_id}` | Start an image edit job using an existing gallery image as source |
| `GET` | `/api/generate/jobs` | List queued/running generation and edit jobs; pass `include_finished=true` for persisted history |
| `GET` | `/api/generate/jobs/events` | Stream queued/running generation and edit jobs over SSE |
| `GET` | `/api/generate/{job_id}` | Get generation job status or result |
| `GET` | `/api/generate/{job_id}/events` | Stream generation job status/progress over SSE |
| `DELETE` | `/api/generate/{job_id}` | Cancel and remove a queued/running generation or edit job |
| `GET` | `/api/gallery` | List gallery images with pagination and optional `prompt`, `model`, `preset`, `size`, `date_from`, `date_to`, and `favorite` filters |
| `PATCH` | `/api/gallery/{id}/favorite` | Set or clear a gallery favorite flag |
| `GET` | `/api/image/{filename}` | Serve image file |
| `GET` | `/api/thumb/{filename}` | Serve or lazily create a WebP gallery thumbnail |
| `GET` | `/api/download/{filename}` | Download image as attachment |
| `DELETE` | `/api/gallery/{id}` | Delete gallery entry and its server image file |
| `GET` | `/api/download-all` | Download all gallery images plus `metadata.json` as a ZIP file |
| `POST` | `/api/import` | Import a ZIP created by `/api/download-all` |
| `DELETE` | `/api/gallery` | Delete all gallery entries and server image files |

## Runtime behavior notes

- The current app version is read from `APP_VERSION` first, then from the root `VERSION` file.
- The frontend checks `https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION` on page load and shows a `New` header badge when that version is newer than the current app version.
- Version comparison ignores a leading `v`, compares numeric version segments, and does not block app startup or usage.
- If the remote version check fails, the UI keeps showing the current version and continues normally.
- API presets are persisted to the SQLite database configured by `DATABASE_FILE`.
- Generation and edit job history is persisted to SQLite for queued, running-start, and terminal states; intermediate running progress remains live over SSE and is throttled before SQLite persistence.
- `/api/generate`, `/api/edits`, and `/api/edits/from-gallery/{image_id}` accept optional `webhook_url`; callback delivery uses async retries and request signing when `WEBHOOK_SIGNING_SECRET` is configured.
- Image URLs returned by upstream APIs are downloaded without automatic redirect following; redirect targets and final peer IPs are revalidated, and downloads are bounded by `MAX_FILE_SIZE_MB` while streaming.
- Generation and edit jobs share one queue; concurrency is limited by `MAX_ACTIVE_GENERATE_JOBS`, queued capacity is limited by `MAX_QUEUED_GENERATE_JOBS`, and overflow requests return `429`.
- If the database has no presets, the default preset is initialized from `DEFAULT_API_URL`, `DEFAULT_API_KEY`, and `DEFAULT_API_PATH`.
- API keys are masked in the UI. Presets may store `${ENV_VAR_NAME}` instead of a real key; the UI labels it as an env ref, SQLite stores only that reference, and generation/edit/health-check calls resolve the real value from the server environment. Literal keys are still stored as provided.
- Existing `data/settings.json` and `data/gallery.json` files are imported once when the matching database tables are empty.
- `/api/import` only accepts valid ZIP archives with safe paths and required `metadata.json`, and enforces archive/file-size, file-count, metadata-size, and compression-ratio limits during validation.
- `/api/download-all` builds export ZIP files on disk in a worker thread and removes the temporary file after the response completes, so large galleries do not need to be fully buffered in memory.
- Gallery image byte sizes are stored with each entry; `/api/gallery` uses this metadata for total-size calculations and backfills older rows from file stats when needed.
- Gallery saves generate WebP thumbnails under `THUMBNAILS_DIR`; `/api/gallery` returns `thumbnail_url`, and `/api/thumb/{filename}` lazily rebuilds missing thumbnails for older images.
- Uploaded edit source images must be supported raster image formats; SVG uploads are rejected.
- On startup, the backend scans `IMAGES_DIR` and removes gallery metadata entries whose image files are missing.
- On startup, queued/running jobs persisted by a previous process are marked as interrupted because their task handles cannot survive restart.
- Queued/running generation and edit jobs can be cancelled; cancellation updates SQLite and calls `asyncio.Task.cancel()` when the task is still in memory.
- Finished generation jobs are trimmed from SQLite when the job store exceeds `MAX_GENERATE_JOBS`.
- `DELETE /api/gallery/{image_id}` removes metadata and deletes the image file from `IMAGES_DIR` when no remaining gallery entry references that filename.
- `DELETE /api/gallery` removes all gallery metadata and deletes image files from `IMAGES_DIR`.
- Streaming image responses use an opened file handle; avoid interrupting cleanup logic if you modify serving behavior.

## Testing

```bash
npm run frontend:check
npm run frontend:build
python3 -m pytest backend/tests/test_contract.py -q
```

The contract tests cover the frozen public API surface, including access cookies, settings, generation/edit job creation, SSE response framing, gallery import/export, frontend index/CSP handling, static asset access, downloads, validation errors, and 500 error shape.

## Contributing

Contributions are welcome.

Helpful guidelines:

- keep backend changes simple and explicit
- update `VERSION` when a user-visible change or release-worthy fix warrants a new `vMAJOR.MINOR.PATCH` version
- use FastAPI response models from `backend/app/schemas/` where applicable
- keep persistent storage operations centralized in `backend/app/repositories/`
- keep upstream API interaction centralized in `backend/app/integrations/`
- keep browser requests on same-origin `/api/*` paths; do not introduce direct cross-origin frontend-to-backend calls
- avoid storing real API keys in repository files
- do not commit generated images or runtime gallery metadata unless explicitly requested
- preserve the existing async generation flow and SSE progress model unless the change explicitly requires altering job lifecycle behavior

## License

This project is licensed under `CC BY-NC 4.0` (`Creative Commons Attribution-NonCommercial 4.0 International`).

- You can use, copy, modify, redistribute, and create derivative works.
- You must provide attribution and keep the license notice.
- You may not use this project or derivative works for commercial purposes.
- If you need commercial use, you must obtain prior permission from the copyright holder.

See [LICENSE](./LICENSE) for the repository license text.

---

# 中文文档

# GPT Image Panel

GPT Image 2 API 图像生成和编辑 Web 面板。

[English](#gpt-image-panel) | 中文

## 概述

GPT Image Panel 是一个轻量级 FastAPI Web 界面，用于图像生成和图像编辑。它被设计为自托管面板，连接外部 GPT 兼容图像 API，并在本地存储生成的图片。

主要特点：

- SvelteKit + TypeScript 前端位于 `frontend/`
- FastAPI 后端位于 `backend/app/`；ASGI 入口使用 `backend.app.main:app`
- 公共 API 路径、方法、状态码、SSE 事件名、cookie 和响应结构通过契约测试冻结
- API 预设持久化保存在 SQLite：`data/app.sqlite3`
- 图像生成任务通过 `asyncio.create_task` 异步执行
- 图片保存在 `images/`
- Gallery 元数据保存在 SQLite：`data/app.sqlite3`
- 旧的 `data/gallery.json` 和 `data/settings.json` 会在数据库为空时于启动阶段导入一次
- Docker 镜像会构建并内置 SvelteKit 静态前端
- pytest API 契约测试位于 `backend/tests/`

## 功能

- 设置界面：API 预设、API Base URL、API Path、API Key、Model
- 生成/编辑选项：尺寸、质量、格式、压缩比、数量、响应格式
- 通过 OpenAI 兼容 `/v1/images/edits` 支持图生图编辑
- 支持自动、比例和自定义图像尺寸
- 左上角语言切换按钮，可在英文和简体中文之间切换，并持久保存
- 预览界面：显示提示词、参数、真实图片分辨率、生成耗时，以及本地化 generation/edit 细分阶段
- 预览进度通过 SSE 实时推送，流断开时退回轮询兜底
- 历史任务抽屉通过 SSE 更新正在排队/运行的生成和编辑任务，并支持选择后主动终止
- 生成和编辑任务共享可配置的并发上限与排队上限
- 支持每个任务可选 `webhook_url` 回调：仅允许 HTTPS，带 SSRF 防护、签名投递与重试
- 支持上传图片或选择 Gallery 图片作为编辑源，并可在提交前预览编辑源
- Gallery：分页、按 prompt/model/preset/尺寸/日期/收藏筛选、收藏切换、Lightbox、卡片/Lightbox 直接 “Edit this image”、生成所用预设、下载、低内存占用的带 `metadata.json` ZIP 导出/导入（导入含安全校验）、删除、全部删除、复制提示词、复制图片链接、基于持久化文件大小元数据的总大小显示
- 可选站点访问密钥
- 可选 IP 白名单和反向代理头支持
- 头部显示当前版本，并可基于 GitHub 仓库 `VERSION` 检测新版本
- 后端服务前端入口时会注入 nonce，并发送对应的 Content Security Policy

## 架构

### 后端

后端是 FastAPI 应用，位于 `backend/app/`。

功能拆分为以下模块：

- `backend/app/main.py` — 很薄的 ASGI 入口
- `backend/app/api/contract_app.py` — 冻结的公共 API 表面和当前路由组装
- `backend/app/schemas/` — Pydantic 请求/响应 DTO
- `backend/app/repositories/` — SQLite、Gallery、图片文件、settings 和 jobs 持久化
- `backend/app/integrations/` — 上游 GPT 兼容图片 API 调用
- `backend/app/core/` — settings、访问 token、IP allowlist、proxy header 和 URL 校验
- `backend/app/services/` — webhook 签名、重试和异步投递

后端服务静态前端时，会为 `frontend/build/index.html` 注入每次响应不同的 script nonce，并发送匹配的 Content Security Policy。前端入口和静态资源访问已纳入后端契约测试。

### 前端

前端是 SvelteKit 静态应用，位于 `frontend/`。
后端只服务 `frontend/build`；生产方式启动后端前需要先完成前端构建。

使用技术：

- Tailwind CSS
- `src/lib/api/client.ts` 封装同源 `/api/*` fetch
- `src/lib/api/events.ts` 封装 SSE
- stores 拆分为 access、settings、gallery、jobs、preview 和 UI
- 组件拆分为 access、header、settings drawer、job history drawer、preview、gallery、lightbox 和 size dialog

前端构建命令：

```bash
npm --prefix frontend install
npm --prefix frontend run build
```

### 存储

运行时持久化存储非常简单：

- 生成的图片保存在 `images/` 目录
- Gallery 元数据、图片字节数和 API 预设保存在 SQLite：`data/app.sqlite3`，包含真实图片宽高和生成耗时
- 生成/编辑任务的状态、错误、耗时、请求参数和结果元数据保存在 SQLite：`data/app.sqlite3`
- 运行中的 `asyncio.Task` 句柄仅保存在进程内存中；重启后，上个进程遗留的排队/运行任务会被标记为 interrupted

### 生成流程

1. 前端请求 `/api/generate`
2. 后端校验配置并创建持久化到 SQLite 的任务
3. 后端通过 `asyncio.create_task` 启动 `run_generate_job(...)`，并在任务运行期间记录 task 句柄
4. 后端会先执行共享队列容量与并发限制校验，再在构建 payload、等待上游 API、解析 JSON、提取图片数据、解码 `b64_json`、校验字节、保存文件和更新 Gallery 元数据时持续上报细分阶段
5. 图片数据从 base64 解码或从 URL 下载
6. 后端保存文件并写入 Gallery 元数据
7. 前端监听 `/api/generate/{job_id}/events` 的 SSE 事件，并在 Preview 中实时渲染当前阶段
8. 历史任务抽屉监听 `/api/generate/jobs/events`，也可通过 `/api/generate/jobs` 手动刷新，并通过 `DELETE /api/generate/{job_id}` 取消选中的任务
9. 若请求里提供 `webhook_url`，后端会先校验后异步投递 `image.job.finished` 回调；配置签名密钥时会附带签名请求头
10. 当共享队列已满时，生成和编辑请求会返回 `429 Generation job queue is full`

### 编辑流程

1. 前端支持两种编辑源：上传受支持的位图图片文件，或在 Gallery 卡片/Lightbox 点击 “Edit this image”；选中后可先预览编辑源
2. 前端将当前参数发送到 `/api/edits`（上传流）或 `/api/edits/from-gallery/{image_id}`（Gallery 流）
3. 后端创建持久化到 SQLite 的任务并调用上游 `/v1/images/edits`
4. 源图片以 multipart `image` 字段转发（来自上传字节或 Gallery 文件字节）；SVG 上传会被拒绝
5. 支持的参数以 multipart 字段转发：`prompt`、`model`、`n`、`size`、`quality`、`output_format`、可选的 `response_format`，以及适用时的 `output_compression`
6. 后端在构建 multipart 表单、上传源图片、等待上游 API、解析 JSON、提取编辑图片数据、解码 `b64_json`、校验字节和保存文件时持续上报细分阶段
7. 返回图片数据从 base64 解码或从 URL 下载
8. 后端保存编辑后的图片并写入 Gallery 元数据
9. 前端监听 `/api/generate/{job_id}/events`，并像普通生成一样渲染预览和 Gallery
10. 编辑任务与生成任务共享同一套并发与排队限制

## 技术栈

- Python 3.11+
- FastAPI
- Uvicorn
- aiohttp
- SQLite
- Pydantic v2
- SvelteKit
- TypeScript
- Tailwind CSS

## 项目结构

```text
LICENSE
README.md
Dockerfile
docker-compose.yml
.env.example
VERSION
requirements.txt
package.json
backend/
  requirements.txt
  requirements-dev.txt
  app/
    main.py
    api/
    core/
    schemas/
    repositories/
    integrations/
    services/
  tests/
frontend/
  package.json
  svelte.config.js
  vite.config.ts
  src/
    routes/
    lib/
      api/
      stores/
      components/
      utils/
deploy/
  nginx.conf
images/
data/
```

## 快速开始

### 前置条件

需要以下条件之一：

- Python 3.11 或更新版本
- Docker
- Docker Compose

实际生成图像需要一个外部 GPT 兼容图像 API。

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

如果解析 `python:3.11-slim` 时 Docker Hub 超时，可以改用可访问的镜像源：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### Docker Compose

```bash
cp .env.example .env
# 按需修改 .env
# 默认必须设置 ACCESS_KEY，除非显式设置 ALLOW_UNAUTHENTICATED=true
docker-compose up -d --build --force-recreate
```

如果 Compose 构建时 Docker Hub 超时，先在 `.env` 里设置：

```bash
PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine
```

### 本地开发

```bash
pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

另开一个终端：

```bash
npm run frontend:dev
```

然后打开 `http://localhost:5173`。Vite dev server 会把 `/api` 和 `/health` 代理到 `127.0.0.1:9090`，浏览器侧仍然是同源路径。

如果要单进程 smoke test：

```bash
npm run frontend:build
uvicorn backend.app.main:app --host 0.0.0.0 --port 9090 --reload
```

然后打开 `http://localhost:9090`。

若本地开发需要无鉴权启动，请设置 `ALLOW_UNAUTHENTICATED=true`。

### 健康检查

```bash
curl http://localhost:9090/health
```

## 使用方法

1. 打开网站
2. 可用左上角语言按钮在英文/简体中文之间切换
3. 点击右上角齿轮图标
4. 选择已有预设，或点击 New 新建预设
5. 填写 API Base URL
6. 选择 API Path
7. 填写 API Key，或填写 `${OPENAI_API_KEY}` 这类环境变量引用
8. 可选：对已保存预设执行 Health check
9. 点击 Save Preset
10. 输入提示词
11. 选择生成参数
12. 点击 Generate
13. 也可以点击 Upload 选择图片，再点击 Edits 执行图生图
14. 查看预览和 Gallery

## 支持的 API Path

面板支持以下上游路径：

### `/v1/images/generations`

- 向 Images API 发送生成请求
- 从 `data[]` 读取图片数据

### `/v1/responses`

- 向 Responses API 发送生成请求
- 上游请求体只发送 `prompt` 和 `model`
- 从 `output[]` 中类型为 `image_generation_call` 的项目读取 base64 图片数据
- 选择该路径时，界面中的尺寸、质量、格式、压缩率、数量和 response format 控件会被禁用

### `/v1/images/edits`

- 上传图片后点击 Edits，或在 Gallery 里选择 “Edit this image” 后点击 Edits 使用
- 始终在配置的 API Base URL 下调用 `/v1/images/edits`
- 使用 multipart/form-data 发送 `image` 和支持的编辑参数（源图片可来自上传或 Gallery）
- 上传的源图必须是受支持的位图图片格式；SVG 上传会被拒绝
- 如果上游返回 `404`、`405` 或 `501`，界面会提示 `/v1/images/edits` 不受支持并停止编辑请求

## API 预设健康检查

- `POST /api/settings/presets/{preset_id}/health` 会校验已保存预设，不会发送真实生成请求
- 检查项包括 API Path 是否允许、HTTPS URL/hostname、上游 host allowlist、SSRF DNS/内网 IP 校验、API Key/环境变量引用是否可用，以及低成本 `OPTIONS`/`HEAD` 上游探测
- 返回结构为 `{ status, checks: [{ name, status, message }] }`，状态值为 `ok`、`warning` 或 `error`
- API Key 环境变量引用必须使用完整的 `${ENV_VAR_NAME}` 格式；数据库只保存引用字符串，生成/编辑请求会在执行时从服务端环境变量解析真实值

## 图像尺寸模式

- `auto` — 默认值；让模型自动选择输出尺寸
- 比例预设 — 1K / 2K / 4K，支持比例 `1:1`、`4:3`、`3:4`、`16:9`、`9:16`、`21:9`
- 自定义宽高 — 会归一化到 16 的倍数，最大边 `3840px`，最大纵横比 `3:1`，像素总量在 `655360` 到 `8294400` 之间

## 生成选项

- Quality：`auto`、`low`、`medium`、`high`
- Format：`PNG`、`JPEG`、`WebP`
- Compression：`PNG` 不可用；`JPEG` 和 `WebP` 可设置 `0-100`
- Quantity：`1` 到 `10`
- Response Format：`none`、`b64_json` 或 `url`；`none` 会省略 `response_format` 参数

## 导入与上传限制

- 上传作为编辑源图的图片大小受 `MAX_FILE_SIZE_MB` 限制，且必须是受支持的位图格式（`.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`、`.avif`、`.bmp`、`.heic`、`.heif`、`.ico`、`.tif`、`.tiff`）；SVG 会被拒绝
- `/api/import` 只接受由 `/api/download-all` 导出的 ZIP 归档
- 导入 ZIP 必须包含 `metadata.json`
- 导入 ZIP 会校验上传体积、文件数、解压总体积、metadata 大小、安全路径和压缩比
- 导入图片条目在存储前必须通过扩展名和文件魔数校验

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_API_URL` | 空 | 预填 API Base URL |
| `DEFAULT_API_KEY` | 空 | 预填 API Key；可以是真实 key，也可以是 `${OPENAI_API_KEY}` 这类环境变量引用 |
| `DEFAULT_API_PATH` | `/v1/images/generations` | 默认上游路径 |
| `DEFAULT_RESPONSES_MODEL` | `gpt-5.4` | 调用 `/v1/responses` 时使用的顶层模型 |
| `APP_VERSION` | `VERSION` 文件 | 覆盖界面显示和 `/api/version` 返回的当前应用版本 |
| `GITHUB_REPO` | `Z1rconium/gpt-image-linux` | 用于检测新版本的 GitHub `owner/repo`；设为空可禁用最新版本检查 |
| `ACCESS_KEY` | 空 | 默认要求设置；设置后每个非健康路由均需解锁 |
| `ALLOW_UNAUTHENTICATED` | `false` | 设置为 `true` 可显式允许在未设置 `ACCESS_KEY` 时启动 |
| `IP_ALLOWLIST` | 空 | 允许访问的 IP/CIDR，逗号分隔 |
| `TRUST_PROXY_HEADERS` | `false` | 是否读取受信任反向代理的 `X-Forwarded-For`、`X-Real-IP`、`X-Forwarded-Proto` 或 `X-Forwarded-Host` |
| `CSRF_ORIGIN_CHECK_ENABLED` | `true` | 是否通过 `Origin` 或 `Referer` 拒绝跨站 `POST`、`PATCH`、`DELETE` 请求 |
| `MAX_FILE_SIZE_MB` | `50` | 上传为编辑源图的图片和导入图片文件的最大体积（MB） |
| `IMPORT_ARCHIVE_MAX_MB` | `1000` | `/api/import` 可上传 ZIP 的最大体积（MB） |
| `IMPORT_MAX_FILES` | `500` | 单个导入归档允许的最大文件数 |
| `IMPORT_MAX_UNCOMPRESSED_MB` | `1024` | 导入归档内所有文件解压后的最大总体积（MB） |
| `IMPORT_MAX_METADATA_BYTES` | `2097152` | 导入归档中 `metadata.json` 的最大字节数 |
| `IMPORT_MAX_COMPRESSION_RATIO` | `100` | 单个导入文件允许的最大解压/压缩体积比 |
| `MAX_ACTIVE_GENERATE_JOBS` | `2` | 生成和编辑任务允许同时运行的最大数量 |
| `MAX_QUEUED_GENERATE_JOBS` | `20` | 超出并发后允许继续排队的最大任务数；超过后新请求返回 `429` |
| `IMAGES_DIR` | `./images` | 图片存储目录 |
| `THUMBNAILS_DIR` | `./images/thumbs` | Gallery 缩略图生成目录 |
| `THUMBNAIL_MAX_SIDE` | `512` | 缩略图最大宽/高像素 |
| `DATA_DIR` | `./data` | SQLite 运行时数据目录 |
| `DATABASE_FILE` | `./data/app.sqlite3` | 保存 Gallery 元数据和 API 预设的 SQLite 数据库 |
| `PYTHON_BASE_IMAGE` | `python:3.11-slim` | Docker 构建基础镜像；Docker Hub 慢或不可访问时可覆盖 |
| `NODE_BASE_IMAGE` | `node:24-alpine` | Docker 前端构建基础镜像；Docker Hub 慢或不可访问时可覆盖 |
| `WEBHOOK_SIGNING_SECRET` | 空 | 使用 `webhook_url` 时需要；用于签名 webhook payload（`X-Webhook-Signature`） |
| `WEBHOOK_HOST_ALLOWLIST` | 空 | 可选 webhook 主机名白名单，逗号分隔 |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` | 单次 webhook 投递超时时间（秒） |
| `WEBHOOK_MAX_ATTEMPTS` | `3` | webhook 最大重试次数 |

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 前端页面 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/version` | 当前应用版本、配置的 GitHub 仓库和 latest release URL |
| `GET` | `/api/access/status` | 访问密钥会话状态 |
| `POST` | `/api/access` | 解锁访问 3 小时 |
| `POST` | `/api/settings` | 保存当前 API 预设 |
| `GET` | `/api/settings` | 获取当前设置和预设列表 |
| `POST` | `/api/settings/presets` | 新建并激活 API 预设 |
| `POST` | `/api/settings/presets/{preset_id}/activate` | 激活 API 预设 |
| `POST` | `/api/settings/presets/{preset_id}/health` | 校验已保存 API 预设并执行低成本上游探测 |
| `DELETE` | `/api/settings/presets/{preset_id}` | 删除 API 预设 |
| `POST` | `/api/generate` | 创建图像生成任务 |
| `POST` | `/api/edits` | 使用 multipart 图片上传创建图像编辑任务 |
| `POST` | `/api/edits/from-gallery/{image_id}` | 使用已有 Gallery 图片作为源图创建图像编辑任务 |
| `GET` | `/api/generate/jobs` | 查询排队/运行中的生成和编辑任务；传 `include_finished=true` 可查持久化历史 |
| `GET` | `/api/generate/jobs/events` | 通过 SSE 推送排队/运行中的生成和编辑任务 |
| `GET` | `/api/generate/{job_id}` | 查询任务状态或结果 |
| `GET` | `/api/generate/{job_id}/events` | 通过 SSE 推送单个任务状态和进度 |
| `DELETE` | `/api/generate/{job_id}` | 取消并移除排队/运行中的生成或编辑任务 |
| `GET` | `/api/gallery` | 分页查询 Gallery 图片，可选 `prompt`、`model`、`preset`、`size`、`date_from`、`date_to`、`favorite` 筛选 |
| `PATCH` | `/api/gallery/{id}/favorite` | 设置或取消 Gallery 收藏标记 |
| `GET` | `/api/image/{filename}` | 访问图片文件 |
| `GET` | `/api/thumb/{filename}` | 访问或懒生成 WebP Gallery 缩略图 |
| `GET` | `/api/download/{filename}` | 下载图片 |
| `DELETE` | `/api/gallery/{id}` | 删除 Gallery 条目和对应服务器图片文件 |
| `GET` | `/api/download-all` | 下载 Gallery 所有图片和 `metadata.json` 为 ZIP 文件 |
| `POST` | `/api/import` | 导入 `/api/download-all` 创建的 ZIP |
| `DELETE` | `/api/gallery` | 删除所有 Gallery 条目和服务器图片文件 |

## 运行时注意事项

- 当前应用版本优先读取 `APP_VERSION`，其次读取根目录 `VERSION` 文件。
- 前端页面加载时会请求 `https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION`，当远端版本高于当前版本时在头部版本 badge 上显示 `New`。
- 版本比较会忽略开头的 `v`，按数字版本段比较；远端检查失败不会阻塞启动或使用。
- API 预设持久化保存在 `DATABASE_FILE` 指向的 SQLite 数据库。
- 生成/编辑任务历史会把 queued、running 起始和终态持久化到 SQLite；中间 running 进度仍通过 SSE 实时推送，并在写入 SQLite 前节流。
- `/api/generate`、`/api/edits` 和 `/api/edits/from-gallery/{image_id}` 接受可选 `webhook_url`；回调使用异步重试，配置 `WEBHOOK_SIGNING_SECRET` 时会签名请求。
- 上游返回的图片 URL 下载不会自动跟随重定向；重定向目标和最终 peer IP 会重新校验，下载流式读取且受 `MAX_FILE_SIZE_MB` 限制。
- 生成和编辑任务共享同一个队列；并发数受 `MAX_ACTIVE_GENERATE_JOBS` 限制，排队容量受 `MAX_QUEUED_GENERATE_JOBS` 限制，超出时请求返回 `429`。
- 如果数据库中没有 preset，默认预设会使用 `DEFAULT_API_URL`、`DEFAULT_API_KEY` 和 `DEFAULT_API_PATH` 初始化。
- API Key 在界面中掩码展示。预设可以保存 `${ENV_VAR_NAME}` 代替真实 key；界面会标记为环境变量引用，SQLite 只保存该引用，生成/编辑/健康检查会从服务端环境变量解析真实值。直接填写真实 key 时仍会按原样保存。
- 旧的 `data/settings.json` 和 `data/gallery.json` 会在对应数据库表为空时导入一次。
- `/api/import` 只接受带安全路径且包含 `metadata.json` 的有效 ZIP，并会校验归档/文件体积、文件数量、metadata 大小和压缩比。
- `/api/download-all` 会在线程中构建磁盘临时 ZIP，并在响应结束后删除临时文件，大 Gallery 导出时无需把完整 ZIP 缓存在内存中。
- Gallery 图片字节数会随条目持久化保存；`/api/gallery` 使用该元数据计算总大小，并在旧数据缺失时从文件 stat 回填。
- Gallery 保存图片时会在 `THUMBNAILS_DIR` 下生成 WebP 缩略图；`/api/gallery` 返回 `thumbnail_url`，`/api/thumb/{filename}` 会为旧图片懒生成缺失缩略图。
- 上传作为编辑源图的文件必须是受支持的位图图片格式；SVG 上传会被拒绝。
- 启动时后端会扫描 `IMAGES_DIR`，并移除图片文件已不存在的 Gallery 元数据条目。
- 启动时，上个进程遗留的排队/运行任务会被标记为 interrupted，因为 `asyncio.Task` 句柄无法跨进程恢复。
- 排队/运行中的生成和编辑任务可以主动取消；取消会更新 SQLite，并在任务句柄仍在内存中时调用 `asyncio.Task.cancel()`。
- 当任务数量超过 `MAX_GENERATE_JOBS` 时，已结束任务会从 SQLite 裁剪。
- `DELETE /api/gallery/{image_id}` 会删除元数据；如果没有其他 Gallery 条目引用同一文件名，也会删除 `IMAGES_DIR` 中的对应图片文件。
- `DELETE /api/gallery` 会删除所有 Gallery 元数据，并删除 `IMAGES_DIR` 中的图片文件。
- 图片流式返回依赖已打开的文件句柄，修改相关逻辑时需注意资源释放。

## 测试

```bash
npm run frontend:check
npm run frontend:build
python3 -m pytest backend/tests/test_contract.py -q
```

契约测试覆盖冻结的公共 API 表面，包括访问 cookie、settings、generation/edit 任务创建、SSE 响应 framing、Gallery import/export、前端入口/CSP 处理、静态资源访问、下载、422 校验错误和 500 错误形状。

## 贡献

欢迎贡献。

建议遵循以下原则：

- 后端修改尽量保持简单和明确
- 用户可见变更或值得发布的修复应同步更新 `VERSION`，格式为 `vMAJOR.MINOR.PATCH`
- 尽量使用 `backend/app/schemas/` 中的 FastAPI 响应模型
- 持久化存储操作集中在 `backend/app/repositories/`
- 上游 API 调用集中在 `backend/app/integrations/`
- 浏览器请求保持同源 `/api/*` 路径，不要引入前端直连跨域后端
- 不要在仓库文件中保存真实 API Key
- 除非明确要求，否则不要提交生成图片或运行时 Gallery 元数据
- 除非明确要求改变任务生命周期，否则保留现有异步生成与 SSE 进度机制

## 许可证

本项目采用 `CC BY-NC 4.0` 许可证，即 `Creative Commons Attribution-NonCommercial 4.0 International`。

- 允许任何人使用、复制、修改、再分发以及二次创作。
- 需要保留署名，并附带许可证说明。
- 不允许将本项目或其衍生作品用于商业用途。
- 如需商业使用，必须事先获得著作权人的许可。

许可证全文见 [LICENSE](./LICENSE)。
