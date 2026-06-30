from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .apps_script_proxy import ProxyFetcher, make_apps_script_fetcher
from .auth import SessionStore, User, UserStore
from .config import Settings, load_settings
from .sqlite_api import SQLiteActionService


def _is_safe_doc_path(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def create_app(
    settings: Settings | None = None,
    proxy_fetcher: ProxyFetcher | None = None,
    sqlite_actions: SQLiteActionService | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    templates = Jinja2Templates(directory=settings.templates_dir)
    user_store = UserStore(settings.users)
    session_store = SessionStore(settings.session_db)
    proxy_fetcher = proxy_fetcher or make_apps_script_fetcher(settings.apps_script_url)
    if settings.data_backend == "sqlite":
        sqlite_actions = sqlite_actions or SQLiteActionService(settings.app_db)

    app = FastAPI(title="Shopping List")
    app.state.settings = settings
    app.state.user_store = user_store
    app.state.session_store = session_store
    app.state.sqlite_actions = sqlite_actions

    if sqlite_actions is not None:
        app.router.add_event_handler("shutdown", sqlite_actions.close)

    @app.middleware("http")
    async def add_noindex_header(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        return response

    def current_user(request: Request) -> User | None:
        token = request.cookies.get(settings.session_cookie_name)
        return session_store.get_user_for_token(token)

    def require_user(request: Request) -> User:
        user = current_user(request)
        if not user:
            raise HTTPException(status_code=401)
        return user

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/robots.txt")
    async def robots_txt() -> Response:
        return Response(
            content="User-agent: *\nDisallow: /\n",
            media_type="text/plain",
        )

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> Response:
        if current_user(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": ""})

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        keep_signed_in: str | None = Form(None),
    ) -> Response:
        user = user_store.authenticate(username, password)
        if not user:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Wrong username or password."},
                status_code=401,
            )
        days = settings.session_days if keep_signed_in else 1
        token, expires_at = session_store.create_session(user, duration=timedelta(days=days))
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            settings.session_cookie_name,
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            expires=expires_at,
        )
        return response

    @app.get("/logout")
    @app.post("/logout")
    async def logout(request: Request) -> Response:
        token = request.cookies.get(settings.session_cookie_name)
        session_store.delete_session(token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(settings.session_cookie_name)
        return response

    @app.get("/api")
    async def api_proxy(request: Request, user: User = Depends(require_user)) -> Response:
        if settings.data_backend == "sqlite":
            if sqlite_actions is None:  # defensive: create_app always supplies it in SQLite mode
                return JSONResponse({"error": "SQLite backend is unavailable"}, status_code=503)
            return JSONResponse(sqlite_actions.dispatch(request.query_params, username=user.username))
        proxied = await proxy_fetcher(request.query_params)
        return Response(
            content=proxied.content,
            status_code=proxied.status_code,
            media_type=proxied.content_type,
        )

    @app.get("/")
    async def index(request: Request) -> Response:
        if not current_user(request):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(settings.docs_dir / "index.html")

    @app.get("/{asset_path:path}")
    async def protected_asset(asset_path: str, request: Request) -> Response:
        if not current_user(request):
            return RedirectResponse("/login", status_code=303)
        target = settings.docs_dir / asset_path
        if not _is_safe_doc_path(settings.docs_dir, target) or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target)

    return app


app = create_app()
