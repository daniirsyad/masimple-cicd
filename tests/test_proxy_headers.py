"""Behind an HTTPS reverse proxy, redirects must stay https:// (an http://
Location makes the browser block htmx's request as mixed content)."""


def test_redirect_keeps_https_from_proxy(client):
    response = client.get(
        "/workflows",  # Flask redirects to /workflows/ with an absolute URL
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "deploy.example.com"},
    )
    assert response.status_code in (301, 308)
    assert response.headers["Location"].startswith("https://deploy.example.com/workflows/")
