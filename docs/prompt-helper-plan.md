# Prompt Helper 落地计划

状态：待实现  
目标版本：下一次功能迭代  
创建日期：2026-05-20

## 目标

把“提示词增强与复用工具”落成三个可交付能力：

1. 在生成表单旁提供分类提示词标签，用户点击即可把常用修饰词追加到当前 Prompt。
2. 增加服务端代理的 AI Prompt Optimizer，用低成本文本模型把短构想改写为更适合生图的长 Prompt。
3. 在图库卡片和 Lightbox 中支持把历史图片 Prompt/参数回填到生成表单，并尽量复用 `Size`、`Model`、`Quality`、`API Path`。

核心约束：

- 不让浏览器直连外部优化器 API，优化器 API key 只存在后端配置/SQLite/env-ref 中。
- 不破坏现有 `/api/generate`、`/api/edits`、SSE、Gallery、Job History 的响应结构；只做向后兼容字段扩展。
- 复用现有 Settings、SQLite、Svelte store、i18n、contract/e2e 测试风格。
- 实施完成后同步更新 `README.md`、`.env.example`、必要时 `docker-compose.yml`。

## 当前代码基线

相关现状：

- 主页面状态在 `frontend/src/routes/+page.svelte`，`form: PromptFormState` 是生成/编辑表单的单一数据源。
- `PromptForm.svelte` 负责 Prompt、model、size、quality、format、quantity 等输入。
- `GalleryEntry` 已包含 `prompt`、`size`、`model`、`quality`、`output_format`、`output_compression`、`response_format`、`n`、`api_path`、`api_preset_name`。
- `Lightbox.svelte` 已有 `onCopyPrompt`，但只是复制，不回填生成表单。
- `JobHistoryDrawer` 已有“复用任务参数”，实现工具是 `frontend/src/lib/utils/promptForm.ts::jobToPromptForm`。
- 当前生成请求的 `api_path` 来自后端 active preset；前端生成表单不能逐次选择 API Path。
- Settings 目前只管理图像 API preset 和全局 SOCKS5 proxy。

关键落点：

- 前端：`PromptForm.svelte`、`GalleryGrid.svelte`、`Lightbox.svelte`、`+page.svelte`、`preview.ts`、`promptForm.ts`、`api/types.ts`、`i18n.ts`
- 后端：`schemas/models.py`、`api/routers/settings.py`、新增 `api/routers/prompt.py`、`api/routers/__init__.py`、`api/presets.py`、`repositories/storage.py`、新增 `integrations/prompt_optimizer_client.py`、`core/settings.py`
- 文档/配置：`README.md`、`.env.example`、`docker-compose.yml`
- 测试：`backend/tests/test_contract.py`、`frontend/tests/e2e/experience.spec.ts`

## 产品行为

### 1. 提示词分类标签

位置：

- 桌面端：`PromptForm` 内右侧 helper panel，占窄列；Prompt textarea 和参数区保持主列。
- 移动端：Prompt textarea 下方折叠/横向滚动标签区，避免挤压主表单。

初始分类：

| 分类 | 标签例子 | 插入值策略 |
|---|---|---|
| 艺术画质 | high detail, ultra sharp, clean composition | 插入英文短语 |
| 画风 | cinematic, watercolor, editorial, 3D render | 插入英文短语 |
| 镜头构图 | macro shot, close-up, wide angle, overhead view | 插入英文短语 |
| 光影效果 | soft rim light, golden hour, volumetric lighting | 插入英文短语 |
| 色彩氛围 | muted palette, vibrant colors, warm tone | 插入英文短语 |
| 材质细节 | glass texture, brushed metal, fabric fibers | 插入英文短语 |

交互规则：

- 点击标签时，把标签值追加到 `form.prompt`。
- 追加分隔符：如果当前 Prompt 为空，直接写入；否则追加 `, ${tag.value}`。
- 同一标签已存在时不重复追加，直接 toast “已存在”。
- 支持“清空标签追加”不做，避免误删用户 Prompt。
- 标签 label 跟随 UI 语言；实际插入值默认英文，因为大多数生图模型对英文修饰词更稳定。

建议文件：

- 新增 `frontend/src/lib/prompt/tags.ts`
- 新增 `frontend/src/lib/components/PromptHelperPanel.svelte`
- `PromptForm.svelte` 引入 helper panel，并暴露 `onOptimize`、`optimizing`、`optimizerEnabled` props。

