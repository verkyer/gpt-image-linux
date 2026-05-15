# 大图 ZIP 导出流式化方案

## 结论

当前 ZIP 导出不是流式响应。实现路径是先把完整 ZIP 写入临时文件，再用 `FileResponse` 返回：

- `backend/app/api/gallery_archive.py::build_gallery_zip_file()`
- `backend/app/api/routers/gallery.py::_gallery_zip_response()`

目标改成：

```text
数据库查出 entries -> 生成 streaming ZIP iterator -> StreamingResponse 边生成边发送
```

主要收益：

- 不再为完整 ZIP 占用临时磁盘空间。
- 不必等待全量 deflate 完成才开始向客户端发送字节。
- 客户端中断时不存在悬挂的 temp 文件需要 `BackgroundTask` 清理。

注意：**首字节延迟不会同比例降低**。`metadata.json` 需要包含每张图的 sha256，所以发送第一个 chunk 之前仍要扫一遍全部图片算 hash（详见"风险点 / metadata 生成时机"）。

## 非目标

- 不改变 `/api/download-all` 和 `/api/gallery/batch/download` 的 API 路径。
- 不改变 ZIP 内部结构：
  - 图片仍在 `images/<filename>`。
  - 元数据仍是 `metadata.json`。
- 不改变 import ZIP 格式。
- 不在本次引入异步文件 IO 库。
- 不为了流式化重写 gallery/storage 数据模型。

## 推荐实现

使用 `zipstream-ng`。

原因：

- 专门支持 streaming ZIP。
- 不需要先落完整临时文件。
- 支持逐文件迭代生成 ZIP bytes。
- 比自己手写 ZIP central directory 更可靠。
- 比标准库 `zipfile` 直接写 non-seekable wrapper 更容易测试和维护。

**注意包名/模块名陷阱**：PyPI 上同时存在两个包：

- `zipstream`（旧的 `python-zipstream`，已停止维护，import 名 `zipstream`）
- `zipstream-ng`（活跃维护的 fork，import 名 `zipstream_ng`）

本方案使用 `zipstream-ng`，因此 import 必须写成 `from zipstream_ng import ZipStream`，不要写 `import zipstream`，否则在没装旧包的机器上 ImportError，在两个都装了的机器上会拿到错误的实现。

新增依赖：

```text
zipstream-ng>=1.8.0,<2.0.0
```

加到根目录 `requirements.txt`。

## 设计细节

### 1. 新增 streaming builder

在 `backend/app/api/gallery_archive.py` 新增（注意 `Iterator` 已在文件顶部 import 过，不要重复声明）：

```python
from zipstream_ng import ZipStream


def iter_gallery_zip_chunks(entries: list[GalleryEntry]) -> Iterator[bytes]:
    used_names: set[str] = set()
    exported_entries: list[GalleryEntry] = []
    # images 已经是压缩格式，统一 STORED 避免白吃 CPU；metadata 走 deflate。
    zs = ZipStream(compress_type=zipfile.ZIP_STORED)

    for entry in entries:
        path = storage.safe_image_path(entry.filename)
        if not path or not path.exists():
            continue

        name = unique_export_name(path, used_names)
        exported_entries.append(entry.model_copy(update={"filename": name}))
        zs.add_path(path, arcname=f"images/{name}")

    metadata = build_gallery_export_metadata(exported_entries)
    zs.add(
        json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        arcname="metadata.json",
        compress_type=zipfile.ZIP_DEFLATED,
    )

    yield from zs
```

实际代码里建议拆出 helper：

```python
def unique_export_name(path: Path, used_names: set[str]) -> str:
    name = path.name
    base = path.stem
    ext = path.suffix
    counter = 1
    while name in used_names:
        name = f"{base}_{counter}{ext}"
        counter += 1
    used_names.add(name)
    return name
```

注意：

- `metadata.json` 必须在所有有效图片筛选完成后再加入，因为 metadata 需要包含去重后的 filename 和 sha256。
- `add` / `add_path` 的归档名必须用 `arcname=` kwarg，避免与位置参数歧义。
- 生成器创建（`iter_gallery_zip_chunks(entries)`）本身是 O(1)，所有 I/O 推迟到 iteration。**不要把 `storage.get_gallery()` 这类查询塞进生成器内部**，否则查询会跑到 threadpool 里，丢失明确的执行边界。
- 第一次 `next()` 才会执行循环 + sha256 计算 + 第一个 chunk 的 deflate；后续 `next()` 才是真正的流式输出。
- **per-file 压缩策略**：`ZIP_STORED` 用于 images（PNG/JPEG/WebP 已经是压缩格式，二次 deflate 几乎不省空间还消耗 CPU），`ZIP_DEFLATED` 用于 metadata.json。这同时是流式化能稳定上线的前提条件——streaming 的 CPU 占用会比 temp 文件方式更长时间在线，更容易在并发请求下叠加。

### 2. 保留旧 builder 作为短期兼容

