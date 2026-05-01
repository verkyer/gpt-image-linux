# GPT Image Panel

Web panel for GPT Image 2 API image generation.

## Quick Start

### Docker (recommended)

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

Or with docker-compose:

```bash
cp .env.example .env
# Edit .env with your API URL, API key, access key, and IP allowlist
docker-compose up -d --build --force-recreate
```

If image generation fails with `Permission denied: 'images/<id>.png'`, the container
is still running an old image or an overridden non-root user. Rebuild and recreate
the container with the command above.

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9090 --reload
```

Then open `http://localhost:9090`.

Set `ACCESS_KEY` in `.env` to require an access key before any site/API access.
Successful access sessions last 60 minutes. Set `IP_ALLOWLIST` to restrict
backend access by client IP before any image generation flow can start.

## Usage

1. Click the **Settings** gear icon in the top right
2. Enter your **API Base URL** (e.g., `https://api.221.qzz.io`) and **API Key**
3. Click **Save**
4. Enter a **prompt**, choose an **image size**, and click **Generate**
5. View the preview, download the image, or browse the **Gallery**

Generation runs as a background job. The UI starts the job immediately and polls its status, so long image requests do not sit behind a single HTTP response and hit reverse-proxy timeouts.

Image size supports:

- `auto`: pass `auto` and let the model choose the output size
- Ratio presets: choose 1K, 2K, or 4K with ratios `1:1`, `4:3`, `3:4`, `16:9`, `9:16`, or `21:9`
- Custom width and height: values are normalized to multiples of 16, max side `3840px`, aspect ratio up to `3:1`, and total pixels between `655360` and `8294400`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_API_URL` | (empty) | Pre-fill API base URL |
| `DEFAULT_API_KEY` | (empty) | Pre-fill API key |
| `ACCESS_KEY` | (empty) | Site access key. When set, every non-health route requires unlock |
| `IP_ALLOWLIST` | (empty) | Comma-separated allowed IPs/CIDRs, e.g. `127.0.0.1,192.168.1.0/24` |
| `TRUST_PROXY_HEADERS` | `false` | Read `X-Forwarded-For`/`X-Real-IP` from a trusted reverse proxy |
| `MAX_FILE_SIZE_MB` | `50` | Max image size in MB |
| `IMAGES_DIR` | `./images` | Directory for saved images |
| `DATA_DIR` | `./data` | Directory for gallery metadata |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Frontend UI |
| `GET` | `/health` | Health check |
| `GET` | `/api/access/status` | Check access-key session status |
| `POST` | `/api/access` | Unlock access for 60 minutes |
| `POST` | `/api/settings` | Save API URL and Key |
| `GET` | `/api/settings` | Get current settings (key masked) |
| `POST` | `/api/generate` | Start an image generation job |
| `GET` | `/api/generate/{job_id}` | Get generation job status/result |
| `GET` | `/api/gallery` | List all gallery images |
| `GET` | `/api/image/{filename}` | Serve image file |
| `GET` | `/api/download/{filename}` | Download image as attachment |
| `DELETE` | `/api/gallery/{id}` | Delete gallery entry |
