from .app_state import FRONTEND_BUILD_DIR, app
from .middleware import register_exception_handlers, register_middleware
from .routers import routers
from ..integrations import upstream_client as proxy
from ..repositories import storage


register_middleware(app)
register_exception_handlers(app)

for router in routers:
    app.include_router(router)
