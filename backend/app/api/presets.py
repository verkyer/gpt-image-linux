import os
import re
from urllib.parse import urlsplit

from fastapi import HTTPException

from .app_state import app
from ..core import settings as config
from ..core.api_paths import ALLOWED_API_PATHS, normalize_api_path, normalize_api_preset
from ..core.validators import mask_socks5_proxy_url, normalize_socks5_proxy_url
from ..integrations import upstream_client as proxy
from ..repositories import storage
from ..schemas.models import ApiPresetResponse, PresetHealthResponse, SettingsResponse


def get_exception_message(error: Exception) -> str:
    return str(error) or repr(error) or error.__class__.__name__
def mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


API_KEY_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def get_api_key_env_var(api_key: str) -> str | None:
    match = API_KEY_ENV_REF_RE.match(str(api_key or "").strip())
    return match.group(1) if match else None


def is_malformed_api_key_env_ref(api_key: str) -> bool:
    value = str(api_key or "").strip()
    return bool(value) and ("${" in value or "}" in value) and not get_api_key_env_var(value)


def resolve_api_key(api_key: str) -> str:
    value = str(api_key or "").strip()
    env_var = get_api_key_env_var(value)
    if env_var:
        return os.getenv(env_var, "").strip()
    return value


def api_key_response_fields(api_key: str) -> dict:
    value = str(api_key or "").strip()
    env_var = get_api_key_env_var(value)
    if env_var:
        return {
            "api_key_masked": f"${{{env_var}}}",
            "has_api_key": bool(value),
            "api_key_source": "env",
            "api_key_env_var": env_var,
        }
    if value:
        return {
            "api_key_masked": mask_key(value),
            "has_api_key": True,
            "api_key_source": "stored",
            "api_key_env_var": None,
        }
    return {
        "api_key_masked": "***",
        "has_api_key": False,
        "api_key_source": "empty",
        "api_key_env_var": None,
    }


def get_effective_preset_api_key(preset: dict) -> str:
    api_key = str(preset.get("api_key") or "").strip()
    env_var = get_api_key_env_var(api_key)
    if is_malformed_api_key_env_ref(api_key):
        raise HTTPException(
            status_code=400,
            detail="API Key env ref must be formatted as ${ENV_VAR_NAME}.",
        )

    resolved_key = resolve_api_key(api_key)
    if resolved_key:
        return resolved_key

    if env_var:
        raise HTTPException(
            status_code=400,
            detail=(
                f"API Key environment variable {env_var} is not set or empty. "
                "Set it in the server environment."
            ),
        )
    raise HTTPException(
        status_code=400,
        detail="API Key not configured. Please set it in Settings.",
    )


def persist_api_settings():
    storage.save_settings(
        {
            "active_preset_id": getattr(app.state, "active_preset_id", "default"),
            "upstream_socks5_proxy": get_upstream_socks5_proxy(),
            "presets": get_api_presets(),
        }
    )


def load_api_settings():
    data = storage.load_settings()
    presets = data["presets"]
    app.state.api_presets = presets
    app.state.active_preset_id = data["active_preset_id"]
    apply_upstream_socks5_proxy(data.get("upstream_socks5_proxy"))
    apply_api_preset(get_active_preset())


def get_api_presets() -> list[dict]:
    presets = getattr(app.state, "api_presets", None)
    if presets:
        return presets

    preset = normalize_api_preset(
        {
            "id": "default",
            "name": "Default",
            "api_url": getattr(app.state, "api_url", config.DEFAULT_API_URL),
            "api_key": getattr(app.state, "api_key", config.DEFAULT_API_KEY),
            "api_path": getattr(app.state, "api_path", config.DEFAULT_API_PATH),
        }
    )
    app.state.api_presets = [preset]
    app.state.active_preset_id = preset["id"]
    return app.state.api_presets


def get_active_preset() -> dict:
    presets = get_api_presets()
    active_id = getattr(app.state, "active_preset_id", presets[0]["id"])
    for preset in presets:
        if preset["id"] == active_id:
            return preset

    app.state.active_preset_id = presets[0]["id"]
    return presets[0]


def get_preset_by_id(preset_id: str) -> dict | None:
    for preset in get_api_presets():
        if preset["id"] == preset_id:
            return preset
    return None


def apply_api_preset(preset: dict):
    app.state.api_url = preset.get("api_url", "").rstrip("/")
    app.state.api_key = preset.get("api_key", "")
    app.state.api_path = normalize_api_path(
        preset.get("api_path", "/v1/images/generations")
    )
    app.state.active_preset_id = preset["id"]


