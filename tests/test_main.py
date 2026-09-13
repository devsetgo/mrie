# -*- coding: utf-8 -*-
def test_read_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Mike Ryan" in response.text
    assert "LinkedIn" in response.text
    assert "Github" in response.text
    assert "PyPi" in response.text
    assert "About" in response.text


def test_read_root_redirects_logged_in_user_to_notes(logged_in_client):
    # mrie is single-user: once logged in, "/" has nothing the public landing
    # page doesn't already skip past - see src/main.py::read_root.
    response = logged_in_client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/notes"


def test_unknown_http_exception_code_falls_back_to_500_error_page(client):
    # A status code outside site_error_routing_codes (e.g. 419, which isn't
    # a real/registered HTTP status) can't be looked up in ALL_HTTP_CODES -
    # the global exception handler (src/app_routes.py) normalizes it to 500
    # rather than KeyError-ing when building the /error/{code} redirect.
    app = client.app

    @app.get("/__test-unknown-error-code", include_in_schema=False)
    async def _raise_unknown_error_code():
        from fastapi import HTTPException

        # detail must be passed explicitly - HTTPException's own __init__
        # looks up http.HTTPStatus(status_code).phrase when detail is None,
        # which would raise ValueError for a code this unrecognized before
        # our handler ever saw it.
        raise HTTPException(status_code=419, detail="test")

    response = client.get("/__test-unknown-error-code", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/error/500"


def test_read_about(client):
    response = client.get("/about")
    assert response.status_code == 200
    assert "Mike Ryan" in response.text
    # Default seeded content (created on first access - see
    # AboutPage/_get_or_create_about in src/endpoints/about.py)
    assert "Professional Summary" in response.text


def test_error_page(client):
    error_codes = [400, 404, 500]
    for code in error_codes:
        response = client.get(f"/error/{code}")
        assert response.status_code == 200


def test_health_status(client):
    response = client.get("/api/health/status")
    assert response.status_code == 200


def test_health_uptime(client):
    response = client.get("/api/health/uptime")
    assert response.status_code == 200


def test_health_heapdump(client):
    response = client.get("/api/health/heapdump")
    assert response.status_code == 200
