import os

DEFAULT_API_URL = os.getenv("DEFAULT_API_URL", "")
DEFAULT_API_KEY = os.getenv("DEFAULT_API_KEY", "")
ACCESS_KEY = os.getenv("ACCESS_KEY", "")
ACCESS_KEY_SESSION_MINUTES = 60
ACCESS_KEY_COOKIE_NAME = os.getenv("ACCESS_KEY_COOKIE_NAME", "gpt_image_access")
IP_ALLOWLIST = os.getenv("IP_ALLOWLIST", "")
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
IMAGES_DIR = os.getenv("IMAGES_DIR", "./images")
DATA_DIR = os.getenv("DATA_DIR", "./data")
GALLERY_FILE = os.path.join(DATA_DIR, "gallery.json")