def get_upstream_socks5_proxy() -> str:
    return str(getattr(app.state, "upstream_socks5_proxy", "") or "").strip()


def apply_upstream_socks5_proxy(value: str | None):
    app.state.upstream_socks5_proxy = normalize_socks5_proxy_url(value)


def upstream_socks5_proxy_response_fields() -> dict:
    value = get_upstream_socks5_proxy()
    return {
        "has_upstream_socks5_proxy": bool(value),
        "upstream_socks5_proxy_masked": mask_socks5_proxy_url(value),
    }


def serialize_api_preset(preset: dict) -> ApiPresetResponse:
    key_fields = api_key_response_fields(preset.get("api_key", ""))
    return ApiPresetResponse(
        id=preset["id"],
        name=preset.get("name") or "Untitled preset",
        api_url=preset.get("api_url", ""),
        api_path=normalize_api_path(
            preset.get("api_path", "/v1/images/generations")
        ),
        **key_fields,
    )


def build_settings_response() -> SettingsResponse:
    active_preset = get_active_preset()
    key_fields = api_key_response_fields(active_preset.get("api_key", ""))
    return SettingsResponse(
        active_preset_id=active_preset["id"],
        api_url=active_preset.get("api_url", ""),
        **key_fields,
        api_path=normalize_api_path(
            active_preset.get("api_path", "/v1/images/generations")
        ),
        **upstream_socks5_proxy_response_fields(),
        presets=[serialize_api_preset(preset) for preset in get_api_presets()],
    )
HEALTH_STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


def add_health_check(checks: list[dict], name: str, status: str, message: str):
    checks.append({"name": name, "status": status, "message": message})


def health_status(checks: list[dict]) -> str:
    if not checks:
        return "error"
    return max(
        (check["status"] for check in checks),
        key=lambda status: HEALTH_STATUS_RANK[status],
    )


def validate_health_api_url(api_url: str, api_path: str, checks: list[dict]) -> bool:
    if not api_url:
        add_health_check(checks, "api_url", "error", "API URL is not configured")
        return False

    parsed = urlsplit(api_url)
    if parsed.scheme != "https":
        add_health_check(checks, "api_url", "error", "API URL must use https://")
        return False
    if not parsed.hostname:
        add_health_check(checks, "api_url", "error", "API URL must include a hostname")
        return False
    if parsed.query or parsed.fragment:
        add_health_check(
            checks,
            "api_url",
            "error",
            "API URL must not include query strings or fragments",
        )
        return False

    try:
        ssrf.validate_upstream_url(
            f"{api_url.rstrip('/')}{api_path}",
            config.UPSTREAM_HOST_ALLOWLIST,
        )
    except ValueError as e:
        add_health_check(checks, "ssrf", "error", str(e))
        return False

    add_health_check(
        checks,
        "ssrf",
        "ok",
        "API URL passed scheme, host allowlist, DNS, and private-IP checks",
    )
    return True


def validate_health_api_path(api_path: str, checks: list[dict]) -> bool:
    if api_path not in ALLOWED_API_PATHS:
        add_health_check(
            checks,
            "api_path",
            "error",
            (
                "API path is not supported. Allowed paths: "
                + ", ".join(sorted(ALLOWED_API_PATHS))
            ),
        )
        return False

    add_health_check(checks, "api_path", "ok", f"API path {api_path} is supported")
    return True


def validate_health_api_key(api_key: str, checks: list[dict]) -> str:
    raw_key = str(api_key or "").strip()
    env_var = get_api_key_env_var(raw_key)

    if is_malformed_api_key_env_ref(raw_key):
        add_health_check(
            checks,
            "api_key",
            "error",
            "API key env ref must be formatted as ${ENV_VAR_NAME}",
        )
        return ""

    if env_var:
        resolved_key = os.getenv(env_var, "").strip()
        if resolved_key:
            add_health_check(
                checks,
                "api_key",
                "ok",
                f"API key resolves from environment variable {env_var}",
            )
            return resolved_key
        add_health_check(
            checks,
            "api_key",
            "error",
            f"Environment variable {env_var} is not set or empty",
        )
        return ""

    if raw_key:
        add_health_check(checks, "api_key", "ok", "Stored API key is configured")
        return raw_key

    add_health_check(checks, "api_key", "error", "API key is not configured")
    return ""

