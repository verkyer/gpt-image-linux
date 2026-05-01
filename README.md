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
# Edit .env with your API URL and key
docker-compose up -d
```

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9090 --reload
```

Then open `http://localhost:9090`.

## Usage

1. Click the **Settings** gear icon in the top right
2. Enter your **API Base URL** (e.g., `https://api.221.qzz.io`) and **API Key**
3. Click **Save**
4. Enter a **prompt**, choose a **size**, and click **Generate**
5. View the preview, download the image, or browse the **Gallery**

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_API_URL` | (empty) | Pre-fill API base URL |
| `DEFAULT_API_KEY` | (empty) | Pre-fill API key |
| `MAX_FILE_SIZE_MB` | `50` | Max image size in MB |
| `IMAGES_DIR` | `./images` | Directory for saved images |
| `DATA_DIR` | `./data` | Directory for gallery metadata |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Frontend UI |
| `GET` | `/health` | Health check |
| `POST` | `/api/settings` | Save API URL and Key |
| `GET` | `/api/settings` | Get current settings (key masked) |
| `POST` | `/api/generate` | Generate an image |
| `GET` | `/api/gallery` | List all gallery images |
| `GET` | `/api/image/{filename}` | Serve image file |
| `GET` | `/api/download/{filename}` | Download image as attachment |
| `DELETE` | `/api/gallery/{id}` | Delete gallery entry |