先不要立刻删除 `build_gallery_zip_file()`。

第一轮保留它，原因：

- 降低 diff 风险。
- 方便测试对比 streaming ZIP 与旧 ZIP 的内容。
- 如果某些客户端对 streaming ZIP 有兼容问题，可以快速回滚。

等一两个版本稳定后再删。

### 3. 路由改为 StreamingResponse

在 `backend/app/api/routers/gallery.py` 中：

```python
from fastapi.responses import FileResponse, StreamingResponse
```

把 `_gallery_zip_response()` 改成同步生成器的响应包装：

```python
def _zip_content_disposition(filename: str) -> str:
    return f'attachment; filename="{filename}"'


async def _gallery_zip_response(
    entries: list[GalleryEntry],
    filename_prefix: str,
) -> StreamingResponse:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    return StreamingResponse(
        iter_gallery_zip_chunks(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": _zip_content_disposition(filename),
            "X-Content-Type-Options": "nosniff",
        },
    )
```

不要设置 `Content-Length`。streaming ZIP 默认应该走 chunked transfer。

### 4. 线程与事件循环影响

`StreamingResponse` 会消费同步 iterator。Starlette/FastAPI 通过 `iterate_in_threadpool` 在线程池里调用 `next()`，避免直接卡住 event loop。

执行模型要点：

- 生成器函数被调用时只创建 generator 对象，不执行函数体。
- 第一次 `next()` 会跑到第一个 `yield` 之前的所有代码——也就是 `add_path` 循环 + `build_gallery_export_metadata`（含全量 sha256）。这部分 I/O 在线程池中执行，不阻塞 loop，但会推迟首字节。
- 此后每次 `next()` 才是真正逐 chunk 流出。

仍要注意：

- ZIP 压缩是 CPU 工作（已经按 per-file 把 images 改 STORED 缓解，但 metadata 仍 deflate）。
- 流式响应的 CPU 占用在线时间更长，并发导出比 temp 文件方式更容易在线程池/CPU 上叠加。
- 第一轮可以先不加并发限制。如果后续发现导出抢 CPU，再加一个导出 semaphore。

可选第二阶段：

```python
EXPORT_ZIP_CONCURRENCY = int(os.getenv("EXPORT_ZIP_CONCURRENCY", "2"))
```

但第一轮不建议加配置面，先保持改动小。

### 5. 文件变动清单

必须改：

- `requirements.txt`
- `backend/app/api/gallery_archive.py`
- `backend/app/api/routers/gallery.py`
- `backend/tests/test_contract.py`

可能改：

- `README.md`

README 只有在用户可见行为明显变化时更新。这里 API 不变，但“大图库导出不再占用完整临时 ZIP 空间”属于部署/性能行为变化，建议在 README 的功能或开发说明里补一句。

## 测试方案

### 1. 保留现有契约测试

这些测试必须继续通过：

```bash
python3 -m pytest backend/tests/test_contract.py -q
```

现有测试会用：

```python
zipfile.ZipFile(io.BytesIO(archive.content))
```

验证 ZIP 内容。StreamingResponse 在 TestClient 里仍会收集完整 body，所以这类测试无需大改。

### 2. 新增响应头测试

在 `test_gallery_image_download_and_zip()` 或新测试里断言：

```python
archive = client.get("/api/download-all")
assert archive.status_code == 200
assert archive.headers["content-type"].startswith("application/zip")
assert "attachment" in archive.headers["content-disposition"]
assert "content-length" not in archive.headers
```

注意：

- TestClient 不走真实 HTTP，**不要**断言 `transfer-encoding == chunked`。
- 如果 TestClient/transport 自动补 `content-length`，把这条断言改成只在真实 ASGI smoke test 里验证，不要在契约测试里硬断。

### 3. 新增无临时 ZIP 文件测试

目标是确认路由不再调用 `build_gallery_zip_file()`。Patch 源头模块（`gallery_archive`）比 patch 路由模块的 import 更鲁棒——未来如果路由删掉这个 import，patch 路由模块的写法会静默失效。

```python
def test_download_all_uses_streaming_zip(client, monkeypatch):
    _fake_gallery_entry("stream-zip", "stream", "1024x1024", "stream-zip.png")

    def boom(_entries):
        raise AssertionError("temporary ZIP builder should not be used")

    monkeypatch.setattr(
        "backend.app.api.gallery_archive.build_gallery_zip_file",
        boom,
        raising=True,
    )

    resp = client.get("/api/download-all")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "images/stream-zip.png" in zf.namelist()
```

等旧 builder 被彻底删除后，这个测试也可以删，靠 grep / import 检查防回归。

### 4. 新增重名文件测试

确保两个 gallery entry 指向同名文件时仍不会重复 arcname：

```python
with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
    names = zf.namelist()
    assert len(names) == len(set(names))
```

当前行为是 `same.png`、`same_1.png`。

### 5. 新增 metadata 测试

