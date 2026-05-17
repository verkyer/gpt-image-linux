from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .csp import CONTENT_SECURITY_POLICY
from ..core import security as auth
from ..core import settings as config


AUTH_EXEMPT_PATHS = {
    "/",
    "/api/access",
    "/api/access/status",
    "/api/version",
    "/api/version/latest",
    "/favicon.ico",
    "/health",
}
AUTH_EXEMPT_PREFIXES = ("/_app/",)
NO_CACHE_PATHS = {"/"}
NO_CACHE_PREFIXES: tuple[str, ...] = ()
CSRF_PROTECTED_METHODS = {"POST", "PATCH", "DELETE"}


def apply_security_headers(response: Response) -> Response:
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None

    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None

    try:
        port = parts.port
    except ValueError:
        return None

    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if port and not (
        (parts.scheme == "http" and port == 80)
        or (parts.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    return f"{parts.scheme.lower()}://{host}"


def get_request_origin(request: Request) -> str | None:
    scheme = request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    if config.TRUST_PROXY_HEADERS:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto:
            trusted_scheme = forwarded_proto.split(",", 1)[0].strip().lower()
            if trusted_scheme in {"http", "https"}:
                scheme = trusted_scheme
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_host:
            host = forwarded_host.split(",", 1)[0].strip()

    return normalize_origin(f"{scheme}://{host}")


def csrf_origin_allowed(request: Request) -> bool:
    if (
        not config.CSRF_ORIGIN_CHECK_ENABLED
        or request.method.upper() not in CSRF_PROTECTED_METHODS
    ):
        return True

    expected_origin = get_request_origin(request)
    if not expected_origin:
        return False

    # Browser fetch metadata reflects the page-visible request target before a
    # local/dev proxy rewrites the upstream Host header.
    sec_fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if sec_fetch_site == "same-origin":
        return True

    origin = request.headers.get("origin")
    if origin is not None:
        return normalize_origin(origin) == expected_origin

    referer = request.headers.get("referer")
    if referer:
        return normalize_origin(referer) == expected_origin

    return True



def register_middleware(app):
    @app.middleware("http")
    async def access_control_middleware(request: Request, call_next):
        if request.url.path != "/health":
            client_ip = auth.get_client_ip(request)
            if not auth.is_ip_allowed(client_ip):
                return apply_security_headers(
                    JSONResponse(
                        status_code=403,
                        content={"status": "error", "detail": "IP address is not allowed"},
                    )
                )

        if not csrf_origin_allowed(request):
            return apply_security_headers(
                JSONResponse(
                    status_code=403,
                    content={"status": "error", "detail": "CSRF origin check failed"},
                )
            )

        if (
            config.ACCESS_KEY
            and request.url.path not in AUTH_EXEMPT_PATHS
            and not request.url.path.startswith(AUTH_EXEMPT_PREFIXES)
        ):
            token = request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
            if not auth.verify_access_token(token):
                return apply_security_headers(
                    JSONResponse(
                        status_code=401,
                        content={"status": "error", "detail": "Access key required"},
                    )
                )

        response = await call_next(request)

        if request.url.path in NO_CACHE_PATHS or request.url.path.startswith(
            NO_CACHE_PREFIXES
        ):
            response.headers["Cache-Control"] = "no-cache"

        return apply_security_headers(response)


def register_exception_handlers(app):
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Internal Server Error"},
        )
