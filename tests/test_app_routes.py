from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from src.shopping_list.app import create_app
from src.shopping_list.apps_script_proxy import ProxyResponse
from src.shopping_list.auth import hash_password
from src.shopping_list.config import Settings, DOCS_DIR, TEMPLATES_DIR


async def fake_proxy(query_params: QueryParams) -> ProxyResponse:
    action = query_params.get("action", "")
    return ProxyResponse(
        status_code=200,
        content=f'{{"success":true,"action":"{action}"}}'.encode("utf-8"),
        content_type="application/json",
    )


def make_client(tmp: str, cookie_secure: bool = False) -> TestClient:
    settings = Settings(
        docs_dir=DOCS_DIR,
        templates_dir=TEMPLATES_DIR,
        apps_script_url="https://example.test/apps-script",
        users={"jamie": hash_password("secret", salt=b"3" * 16, iterations=1_000)},
        session_db=Path(tmp) / "sessions.sqlite",
        cookie_secure=cookie_secure,
    )
    return TestClient(create_app(settings=settings, proxy_fetcher=fake_proxy))


def make_client_without_apps_script_url(tmp: str) -> TestClient:
    settings = Settings(
        docs_dir=DOCS_DIR,
        templates_dir=TEMPLATES_DIR,
        apps_script_url="",
        users={"jamie": hash_password("secret", salt=b"5" * 16, iterations=1_000)},
        session_db=Path(tmp) / "sessions.sqlite",
        cookie_secure=False,
    )
    return TestClient(create_app(settings=settings))


class AppRouteTests(unittest.TestCase):
    def test_healthz_is_public_and_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)

            response = client.get("/healthz")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ok": True})
            self.assertEqual(response.headers["x-robots-tag"], "noindex, nofollow, noarchive")

    def test_robots_txt_is_public_and_disallows_all_crawlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)

            response = client.get("/robots.txt")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, "User-agent: *\nDisallow: /\n")
            self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
            self.assertEqual(response.headers["x-robots-tag"], "noindex, nofollow, noarchive")

    def test_logged_out_user_is_redirected_from_app_and_static_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)

            index = client.get("/", follow_redirects=False)
            app_js = client.get("/app.js", follow_redirects=False)
            api = client.get("/api?action=getList", follow_redirects=False)

            self.assertEqual(index.status_code, 303)
            self.assertEqual(index.headers["location"], "/login")
            self.assertEqual(app_js.status_code, 303)
            self.assertEqual(app_js.headers["location"], "/login")
            self.assertEqual(app_js.headers["x-robots-tag"], "noindex, nofollow, noarchive")
            self.assertEqual(api.status_code, 401)
            self.assertEqual(api.headers["x-robots-tag"], "noindex, nofollow, noarchive")

    def test_logged_in_unknown_static_path_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            response = client.get("/not-a-real-asset.js")

            self.assertEqual(response.status_code, 404)

    def test_static_path_traversal_is_rejected_after_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            response = client.get("/%2e%2e/AGENTS.md")

            self.assertEqual(response.status_code, 404)

    def test_logged_out_user_is_redirected_from_nested_static_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)

            res = client.get("/some/nested/path/image.png", follow_redirects=False)

            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], "/login")

    def test_unknown_static_paths_return_404_after_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            res = client.get("/does_not_exist.html")
            
            self.assertEqual(res.status_code, 404)

    def test_bad_login_shows_generic_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)

            response = client.post(
                "/login",
                data={"username": "jamie", "password": "wrong"},
                follow_redirects=False,
            )

            self.assertEqual(response.status_code, 401)
            self.assertIn("Wrong username or password.", response.text)

    def test_login_allows_app_static_assets_and_api_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)

            login = client.post(
                "/login",
                data={"username": "jamie", "password": "secret", "keep_signed_in": "1"},
                follow_redirects=False,
            )
            self.assertEqual(login.status_code, 303)
            self.assertIn("shopping_list_session", login.headers.get("set-cookie", ""))

            index = client.get("/")
            app_js = client.get("/app.js")
            api = client.get("/api?action=getList")

            self.assertEqual(index.status_code, 200)
            self.assertIn("Shopping List", index.text)
            self.assertEqual(index.headers["x-robots-tag"], "noindex, nofollow, noarchive")
            self.assertEqual(app_js.status_code, 200)
            self.assertIn("Shopping List", app_js.text)
            self.assertEqual(api.status_code, 200)
            self.assertEqual(api.json()["action"], "getList")

    def test_login_cookie_is_http_only_same_site_and_secure_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp, cookie_secure=True)

            login = client.post(
                "/login",
                data={"username": "jamie", "password": "secret", "keep_signed_in": "1"},
                follow_redirects=False,
            )

            cookie = login.headers["set-cookie"].lower()
            self.assertIn("shopping_list_session=", cookie)
            self.assertIn("httponly", cookie)
            self.assertIn("samesite=lax", cookie)
            self.assertIn("secure", cookie)

    def test_login_cookie_omits_secure_flag_for_local_http_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp, cookie_secure=False)

            login = client.post(
                "/login",
                data={"username": "jamie", "password": "secret"},
                follow_redirects=False,
            )

            cookie = login.headers["set-cookie"].lower()
            self.assertIn("shopping_list_session=", cookie)
            self.assertIn("httponly", cookie)
            self.assertIn("samesite=lax", cookie)
            self.assertNotIn("secure", cookie)

    def test_logout_revokes_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            logout = client.post("/logout", follow_redirects=False)
            index = client.get("/", follow_redirects=False)

            self.assertEqual(logout.status_code, 303)
            self.assertEqual(index.status_code, 303)
            self.assertEqual(index.headers["location"], "/login")

    def test_logout_clears_session_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            logout = client.post("/logout", follow_redirects=False)

            cookie = logout.headers["set-cookie"].lower()
            self.assertIn("shopping_list_session=", cookie)
            self.assertIn("max-age=0", cookie)

    def test_get_logout_revokes_session_for_header_logout_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            logout = client.get("/logout", follow_redirects=False)
            index = client.get("/", follow_redirects=False)

            self.assertEqual(logout.status_code, 303)
            self.assertEqual(logout.headers["location"], "/login")
            self.assertEqual(index.status_code, 303)
            self.assertEqual(index.headers["location"], "/login")

    def test_get_logout_while_logged_out_redirects_to_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)

            logout = client.get("/logout", follow_redirects=False)

            self.assertEqual(logout.status_code, 303)
            self.assertEqual(logout.headers["location"], "/login")

    def test_login_redirects_to_root_when_already_logged_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            login = client.get("/login", follow_redirects=False)

            self.assertEqual(login.status_code, 303)
            self.assertEqual(login.headers["location"], "/")

    def test_unknown_api_path_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            # The app only mounts /api, not /api/something
            response = client.get("/api/getList")
            self.assertEqual(response.status_code, 404)

    def test_authenticated_api_returns_503_when_apps_script_url_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = make_client_without_apps_script_url(tmp)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            response = client.get("/api?action=getList")

            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"error": "Apps Script URL is not configured"})


if __name__ == "__main__":
    unittest.main()