### 2. AI Prompt Optimizer

入口：

- Prompt textarea 右上/下方增加 `Optimize` 按钮。
- 按钮禁用条件：
  - Prompt 为空
  - 优化器未启用或未配置
  - 正在优化
  - 当前生成/编辑提交正在 loading

默认行为：

1. 用户输入短 Prompt。
2. 点击 `Optimize`。
3. 前端调用 `POST /api/prompt/optimize`。
4. 成功后用 `optimized_prompt` 替换 textarea。
5. toast 显示“Prompt optimized”，并提供一次性 Undo：恢复优化前文本。

请求建议：

```json
{
  "prompt": "a tiny robot in a kitchen",
  "target_language": "en",
  "api_path": "/v1/images/generations",
  "model": "gpt-image-2",
  "size": "1024x1024",
  "quality": "high"
}
```

响应建议：

```json
{
  "optimized_prompt": "A tiny friendly robot standing on a kitchen counter, detailed product-style composition, soft morning window light, clean background, sharp focus, high detail...",
  "model": "gpt-4o-mini",
  "duration_ms": 842
}
```

优化器后端契约：

- 新增 `POST /api/prompt/optimize`
- 同源、受现有 access gate / CSRF / IP allowlist 保护。
- 同步请求，不进入图像生成队列，不占用 `MAX_ACTIVE_GENERATE_JOBS`。
- 超时时间独立配置，默认 20 秒。
- 输入 Prompt 最大 4000 字符，输出最大 4000 字符，保证能回填现有 textarea。
- 后端日志不得记录完整 Prompt，只记录状态码、耗时、错误类型。

优化器系统提示词策略：

- 保留用户原始意图，不改变主体。
- 补全画面主体、风格、构图、光影、材质、背景、质量描述。
- 输出单段 Prompt，不带 Markdown、解释、编号。
- 默认输出英文；用户可后续扩展为 same-language/中文。
- 不生成负面提示词字段，因为当前表单没有 negative prompt 概念。

优化器配置：

采用“完整 Chat Completions 兼容 endpoint URL”的方式，而不是复用图像 API preset。

原因：

- OpenAI：`https://api.openai.com/v1/chat/completions`
- Gemini OpenAI-compatible：`https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`
- 第三方兼容服务也常直接给完整 endpoint。
- 这样不用把 optimizer 限死在当前图像 API 的 `/v1/*` path 规则里。

