from . import access, edits, gallery, generate, metrics, settings, static


routers = (
    access.router,
    settings.router,
    generate.router,
    edits.router,
    gallery.router,
    metrics.router,
    static.router,
)
