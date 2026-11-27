from flask import g, has_request_context


def per_request(key, compute):
    """compute() once per request, then the same value for the rest of it.

    Outside a request (background workers, CLI) it computes every time, so a
    long-lived app context never serves a stale value. The cache is emptied
    at the start of every request (app/__init__.py): a request served inside
    an app context that's already open (a test's `with app.app_context():`)
    reuses that context, and its `g`.
    """
    if not has_request_context():
        return compute()
    cache = g.setdefault("_per_request", {})
    if key not in cache:
        cache[key] = compute()
    return cache[key]