新增环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PROMPT_OPTIMIZER_ENABLED` | `false` | 是否启用优化器 |
| `PROMPT_OPTIMIZER_API_URL` | empty | Chat Completions 兼容完整 endpoint URL |
| `PROMPT_OPTIMIZER_API_KEY` | empty | 优化器 key；Settings 中建议使用 `${ENV_VAR}` 引用 |
| `PROMPT_OPTIMIZER_MODEL` | `gpt-4o-mini` | 优化器模型 |
| `PROMPT_OPTIMIZER_TIMEOUT_SECONDS` | `20` | 优化请求超时 |
| `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS` | `4000` | 输出长度上限 |
| `PROMPT_OPTIMIZER_HOST_ALLOWLIST` | empty | 可选 host allowlist；为空时复用现有 SSRF DNS/private-IP 校验 |

Settings UI：

- 在 `SettingsDrawer.svelte` 新增 `Prompt Optimizer` section。
- 字段：Enabled、Endpoint URL、Model、API Key。
- API Key 行为复用现有 preset key 规则：
  - 可填 literal key，SQLite 明文保存。
  - 可填 `${OPENAI_API_KEY}` 这种 env ref，SQLite 只保存引用。
  - `/api/settings` 返回 masked value，不返回真实 key。

### 3. Gallery Prompt 复用

新增操作：

- Gallery card：
  - `Use prompt`：复制 Prompt 到剪贴板，并填入生成输入框。
  - `Use all`：复制 Prompt，并回填 Prompt + Size + Model + Quality + API Path + Format + Compression + Response Format + Quantity。
- Lightbox：
  - 保留现有 `Copy prompt`。
  - 新增 `Use prompt` 和 `Use all`，或把 `Copy prompt` 改为 `Use prompt` 但保留复制行为。

回填规则：

- `Use prompt`
  - `form.prompt = image.prompt`
  - best-effort `navigator.clipboard.writeText(image.prompt)`
  - 不改 size/model/quality/apiPath

- `Use all`
  - `prompt = image.prompt`
  - `size = image.size || initialPromptFormState.size`
  - `model = image.model || lastActivePresetDefaultModel`
  - `quality = normalizeJobQuality(image.quality)`
  - `outputFormat = normalizeJobOutputFormat(image.output_format)`
  - `outputCompression = image.output_compression != null ? String(image.output_compression) : ''`
  - `responseFormat = image.response_format === 'url' || 'b64_json' ? image.response_format : ''`
  - `quantity = clamp(image.n || 1, 1, 10)`
  - `apiPath = image.api_path` 仅当它是生成路径：
    - `/v1/images/generations`
    - `/v1/responses`
    - `/v1/chat/completions`
  - 如果 `image.api_path === '/v1/images/edits'`，保留当前 `form.apiPath`，toast 提示“该图片来自编辑接口，生成 API Path 保持当前值”。

为什么要给表单增加 API Path：

- 当前 API Path 是 Settings preset 级别，Gallery 复用无法逐次复用历史路径。
- 若通过自动切换 active preset 来复用 API Path，会产生隐式全局副作用。
- 更稳的做法是给生成请求增加可选 `api_path` 字段；后端仍使用 active preset 的 URL/key，只让每次生成选择允许的上游 path。

后端兼容扩展：

- `GenerateRequest` 新增可选字段：

```py
api_path: Optional[ApiPath] = None
```

- `queue_image_job(... operation="generation")` 解析顺序：
  1. 如果 `req.api_path` 存在，使用 `normalize_api_path(req.api_path)`。
  2. 否则使用 active preset 的 `api_path`。
  3. `model` 默认值仍按 effective api_path 计算。

- `EditRequest` 不从 multipart form 接收 `api_path`；编辑继续固定走 `/v1/images/edits`。
- Gallery/job metadata 继续保存 effective `api_path`。

前端表单变化：

- `PromptFormState` 新增：

```ts
apiPath: ApiPath;
```

- `PromptForm.svelte` 增加 API Path select；默认值来自 active preset。
- `promptOnlyMode` 改为基于 `form.apiPath`，不再直接看 `$settingsStore.settings?.api_path`。
- `buildRequestBody(form)` 对生成请求写入 `api_path: form.apiPath`。
- 编辑请求不把 `api_path` append 进 `FormData`，避免误导编辑接口。

## 数据结构与接口改动

### 后端 DTO

修改 `backend/app/schemas/models.py`：

```py
class PromptOptimizerSettingsResponse(BaseModel):
    enabled: bool = False
    api_url: str = ""
    model: str = "gpt-4o-mini"
    api_key_masked: str = "***"
    has_api_key: bool = False
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None

class PromptOptimizerSettingsRequest(BaseModel):
    enabled: bool = False
    api_url: str = ""
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None

class PromptOptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    target_language: Literal["en", "zh-CN", "same"] = "en"
    api_path: Optional[ApiPath] = None
    model: Optional[str] = None
    size: Optional[str] = None
    quality: Optional[Literal["auto", "low", "medium", "high"]] = None

class PromptOptimizeResponse(BaseModel):
    optimized_prompt: str
    model: str
    duration_ms: int
```

扩展：

```py
class SettingsRequest(BaseModel):
    ...
    prompt_optimizer: Optional[PromptOptimizerSettingsRequest] = None

class SettingsResponse(BaseModel):
    ...
    prompt_optimizer: PromptOptimizerSettingsResponse

class GenerateRequest(BaseModel):
    ...
    api_path: Optional[ApiPath] = None
```

### 后端 settings 持久化

修改 `backend/app/repositories/storage.py`：

- 新增 `PROMPT_OPTIMIZER_SETTINGS_KEY = "prompt_optimizer_settings"`。
- `_default_settings()` 增加 `prompt_optimizer` 默认值，来自 env。
- `_normalize_settings()` 接收并规范化 `prompt_optimizer`。
- `_replace_settings_on_conn()` 把 `prompt_optimizer` JSON 写到 `settings_kv`。
- `_load_settings_from_conn()` 从 `settings_kv` 读 `prompt_optimizer`，没有则用 env 默认。
- 不需要新表；settings_kv 足够。

修改 `backend/app/api/presets.py`：

- 将 `mask_key`、`get_api_key_env_var`、`resolve_api_key`、`api_key_response_fields` 泛化为可复用 secret helper，或继续放在该模块但让 prompt optimizer 复用。
- 新增：
  - `get_prompt_optimizer_settings()`
  - `apply_prompt_optimizer_settings()`
  - `resolve_prompt_optimizer_api_key()`
  - `build_prompt_optimizer_settings_response()`

### 后端 optimizer client

新增 `backend/app/integrations/prompt_optimizer_client.py`：

职责：

- 校验 endpoint URL：
  - `https://`
  - 必须有 hostname
  - 不允许 query/fragment
  - 通过 SSRF 校验；host allowlist 使用 `PROMPT_OPTIMIZER_HOST_ALLOWLIST`
