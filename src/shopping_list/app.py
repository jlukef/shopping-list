from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .apps_script_proxy import ProxyFetcher, make_apps_script_fetcher
from .auth import SessionStore, User, UserStore
from .config import Settings, load_settings
from .receipt_extraction import ExtractorRegistry
from .receipts_service import ReceiptNotFound, ReceiptService, ReceiptStateError
from .sqlite_api import SQLiteActionService


def _is_safe_doc_path(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


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
    # Receipts have no Apps Script equivalent at all (PHASE5_RECEIPT_OCR_PLAN.md §6),
    # so the feature is only available in SQLite mode, regardless of data_backend.
    receipt_service = (
        ReceiptService(sqlite_actions.engine, extractor_registry=ExtractorRegistry(settings.receipt_ai))
        if sqlite_actions is not None else None
    )

    app = FastAPI(title="Shopping List")
    app.state.settings = settings
    app.state.user_store = user_store
    app.state.session_store = session_store
    app.state.sqlite_actions = sqlite_actions
    app.state.receipt_service = receipt_service

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

    def require_same_origin(request: Request) -> None:
        # Legacy /api?action=... traffic is GET-only and has no Apps-Script CORS
        # exposure, so SameSite=lax cookies were sufficient there. The new
        # /api/receipts/* routes are real state-changing methods, so per
        # PHASE5_RECEIPT_OCR_PLAN.md §6 they get an explicit same-origin check
        # rather than relying solely on the cookie's SameSite attribute.
        candidate = request.headers.get("origin") or request.headers.get("referer")
        candidate_origin = _origin_of(candidate) if candidate else None
        if not candidate_origin or candidate_origin != _origin_of(str(request.base_url)):
            raise HTTPException(status_code=403, detail="Cross-origin request rejected")

    def require_receipt_service() -> ReceiptService:
        if receipt_service is None:
            raise HTTPException(status_code=503, detail="Receipts are unavailable")
        return receipt_service

    def receipt_error_response(exc: Exception) -> JSONResponse:
        if isinstance(exc, ReceiptNotFound):
            return JSONResponse({"error": str(exc)}, status_code=404)
        if isinstance(exc, ReceiptStateError):
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"error": str(exc)}, status_code=400)

    async def read_upload_within_limit(request: Request, max_bytes: int) -> bytes:
        # Read the raw request body directly. FastAPI's UploadFile/multipart parser
        # uses SpooledTemporaryFile and rolls files over 1 MiB onto disk before a
        # route handler runs, which violates this app's no-image-storage rule and
        # makes an in-handler limit too late. Raw ASGI streaming stays memory-only
        # and enforces the limit while bytes arrive.
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise HTTPException(status_code=413, detail="Image is too large")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length")
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="Image is too large")
            chunks.append(chunk)
        return b"".join(chunks)

    async def read_json_object(request: Request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        return data

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

    # ── Receipts (5a transient upload/manual review + 5b AI extraction) ──
    # Real REST routes rather than the legacy ?action= GET convention — see
    # PHASE5_RECEIPT_OCR_PLAN.md §6 for why that's a deliberate exception.

    @app.get("/api/receipt-ai/options")
    async def receipt_ai_options_route(user: User = Depends(require_user)) -> JSONResponse:
        service = require_receipt_service()
        return JSONResponse({"options": service.list_extractor_options()})

    @app.post("/api/receipts")
    async def upload_receipt(
        request: Request,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        raw_bytes = await read_upload_within_limit(request, settings.max_upload_mb * 1024 * 1024)
        filename = unquote(request.headers.get("x-receipt-filename", "")) or None
        extractor_alias = request.query_params.get("extractor", "auto")
        try:
            result = await service.create_receipt(
                raw_bytes, original_filename=filename, extractor_alias=extractor_alias,
            )
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)
        finally:
            raw_bytes = b""  # nothing here persists the upload; drop the reference promptly
        return JSONResponse(result, status_code=201)

    @app.post("/api/receipts/{receipt_id}/retry")
    async def retry_receipt_route(
        receipt_id: str,
        request: Request,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        raw_bytes = await read_upload_within_limit(request, settings.max_upload_mb * 1024 * 1024)
        extractor_alias = request.query_params.get("extractor", "auto")
        try:
            result = await service.retry_receipt(receipt_id, raw_bytes, extractor_alias=extractor_alias)
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)
        finally:
            raw_bytes = b""
        return JSONResponse(result)

    @app.get("/api/receipts")
    async def list_receipts_route(user: User = Depends(require_user)) -> JSONResponse:
        service = require_receipt_service()
        return JSONResponse({"receipts": service.list_receipts()})

    @app.get("/api/receipts/{receipt_id}")
    async def get_receipt_route(receipt_id: str, user: User = Depends(require_user)) -> JSONResponse:
        service = require_receipt_service()
        try:
            return JSONResponse(service.get_receipt(receipt_id))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.patch("/api/receipts/{receipt_id}")
    async def patch_receipt_route(
        receipt_id: str,
        request: Request,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        data = await read_json_object(request)
        try:
            return JSONResponse(service.patch_receipt(receipt_id, data))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.delete("/api/receipts/{receipt_id}")
    async def discard_receipt_route(
        receipt_id: str,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        try:
            return JSONResponse(service.discard_receipt(receipt_id))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.post("/api/receipts/{receipt_id}/items")
    async def add_receipt_item_route(
        receipt_id: str,
        request: Request,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        data = await read_json_object(request)
        try:
            return JSONResponse(service.add_item(receipt_id, data), status_code=201)
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.patch("/api/receipts/{receipt_id}/items/{item_id}")
    async def update_receipt_item_route(
        receipt_id: str,
        item_id: str,
        request: Request,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        data = await read_json_object(request)
        try:
            return JSONResponse(service.update_item(receipt_id, item_id, data))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.post("/api/receipts/{receipt_id}/accept")
    async def accept_receipt_route(
        receipt_id: str,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        try:
            return JSONResponse(service.accept_receipt(receipt_id))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    # ── History (linked bidirectionally to saved receipts where applicable) ──
    @app.get("/api/history")
    async def list_history_route(user: User = Depends(require_user)) -> JSONResponse:
        service = require_receipt_service()
        return JSONResponse({"trips": service.list_history()})

    @app.get("/api/history/{trip_id}")
    async def get_history_trip_route(trip_id: str, user: User = Depends(require_user)) -> JSONResponse:
        service = require_receipt_service()
        try:
            return JSONResponse(service.get_history_trip(trip_id))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.patch("/api/history/{trip_id}")
    async def patch_history_trip_route(
        trip_id: str,
        request: Request,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        data = await read_json_object(request)
        try:
            return JSONResponse(service.patch_history_trip(trip_id, data))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.delete("/api/history/{trip_id}")
    async def delete_history_trip_route(
        trip_id: str,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        try:
            return JSONResponse(service.delete_history_trip(trip_id))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.patch("/api/history/{trip_id}/items/{item_id}")
    async def patch_history_item_route(
        trip_id: str,
        item_id: str,
        request: Request,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        data = await read_json_object(request)
        try:
            return JSONResponse(service.update_history_item(trip_id, item_id, data))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

    @app.delete("/api/history/{trip_id}/items/{item_id}")
    async def delete_history_item_route(
        trip_id: str,
        item_id: str,
        user: User = Depends(require_user),
        _origin: None = Depends(require_same_origin),
    ) -> JSONResponse:
        service = require_receipt_service()
        try:
            return JSONResponse(service.delete_history_item(trip_id, item_id))
        except (ValueError, ReceiptNotFound, ReceiptStateError) as exc:
            return receipt_error_response(exc)

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
