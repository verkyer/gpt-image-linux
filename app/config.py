import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"


def read_app_version() -> str:
    env_version = os.getenv("APP_VERSION", "").strip()
    if env_version:
        return env_version

    try:
        file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "v0.0.0"

    return file_version or "v0.0.0"


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

DEFAULT_API_URL = os.getenv("DEFAULT_API_URL", "")
DEFAULT_API_KEY = os.getenv("DEFAULT_API_KEY", "")
DEFAULT_API_PATH = os.getenv("DEFAULT_API_PATH", "/v1/images/generations")
DEFAULT_RESPONSES_MODEL = os.getenv("DEFAULT_RESPONSES_MODEL", "gpt-5.4")
APP_VERSION = read_app_version()
GITHUB_REPO = os.getenv("GITHUB_REPO", "Z1rconium/gpt-image-linux").strip()
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()
ALLOW_UNAUTHENTICATED = env_flag("ALLOW_UNAUTHENTICATED")
ACCESS_KEY_SESSION_MINUTES = 180
ACCESS_KEY_COOKIE_NAME = os.getenv("ACCESS_KEY_COOKIE_NAME", "gpt_image_access")
IP_ALLOWLIST = os.getenv("IP_ALLOWLIST", "")
TRUST_PROXY_HEADERS = env_flag("TRUST_PROXY_HEADERS")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
IMAGES_DIR = os.getenv("IMAGES_DIR", "./images")
DATA_DIR = os.getenv("DATA_DIR", "./data")
DATABASE_FILE = os.getenv("DATABASE_FILE", os.path.join(DATA_DIR, "app.sqlite3"))
GALLERY_FILE = os.path.join(DATA_DIR, "gallery.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
