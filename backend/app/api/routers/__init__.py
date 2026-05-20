from . import access, edits, gallery, generate, metrics, prompt, settings, static


routers = (
    access.router,
    settings.router,
    generate.router,
    edits.router,
    gallery.router,
    prompt.router,
    metrics.router,
    static.router,
)
