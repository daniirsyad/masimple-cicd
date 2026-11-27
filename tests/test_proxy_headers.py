"""Behind an HTTPS reverse proxy, redirects must stay https:// (an http://
Location makes the browser block htmx's request as mixed content)."""

from flask import url_for

from app import create_app


def test_external_urls_keep_https_from_proxy():
    # A fresh app: the shared one has served requests, so it can't take a
    # new route. url_for(_external=True) is what redirects are built from.
    app = create_app("testing")
    app.add_url_rule("/_proxy_check", "proxy_check", lambda: url_for("auth.login", _external=True))
    response = app.test_client().get(
        "/_proxy_check",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "deploy.example.com"},
    )
    assert response.get_data(as_text=True).startswith("https://deploy.example.com/login")


def test_menu_urls_without_trailing_slash_are_served_directly(client):
    # Menus store /workflows; the route is /workflows/. No 308 round trip.
    assert client.get("/workflows").status_code != 308