- 构造 Chat Completions 请求：

```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0.4,
  "max_tokens": 900,
  "stream": false
}
```

- 解析 `choices[0].message.content`。
- 对返回文本做 trim、去掉 Markdown fence、截断或报错。
- 使用 `session_pool.get_pool()`，timeout kind 可以新增 `TIMEOUT_PROMPT_OPTIMIZER`，或在 session_pool 支持自定义 timeout。

### 新增 router

新增 `backend/app/api/routers/prompt.py`：

```py
@router.post("/api/prompt/optimize", response_model=PromptOptimizeResponse)
async def optimize_prompt(req: PromptOptimizeRequest):
    ...
```

错误码：

- `400`：optimizer disabled / URL missing / key missing / model missing
- `422`：请求字段非法
- `502`：上游返回错误、非 JSON、缺少 content
- `504`：上游超时

注册：

- `backend/app/api/routers/__init__.py` 导入并加入 `routers`，放在 `static.router` 之前。

## 前端实现细节

### Types

修改 `frontend/src/lib/api/types.ts`：

- `GenerateRequestBody` 新增 `api_path?: ApiPath | null`
- `PromptFormState` 对应新增 `apiPath`
- `SettingsResponse` 新增 `prompt_optimizer`
- 新增 `PromptOptimizeRequest` / `PromptOptimizeResponse` 类型

### Store / Utils

修改 `frontend/src/lib/stores/preview.ts`：

- `initialPromptFormState.apiPath = '/v1/images/generations'`
- `buildRequestBody(form)` 给生成请求带上 `api_path`
- `editImage()` 构造 FormData 时跳过 `api_path`

修改 `frontend/src/lib/utils/promptForm.ts`：

- 新增 `normalizeApiPath(value)`。
- `jobToPromptForm()` 填充 `apiPath`。
- 新增 `galleryEntryToPromptForm(image, fallbackModel, currentApiPath)`。

修改 `frontend/src/routes/+page.svelte`：

- `syncFormModelToActivePreset()` 同步 active preset 时也同步默认 `form.apiPath`，但只在用户未手动改过或仍等于旧默认值时同步。
- 新增：
  - `appendPromptTag(tagValue: string)`
  - `optimizePrompt()`
  - `useGalleryPrompt(image)`
  - `useGalleryParams(image)`
- Gallery/Lightbox callbacks 接入上述方法。

### PromptForm

修改 `frontend/src/lib/components/PromptForm.svelte`：

- 移除外部 `apiPath` prop，直接使用 `form.apiPath`。
- 增加 API Path select。
- 增加 `Optimize` 按钮。
- 嵌入 `PromptHelperPanel.svelte`。
- `promptOnlyMode` 根据 `form.apiPath` 禁用 size/quality/format/quantity/responseFormat。

### GalleryGrid

修改 `frontend/src/lib/components/GalleryGrid.svelte`：

- props 新增：
  - `onUsePrompt: (image: GalleryEntry) => void`
  - `onUseAll: (image: GalleryEntry) => void`
- 每张卡片操作区新增两个小按钮。
- 按钮 click 复用 `handleGalleryAction(event, ...)`，避免触发 open lightbox。

### Lightbox

修改 `frontend/src/lib/components/Lightbox.svelte`：

- props 新增：
  - `onUsePrompt`
  - `onUseAll`
- 底部 action grid 加按钮。
- 保留 `onCopyPrompt`，避免已有用户习惯断掉。

### i18n

修改 `frontend/src/lib/i18n.ts`：

新增 keys：

