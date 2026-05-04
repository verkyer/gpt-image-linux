# GPT Image Panel

Web panel for GPT Image 2 API image generation and editing.

English | [中文](#中文文档)

## Overview

GPT Image Panel is a lightweight FastAPI web UI for image generation and image editing. It is designed as a self-hosted panel that connects to an external GPT-compatible image API and stores generated images locally.

Key characteristics:

- single-page frontend served from `static/index.html`
- FastAPI backend defined primarily in `app/main.py`
- API presets persisted to `data/settings.json`
- background image-generation jobs executed with `asyncio.create_task`
- local image storage under `images/`
- gallery metadata stored in `data/gallery.json`
- API preset settings stored in `data/settings.json`
- Docker and Docker Compose deployment support
- no test suite is currently present in the repository

## Features

- settings UI for API presets, API base URL, API path, API key, and model
- generation and edit options for size, quality, format, compression, quantity, and response format
- image-to-image edits via OpenAI-compatible `/v1/images/edits`
- auto, ratio-based, and custom image sizes
- preview UI with prompt, parameters, elapsed time, and detailed English generation/edit stages
- job polling UI with 2-second interval and 10-minute timeout
- gallery with pagination, lightbox, download, download all as ZIP, delete, copy prompt, and copy image URL
- optional site access key with session unlock
- optional IP allowlist and reverse proxy header support

## Architecture

### Backend

The backend is a FastAPI application defined primarily in `app/main.py`.

Responsibilities are split into a few modules:

- `app/main.py` — application bootstrap, access control, routes, and background job orchestration
- `app/models.py` — request and response models
- `app/proxy.py` — upstream API client and image decoding
- `app/storage.py` — image saving, gallery persistence, directory checks
- `app/config.py` — environment-based configuration

### Frontend

The frontend is a single-page application in `static/index.html`.

It uses:

- Tailwind CSS via CDN
- Font Awesome via CDN
- vanilla JavaScript for polling, gallery rendering, preview/lightbox, download, and delete actions

There is no frontend build step.

### Storage

Runtime persistent storage is minimal:

- generated images are saved in the `images/` directory
- gallery metadata is stored in `data/gallery.json`
- generation jobs live only in process memory and are lost on restart

### Generation flow

1. the frontend sends a request to `/api/generate`
2. the backend validates settings and creates an in-memory job
3. the backend starts `run_generate_job(...)` with `asyncio.create_task`
4. the backend reports detailed stages while building the payload, waiting for the upstream API, parsing JSON, extracting image data, decoding `b64_json`, validating bytes, saving files, and updating gallery metadata
5. image data is decoded from base64 or downloaded from URL
6. the backend saves the file and appends the gallery metadata entry
7. the frontend polls `/api/generate/{job_id}` until success or error and renders the current stage in Preview

### Edit flow

1. the frontend lets the user upload any image file
2. the frontend sends the uploaded image and current parameters to `/api/edits`
3. the backend creates an in-memory job and calls upstream `/v1/images/edits`
4. the uploaded image is forwarded as multipart `image`
5. supported parameters are forwarded as multipart fields: `prompt`, `model`, `n`, `size`, `quality`, `output_format`, optional `response_format`, and `output_compression` when applicable
6. the backend reports detailed stages while building multipart form data, uploading the source image, waiting for the upstream API, parsing JSON, extracting edited image data, decoding `b64_json`, validating bytes, and saving files
7. returned image data is decoded from base64 or downloaded from URL
8. the backend saves the edited image and appends the gallery metadata entry
9. the frontend polls `/api/generate/{job_id}` and renders preview/gallery like normal generation

## Tech stack

- Python 3.11+
- FastAPI
- Uvicorn
- httpx
- aiofiles
- Pydantic v2
- Pillow
- Tailwind CSS and Font Awesome via CDN

## Project structure

```text
LICENSE
README.md
README_ZH.md
Dockerfile
docker-compose.yml
.env.example
requirements.txt
app/
  __init__.py
  config.py
  main.py
  models.py
  proxy.py
  storage.py
static/
  index.html
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
  -p 9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

If Docker Hub times out while resolving `python:3.11-slim`, use a reachable mirror image:

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  -t gpt-image-panel .
```

### Docker Compose

```bash
cp .env.example .env
# edit .env if needed
docker-compose up -d --build --force-recreate
```

For Docker Hub timeout issues with Compose, set this in `.env` before building:

```bash
PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
```

### Local development

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9090 --reload
```

Then open `http://localhost:9090`.

### Health check

```bash
curl http://localhost:9090/health
```

## Usage

1. open the site
2. click the settings gear icon
3. choose an existing preset or click New
4. enter the API base URL
5. choose the API path
6. enter the API key
7. click Save Preset
8. enter a prompt
9. choose generation options
10. click Generate
11. optionally click Upload, choose an image, then click Edits to run image-to-image
12. view preview and gallery

## API paths

The panel supports these upstream paths:

### `/v1/images/generations`

- sends generation requests to the Images API
- reads image data from `data[]`

### `/v1/responses`

- sends generation requests to the Responses API
- uses an `image_generation` tool
- reads base64 image data from `output[]` items of type `image_generation_call`
- the selected image model is passed to the tool
- the top-level Responses model defaults to `gpt-5.4` and can be changed with `DEFAULT_RESPONSES_MODEL`

### `/v1/images/edits`

- used by the Edits button after an image is uploaded
- always calls `/v1/images/edits` on the configured API base URL
- sends multipart/form-data with `image` plus supported edit parameters
- if the upstream returns `404`, `405`, or `501`, the UI reports that `/v1/images/edits` is not supported and stops the edit request

## Image size modes

- `auto` — let the model choose the output size
- ratio presets — 1K, 2K, or 4K with ratios `1:1`, `4:3`, `3:4`, `16:9`, `9:16`, or `21:9`
- custom width and height — values are normalized to multiples of 16, max side `3840px`, aspect ratio up to `3:1`, and total pixels between `655360` and `8294400`

## Generation options

- Quality: `auto`, `low`, `medium`, or `high`
- Format: `PNG`, `JPEG`, or `WebP`
- Compression: disabled for `PNG`; `0-100` for `JPEG` and `WebP`
- Quantity: integer from `1` to `10`
- Response Format: `b64_json`, `url`, or `none`; `none` omits the `response_format` parameter

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_API_URL` | empty | Pre-fill API base URL |
| `DEFAULT_API_KEY` | empty | Pre-fill API key |
| `DEFAULT_API_PATH` | `/v1/images/generations` | Default upstream path |
| `DEFAULT_RESPONSES_MODEL` | `gpt-5.4` | Top-level model used when calling `/v1/responses` |
| `ACCESS_KEY` | empty | Site access key; when set, every non-health route requires unlock |
| `IP_ALLOWLIST` | empty | Comma-separated allowed IPs/CIDRs |
| `TRUST_PROXY_HEADERS` | `false` | Read `X-Forwarded-For` or `X-Real-IP` from a trusted reverse proxy |
| `MAX_FILE_SIZE_MB` | `50` | Max image size in MB |
| `IMAGES_DIR` | `./images` | Directory for saved images |
| `DATA_DIR` | `./data` | Directory for gallery metadata |
| `PYTHON_BASE_IMAGE` | `python:3.11-slim` | Docker build base image; override when Docker Hub is slow or blocked |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Frontend UI |
| `GET` | `/health` | Health check |
| `GET` | `/api/access/status` | Check access-key session status |
| `POST` | `/api/access` | Unlock access for 3 hours |
| `POST` | `/api/settings` | Save the active API preset |
| `GET` | `/api/settings` | Get current settings and presets |
| `POST` | `/api/settings/presets` | Create and activate an API preset |
| `POST` | `/api/settings/presets/{preset_id}/activate` | Activate an API preset |
| `DELETE` | `/api/settings/presets/{preset_id}` | Delete an API preset |
| `POST` | `/api/generate` | Start an image generation job |
| `POST` | `/api/edits` | Start an image edit job with multipart image upload |
| `GET` | `/api/generate/{job_id}` | Get generation job status or result |
| `GET` | `/api/gallery` | List gallery images with pagination |
| `GET` | `/api/image/{filename}` | Serve image file |
| `GET` | `/api/download/{filename}` | Download image as attachment |
| `DELETE` | `/api/gallery/{id}` | Delete gallery entry |
| `GET` | `/api/download-all` | Download all gallery images as a ZIP file |

## Runtime behavior notes

- API presets are persisted to `data/settings.json`.
- If `data/settings.json` does not exist, the default preset is initialized from `DEFAULT_API_URL`, `DEFAULT_API_KEY`, and `DEFAULT_API_PATH`.
- API keys are masked in the UI but stored as plain text in `data/settings.json`.
- Finished generation jobs are trimmed when the job store exceeds `MAX_GENERATE_JOBS`.
- `DELETE /api/gallery/{image_id}` removes metadata but does not delete the image file from disk.
- Streaming image responses use an opened file handle; avoid interrupting cleanup logic if you modify serving behavior.

## Testing

There is no test suite in the repository.

## Contributing

Contributions are welcome.

Helpful guidelines:

- keep backend changes simple and explicit
- use FastAPI response models from `app/models.py` where applicable
- keep persistent file operations centralized in `app/storage.py`
- keep upstream API interaction centralized in `app/proxy.py`
- avoid introducing a frontend build system unless explicitly requested
- avoid storing real API keys in repository files
- do not commit generated images or runtime gallery metadata unless explicitly requested
- preserve the existing async generation flow and polling model unless the change explicitly requires altering job lifecycle behavior

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

- 单页前端，由 `static/index.html` 提供
- FastAPI 后端主要定义在 `app/main.py`
- API 预设持久化保存在 `data/settings.json`
- 图像生成任务通过 `asyncio.create_task` 异步执行
- 图片保存在 `images/`
- Gallery 元数据保存在 `data/gallery.json`
- API 预设配置保存在 `data/settings.json`
- 支持 Docker 和 Docker Compose 部署
- 仓库目前没有测试套件

## 功能

- 设置界面：API 预设、API Base URL、API Path、API Key、Model
- 生成/编辑选项：尺寸、质量、格式、压缩比、数量、响应格式
- 通过 OpenAI 兼容 `/v1/images/edits` 支持图生图编辑
- 支持自动、比例和自定义图像尺寸
- 预览界面：显示提示词、参数、真实图片分辨率、生成耗时，以及英文 generation/edit 细分阶段
- 任务轮询界面：2 秒轮询一次，最长 10 分钟
- Gallery：分页、Lightbox、下载、批量下载为 ZIP、删除、复制提示词、复制图片链接
- 可选站点访问密钥
- 可选 IP 白名单和反向代理头支持

## 架构

### 后端

后端是 FastAPI 应用，主要定义在 `app/main.py`。

功能拆分为以下模块：

- `app/main.py` — 应用启动、访问控制、路由、后台任务管理
- `app/models.py` — 请求与响应模型
- `app/proxy.py` — 上游 API 调用与图片解码
- `app/storage.py` — 图片存储、Gallery 持久化、目录检查
- `app/config.py` — 环境变量配置

### 前端

前端是单页应用，位于 `static/index.html`。

使用技术：

- Tailwind CSS（CDN）
- Font Awesome（CDN）
- 原生 JavaScript，用于任务轮询、Gallery 渲染、预览/Lightbox、下载和删除操作

没有前端构建步骤。

### 存储

运行时持久化存储非常简单：

- 生成的图片保存在 `images/` 目录
- Gallery 元数据保存在 `data/gallery.json`，新记录会保存真实图片宽高
- 生成任务仅保存在进程内存中，重启后会丢失

### 生成流程

1. 前端请求 `/api/generate`
2. 后端校验配置并创建内存任务
3. 后端通过 `asyncio.create_task` 启动 `run_generate_job(...)`
4. 后端在构建 payload、等待上游 API、解析 JSON、提取图片数据、解码 `b64_json`、校验字节、保存文件和更新 Gallery 元数据时持续上报细分阶段
5. 图片数据从 base64 解码或从 URL 下载
6. 后端保存文件并写入 Gallery 元数据
7. 前端轮询 `/api/generate/{job_id}` 直到成功或失败，并在 Preview 中渲染当前阶段

### 编辑流程

1. 前端让用户上传任意图片文件
2. 前端将上传图片和当前参数发送到 `/api/edits`
3. 后端创建内存任务并调用上游 `/v1/images/edits`
4. 上传图片以 multipart `image` 字段转发
5. 支持的参数以 multipart 字段转发：`prompt`、`model`、`n`、`size`、`quality`、`output_format`、可选的 `response_format`，以及适用时的 `output_compression`
6. 后端在构建 multipart 表单、上传源图片、等待上游 API、解析 JSON、提取编辑图片数据、解码 `b64_json`、校验字节和保存文件时持续上报细分阶段
7. 返回图片数据从 base64 解码或从 URL 下载
8. 后端保存编辑后的图片并写入 Gallery 元数据
9. 前端轮询 `/api/generate/{job_id}`，并像普通生成一样渲染预览和 Gallery

## 技术栈

- Python 3.11+
- FastAPI
- Uvicorn
- httpx
- aiofiles
- Pydantic v2
- Pillow
- Tailwind CSS 与 Font Awesome（CDN）

## 项目结构

```text
LICENSE
README.md
README_ZH.md
Dockerfile
docker-compose.yml
.env.example
requirements.txt
app/
  __init__.py
  config.py
  main.py
  models.py
  proxy.py
  storage.py
static/
  index.html
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
  -p 9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

如果解析 `python:3.11-slim` 时 Docker Hub 超时，可以改用可访问的镜像源：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  -t gpt-image-panel .
```

### Docker Compose

```bash
cp .env.example .env
# 按需修改 .env
docker-compose up -d --build --force-recreate
```

如果 Compose 构建时 Docker Hub 超时，先在 `.env` 里设置：

```bash
PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
```

### 本地开发

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9090 --reload
```

然后打开 `http://localhost:9090`。

### 健康检查

```bash
curl http://localhost:9090/health
```

## 使用方法

1. 打开网站
2. 点击右上角齿轮图标
3. 选择已有预设，或点击 New 新建预设
4. 填写 API Base URL
5. 选择 API Path
6. 填写 API Key
7. 点击 Save Preset
8. 输入提示词
9. 选择生成参数
10. 点击 Generate
11. 也可以点击 Upload 选择图片，再点击 Edits 执行图生图
12. 查看预览和 Gallery

## 支持的 API Path

面板支持以下上游路径：

### `/v1/images/generations`

- 向 Images API 发送生成请求
- 从 `data[]` 读取图片数据

### `/v1/responses`

- 向 Responses API 发送生成请求
- 使用 `image_generation` 工具
- 从 `output[]` 中类型为 `image_generation_call` 的项目读取 base64 图片数据
- 界面中选择的图像模型会传给该工具
- Responses 顶层模型默认为 `gpt-5.4`，可通过 `DEFAULT_RESPONSES_MODEL` 修改

### `/v1/images/edits`

- 上传图片后点击 Edits 使用
- 始终在配置的 API Base URL 下调用 `/v1/images/edits`
- 使用 multipart/form-data 发送 `image` 和支持的编辑参数
- 如果上游返回 `404`、`405` 或 `501`，界面会提示 `/v1/images/edits` 不受支持并停止编辑请求

## 图像尺寸模式

- `auto` — 让模型自动选择输出尺寸
- 比例预设 — 1K / 2K / 4K，支持比例 `1:1`、`4:3`、`3:4`、`16:9`、`9:16`、`21:9`
- 自定义宽高 — 会归一化到 16 的倍数，最大边 `3840px`，最大纵横比 `3:1`，像素总量在 `655360` 到 `8294400` 之间

## 生成选项

- Quality：`auto`、`low`、`medium`、`high`
- Format：`PNG`、`JPEG`、`WebP`
- Compression：`PNG` 不可用；`JPEG` 和 `WebP` 可设置 `0-100`
- Quantity：`1` 到 `10`
- Response Format：`b64_json`、`url` 或 `none`；`none` 会省略 `response_format` 参数

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_API_URL` | 空 | 预填 API Base URL |
| `DEFAULT_API_KEY` | 空 | 预填 API Key |
| `DEFAULT_API_PATH` | `/v1/images/generations` | 默认上游路径 |
| `DEFAULT_RESPONSES_MODEL` | `gpt-5.4` | 调用 `/v1/responses` 时使用的顶层模型 |
| `ACCESS_KEY` | 空 | 站点访问密钥；设置后每个非健康路由均需解锁 |
| `IP_ALLOWLIST` | 空 | 允许访问的 IP/CIDR，逗号分隔 |
| `TRUST_PROXY_HEADERS` | `false` | 是否读取受信任反向代理的 `X-Forwarded-For` 或 `X-Real-IP` |
| `MAX_FILE_SIZE_MB` | `50` | 图片最大体积（MB） |
| `IMAGES_DIR` | `./images` | 图片存储目录 |
| `DATA_DIR` | `./data` | Gallery 元数据目录 |
| `PYTHON_BASE_IMAGE` | `python:3.11-slim` | Docker 构建基础镜像；Docker Hub 慢或不可访问时可覆盖 |

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 前端页面 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/access/status` | 访问密钥会话状态 |
| `POST` | `/api/access` | 解锁访问 3 小时 |
| `POST` | `/api/settings` | 保存当前 API 预设 |
| `GET` | `/api/settings` | 获取当前设置和预设列表 |
| `POST` | `/api/settings/presets` | 新建并激活 API 预设 |
| `POST` | `/api/settings/presets/{preset_id}/activate` | 激活 API 预设 |
| `DELETE` | `/api/settings/presets/{preset_id}` | 删除 API 预设 |
| `POST` | `/api/generate` | 创建图像生成任务 |
| `POST` | `/api/edits` | 使用 multipart 图片上传创建图像编辑任务 |
| `GET` | `/api/generate/{job_id}` | 查询任务状态或结果 |
| `GET` | `/api/gallery` | 分页查询 Gallery 图片 |
| `GET` | `/api/image/{filename}` | 访问图片文件 |
| `GET` | `/api/download/{filename}` | 下载图片 |
| `DELETE` | `/api/gallery/{id}` | 删除 Gallery 条目 |
| `GET` | `/api/download-all` | 下载 Gallery 所有图片为 ZIP 文件 |

## 运行时注意事项

- API 预设持久化保存在 `data/settings.json`。
- 如果 `data/settings.json` 不存在，默认预设会使用 `DEFAULT_API_URL`、`DEFAULT_API_KEY` 和 `DEFAULT_API_PATH` 初始化。
- API Key 在界面中掩码展示，但会以明文保存到 `data/settings.json`。
- 当任务数量超过 `MAX_GENERATE_JOBS` 时，已结束任务会被裁剪。
- `DELETE /api/gallery/{image_id}` 仅删除元数据，不会删除磁盘上的图片文件。
- 图片流式返回依赖已打开的文件句柄，修改相关逻辑时需注意资源释放。

## 测试

仓库目前没有测试套件。

## 贡献

欢迎贡献。

建议遵循以下原则：

- 后端修改尽量保持简单和明确
- 尽量使用 `app/models.py` 中的 FastAPI 响应模型
- 持久化文件操作集中在 `app/storage.py`
- 上游 API 调用集中在 `app/proxy.py`
- 除非明确要求，否则不要引入前端构建系统
- 不要在仓库文件中保存真实 API Key
- 除非明确要求，否则不要提交生成图片或运行时 Gallery 元数据
- 除非明确要求改变任务生命周期，否则保留现有异步生成与轮询机制

## 许可证

本项目采用 `CC BY-NC 4.0` 许可证，即 `Creative Commons Attribution-NonCommercial 4.0 International`。

- 允许任何人使用、复制、修改、再分发以及二次创作。
- 需要保留署名，并附带许可证说明。
- 不允许将本项目或其衍生作品用于商业用途。
- 如需商业使用，必须事先获得著作权人的许可。

许可证全文见 [LICENSE](./LICENSE)。
