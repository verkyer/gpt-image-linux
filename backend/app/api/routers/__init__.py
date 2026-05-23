from . import (
    access,
    edits,
    gallery,
    generate,
    metrics,
    prompt,
    prompt_snippets,
    settings,
    static,
)


routers = (
    access.router,
    settings.router,
    generate.router,
    edits.router,
    gallery.router,
    prompt_snippets.router,
    prompt.router,
    metrics.router,
    static.router,
)