断言 streaming 后 metadata 仍然不包含 UI 字段：

```python
metadata = json.loads(zf.read("metadata.json"))
assert "thumbnail_filename" not in metadata["images"][0]
assert "thumbnail_url" not in metadata["images"][0]
assert metadata["images"][0]["sha256"]
```

### 6. 可选真实流式 smoke test

TestClient 会把 body 收完，不适合验证首字节延迟。

如果要验证真实 chunked 行为：

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 9090
curl -v -o /tmp/gallery.zip http://127.0.0.1:9090/api/download-all
```

期望：

```text
Transfer-Encoding: chunked
Content-Type: application/zip
Content-Disposition: attachment; filename="gpt-images-....zip"
```

## 回滚方案

保留旧 `_gallery_zip_response()` 的临时文件版本，必要时只需要把路由包装切回：

```python
temp_path = await asyncio.to_thread(build_gallery_zip_file, entries)
return FileResponse(
    temp_path,
    media_type="application/zip",
    filename=filename,
    background=BackgroundTask(remove_file, temp_path),
)
```

同时从 `requirements.txt` 移除 `zipstream-ng`。

## 风险点

### 客户端兼容性

大多数现代浏览器、curl、下载器都支持 chunked ZIP 下载。风险主要是非常老的代理或网关强依赖 `Content-Length`。

缓解：

- 不改 URL。
- 不改 ZIP 内容结构。
- 保留旧 builder 一段时间。
- 如果需要，后续加环境变量回退：

```text
STREAM_GALLERY_ZIP=true
```

但第一轮不建议加这个开关，除非线上环境有已知代理限制。

### 中途断连

流式响应中途断连时，ZIP 可能不完整。这个和任何下载中断一样，客户端重新下载即可。

好处是：

- 服务端不再需要生成完整临时 ZIP 后才发现客户端已经走了。
- **没有 temp 文件需要清理**，不存在 `BackgroundTask(remove_file, ...)` 的悬挂资源。Starlette 在客户端断连时会调用 generator 的 `aclose()`，触发 `GeneratorExit`，`zipstream-ng` 内部 `with open()` 打开的文件会被正常关闭。

### metadata 生成时机

metadata 现在需要先扫描所有 entry 才能知道有效文件和去重 filename。第一版 streaming 仍然会先做这一步，但不会读取图片内容进内存。

`build_gallery_export_metadata()` 会计算 sha256，因此在发送第一个 chunk 前仍要读一遍所有导出文件。若要进一步降低首字节延迟，需要做第二阶段优化：

- 去掉导出 metadata 里的 sha256；或
- 把 metadata 放最后并在添加图片时顺便计算 hash；或
- 提供 `include_hash=false` 之类的导出选项。

当前为了不改导出格式，保留 sha256。

### 压缩成本

PNG/JPEG/WebP 已经是压缩格式，`ZIP_DEFLATED` 对图片收益有限，还会吃 CPU。流式化后 CPU 占用在线时间更长，叠加效应比 temp 文件方式更明显。

**因此第一轮就要按 per-file 压缩策略上线**，而不是当作第二阶段优化：

```text
images/*      ZIP_STORED
metadata.json ZIP_DEFLATED
```

`zipstream-ng` 的 `add` / `add_path` 都接受 `compress_type=` kwarg。这通常比换框架更能提升大图导出的吞吐，并且是流式方案能稳定承受并发的前提。

## 推荐实施顺序

1. 加 `zipstream-ng` 依赖（注意 import 名是 `zipstream_ng`，不是 `zipstream`）。
2. 在 `gallery_archive.py` 加 `unique_export_name()` 和 `iter_gallery_zip_chunks()`，images 用 `ZIP_STORED`，metadata 用 `ZIP_DEFLATED`。
3. 在 `gallery.py` 把 ZIP download 改为 `StreamingResponse`。
4. 保留旧临时文件 builder（一两个版本后再删）。
5. 补契约测试，确认 ZIP 内容和 metadata 不变；patch 源头模块 `gallery_archive.build_gallery_zip_file` 验证不再走 temp 文件路径。
6. 跑：

```bash
python3 -m pytest backend/tests/test_contract.py -q
```

7. 可选跑真实 curl smoke test，看是否返回 chunked。
8. README 补一句导出行为变化。

## 验收标准

- `/api/download-all` 返回合法 ZIP。
- `/api/gallery/batch/download` 返回合法 ZIP。
- ZIP 内仍包含 `metadata.json` 和 `images/*`。
- metadata 字段与旧实现兼容（包含 `sha256`、`bytes`，不含 `thumbnail_filename` / `thumbnail_url`）。
- images 条目使用 `ZIP_STORED`，metadata.json 使用 `ZIP_DEFLATED`。
- 导出不存在的污染 filename 会被跳过。
- 不再创建完整导出临时 ZIP。
- 契约测试通过。
- 真实 ASGI 下响应没有 `Content-Length`，并使用 chunked transfer。

