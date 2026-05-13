from . import access, edits, gallery, generate, settings, static


routers = (
    access.router,
    settings.router,
    generate.router,
    edits.router,
    gallery.router,
    static.router,
)