- `common.usePrompt`
- `common.useAllParams`
- `promptForm.optimize`
- `promptForm.optimizing`
- `promptForm.apiPath`
- `promptHelper.title`
- `promptHelper.categories.*`
- `settings.promptOptimizer`
- `settings.promptOptimizerEnabled`
- `settings.promptOptimizerApiUrl`
- `settings.promptOptimizerModel`
- `settings.promptOptimizerApiKey`
- `messages.promptOptimized`
- `messages.promptOptimizeFailed`
- `messages.promptTagExists`
- `messages.galleryPromptLoaded`
- `messages.galleryParamsLoaded`
- `messages.galleryEditApiPathIgnored`

## 安全与边界

- 优化器 key 不进入前端 response；只返回 masked/env-ref metadata。
- 优化器调用只在后端发起，并走现有 access/CSRF/IP allowlist。
- endpoint URL 必须 SSRF 校验，禁止私网/loopback，除非未来明确添加 allowlist 豁免。
- 不记录完整 prompt，不把 optimizer 请求写入 SQLite。
- Optimizer 不共享图像生成队列，避免文本优化阻塞生图任务。
- 生成请求新增 `api_path` 只能取 `ApiPath` Literal 中的三个生成路径。
- 编辑接口继续固定 `/v1/images/edits`，不支持通过 Prompt Helper 改编辑 path。

## 测试计划

### Backend contract tests

在 `backend/tests/test_contract.py` 增加：

1. `GET /api/settings` 返回 `prompt_optimizer`，不泄露 literal key。
2. `POST /api/settings` 可保存 optimizer 配置，masked key preserve/clear/env-ref 行为正确。
3. `POST /api/prompt/optimize` 在 disabled 时返回 `400`。
4. mock optimizer 上游成功时返回 `optimized_prompt`。
5. mock optimizer 上游：
   - 401/500 -> `502`
   - 非 JSON -> `502`
   - JSON 缺少 `choices[0].message.content` -> `502`
   - timeout -> `504`
6. optimizer endpoint URL 的 SSRF/非 HTTPS 校验。
7. `/api/generate` 请求带 `api_path=/v1/responses` 时，job/gallery metadata 使用该 path。
8. `/api/edits` 不接受/不使用 `api_path`，metadata 仍是 `/v1/images/edits`。

### Frontend e2e

在 `frontend/tests/e2e/experience.spec.ts` 增加：

1. 点击 Prompt tag 后 textarea 追加短语；重复点击不重复。
2. Optimize 按钮调用 `/api/prompt/optimize`，成功后替换 textarea。
3. Optimizer disabled 时按钮不可用或显示未配置状态。
4. Gallery card `Use prompt`：
   - 填入 Prompt
   - 不改变 model/size/quality/apiPath
5. Gallery card `Use all`：
   - 填入 Prompt、Size、Model、Quality、API Path
   - 下一次 `/api/generate` request body 包含对应 `api_path`
6. Lightbox `Use all` 同样工作，并关闭/保留 Lightbox 的行为符合设计。
7. `image.api_path === '/v1/images/edits'` 时不把表单 apiPath 改成编辑 path，并显示 toast。

### 手动验证

```bash
npm run frontend:check
npm run frontend:build
npm run test:contract
npm run test:e2e
```

如果只改 Prompt Helper，不触碰 Gallery 查询性能，不需要跑 perf；如果改了 Gallery 大列表按钮布局导致性能风险，再跑：

```bash
npm run test:perf
npm run test:e2e:perf
```

## 文档与配置更新

实施功能时必须更新：

- `.env.example`
  - 加入 `PROMPT_OPTIMIZER_*` 变量。
- `docker-compose.yml`
  - 加入可选 `PROMPT_OPTIMIZER_*` env 映射，尤其是 `PROMPT_OPTIMIZER_ENABLED/API_URL/API_KEY/MODEL`。
- `README.md`
  - Features：加入 Prompt Helper、AI Prompt Optimizer、Gallery 参数复用。
  - Usage：说明 Optimize、标签、Use prompt/Use all。
  - Environment variables：加入 optimizer 变量。
  - Endpoints：加入 `POST /api/prompt/optimize`。
  - Runtime notes：说明 optimizer key/env-ref、server-side call、不会进入生成队列。

本计划文件本身不修改 README，因为功能尚未实现；README 应在代码实现同一 PR/提交中同步更新，避免文档提前声明不存在的功能。

## 实施顺序

### Phase 1：后端 optimizer 配置与 API

