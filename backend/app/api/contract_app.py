from starlette.middleware.gzip import GZipMiddleware

from .app_state import FRONTEND_BUILD_DIR, app
from .middleware import register_exception_handlers, register_middleware
from .routers import routers
from ..integrations import upstream_client as proxy
from ..repositories import storage


register_middleware(app)
register_exception_handlers(app)
app.add_middleware(GZipMiddleware, minimum_size=1024)

for router in routers:
    app.include_router(router)