1. 在 `core/settings.py` 加 `PROMPT_OPTIMIZER_*` 环境变量。
2. 在 `schemas/models.py` 加 optimizer settings/request/response DTO。
3. 在 `storage.py` 用 `settings_kv` 持久化 `prompt_optimizer_settings`。
4. 在 `api/presets.py` 增加 optimizer settings 序列化、key mask/env-ref 解析。
5. 在 `settings.py` router 的 get/update settings 中带上 optimizer 配置。
6. 新增 `prompt_optimizer_client.py`。
7. 新增 `routers/prompt.py` 和 `/api/prompt/optimize`。
8. 写 backend contract tests。

验收：

- Settings 能保存/读取 optimizer。
- Optimizer disabled/missing config 有明确错误。
- mock 上游成功能返回 optimized prompt。
- 响应不泄露 key。

### Phase 2：生成 API Path 逐次覆盖

1. `GenerateRequest` 增加 `api_path`。
2. `queue_image_job` 对 generation 使用 request override。
3. 保证 edit job 仍固定 `/v1/images/edits`。
4. 更新 backend tests，覆盖 metadata。

验收：

- 不带 `api_path` 的旧请求行为不变。
- 带合法 `api_path` 的生成请求会使用该 path。
- 非法 path 被 Pydantic 拒绝或 normalize 后退回默认，按最终实现写测试。

### Phase 3：前端表单状态与 optimizer 调用

1. 扩展 `ApiPath`/request/response types。
2. `PromptFormState` 加 `apiPath`。
3. `PromptForm` 加 API Path select 和 Optimize 按钮。
4. `+page.svelte` 加 `optimizePrompt()`，处理 loading、错误 toast、undo。
5. Settings drawer 加 optimizer section。
6. 更新 i18n。

验收：

- 前端 type check 通过。
- Optimize mock 成功后 textarea 替换。
- API Path select 会影响 `/api/generate` request body。

### Phase 4：提示词标签 UI

1. 新增 `prompt/tags.ts`。
2. 新增 `PromptHelperPanel.svelte`。
3. 接入 `PromptForm.svelte`。
4. 补 e2e：追加、去重、移动端布局基本可见。

验收：

- 标签点击可追加。
- 重复标签不重复。
- 不影响现有 Generate/Edit 提交流程。

### Phase 5：Gallery / Lightbox 复用

1. `promptForm.ts` 新增 `galleryEntryToPromptForm`。
2. `GalleryGrid` 加 `Use prompt` / `Use all`。
3. `Lightbox` 加 `Use prompt` / `Use all`。
4. `+page.svelte` 接入回填逻辑。
5. 补 e2e 覆盖 Gallery card 和 Lightbox。

验收：

- `Use prompt` 只改 prompt。
- `Use all` 回填 Prompt + 参数。
- 生成路径 row 可以复用 `api_path`。
- 编辑路径 row 不会把 `/v1/images/edits` 填到生成 API Path。

### Phase 6：文档、配置、最终验证

1. 更新 `.env.example`。
2. 更新 `docker-compose.yml`。
3. 更新 `README.md` 英文/中文两段。
4. 跑验证命令：

```bash
npm run frontend:check
npm run frontend:build
npm run test:contract
npm run test:e2e
```

验收：

- 所有相关测试通过。
- `git status --short` 不包含 runtime/generated artifacts。
- README 与实际功能一致。

## 可推迟的增强

这些不进首版，避免范围膨胀：

- 用户自定义标签库持久化。
- Prompt 模板变量，例如 `{subject}`、`{style}`。
- Prompt A/B 版本历史。
- Optimizer 多 provider 原生协议适配；首版只支持 Chat Completions 兼容接口。
- 批量对 Gallery 旧 Prompt 做优化。
- Negative prompt 独立字段；当前上游契约没有稳定字段。

## 完成定义

功能完成必须同时满足：

- 用户能在 PromptForm 里用标签快速追加修饰词。
- 用户能配置 optimizer，并通过 Optimize 把短 Prompt 改写成长 Prompt。
- 用户能从 Gallery card 和 Lightbox 一键回填 Prompt 或完整参数。
- 复用完整参数时，生成请求能带上正确 `api_path`。
- API key 不泄露到前端响应。
- Backend contract、frontend check/build、e2e 全部通过。
- README、`.env.example`、`docker-compose.yml` 已同步。
