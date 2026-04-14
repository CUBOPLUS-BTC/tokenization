from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import sys
from typing import Any
import uuid

from fastapi import Depends, FastAPI, Query, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth.jwt_utils import decode_token
from google.protobuf.json_format import MessageToDict
from common import get_readiness_payload, get_settings
from tokenization.tapd_client import TapdClient
from tokenization.db import (
    begin_asset_evaluation,
    complete_asset_evaluation,
    create_asset,
    get_asset_by_id,
    get_user_by_id,
    list_assets,
    reset_asset_evaluation,
)
from tokenization.evaluation import evaluate_asset_submission
from tokenization.events import InternalEventBus, RedisStreamMirror
from tokenization.schemas import (
    AssetCategory,
    AssetCreateRequest,
    AssetDetailOut,
    AssetDetailResponse,
    AssetEvaluationRequestResponse,
    AssetListResponse,
    AssetOut,
    AssetResponse,
    AssetStatus,
    AssetTokenOut,
)

settings = get_settings(service_name="tokenization", default_port=8002)
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: AsyncEngine | object | None = None
logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()
_event_bus = InternalEventBus()
_event_bus.subscribe("ai.evaluation.complete", RedisStreamMirror(settings.redis_url))


class ContractError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: str
    role: str


def _make_async_url(sync_url: str) -> str:
    url = sync_url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


def _runtime_engine() -> AsyncEngine | object:
    global _engine
    if _engine is None:
        _engine = create_async_engine(_make_async_url(settings.database_url), pool_pre_ping=True)
    return _engine


@asynccontextmanager
async def _lifespan(app: FastAPI):
    engine = _runtime_engine()
    yield
    tasks = tuple(_background_tasks)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    _background_tasks.clear()
    await engine.dispose()


def _error(
    code: str,
    message: str,
    status_code: int,
    *,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=payload)


def _normalize_uuid_claim(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _row_value(row: object, key: str):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key)


def _optional_row_value(row: object, key: str):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(key)
    return getattr(row, key, None)


def _jwt_secret() -> str:
    return settings.jwt_secret or "dev-secret-change-me"


def _invalid_access_token_error() -> ContractError:
    return ContractError(
        code="invalid_token",
        message="Access token is invalid or expired.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def _asset_not_found_error() -> ContractError:
    return ContractError(
        code="asset_not_found",
        message="Asset not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _asset_ownership_error() -> ContractError:
    return ContractError(
        code="forbidden",
        message="Only the owning seller can evaluate this asset.",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def _asset_evaluation_conflict_error(message: str) -> ContractError:
    return ContractError(
        code="asset_state_conflict",
        message=message,
        status_code=status.HTTP_409_CONFLICT,
    )


def _aware_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _isoformat_utc(value) -> str | None:
    aware_value = _aware_datetime(value)
    if aware_value is None:
        return None
    return aware_value.isoformat().replace("+00:00", "Z")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _asset_out(row: object) -> AssetOut:
    created_at = _aware_datetime(_row_value(row, "created_at"))
    updated_at = _aware_datetime(_row_value(row, "updated_at"))

    return AssetOut(
        id=str(_row_value(row, "id")),
        owner_id=str(_row_value(row, "owner_id")),
        name=_row_value(row, "name"),
        description=_row_value(row, "description"),
        category=_row_value(row, "category"),
        valuation_sat=_row_value(row, "valuation_sat"),
        documents_url=_optional_row_value(row, "documents_url"),
        status=_row_value(row, "status"),
        created_at=created_at,
        updated_at=updated_at,
    )


def _asset_token_out(row: object) -> AssetTokenOut | None:
    token_id = _optional_row_value(row, "token_id")
    if token_id is None:
        return None

    minted_at = _aware_datetime(_optional_row_value(row, "minted_at"))
    assert minted_at is not None

    return AssetTokenOut(
        id=str(token_id),
        taproot_asset_id=_row_value(row, "taproot_asset_id"),
        total_supply=_row_value(row, "total_supply"),
        circulating_supply=_row_value(row, "circulating_supply"),
        unit_price_sat=_row_value(row, "unit_price_sat"),
        minted_at=minted_at,
    )


def _asset_detail_out(row: object) -> AssetDetailOut:
    base_asset = _asset_out(row)
    return AssetDetailOut(
        **base_asset.model_dump(),
        ai_score=_optional_float(_optional_row_value(row, "ai_score")),
        ai_analysis=_optional_row_value(row, "ai_analysis"),
        projected_roi=_optional_float(_optional_row_value(row, "projected_roi")),
        token=_asset_token_out(row),
    )


def _sort_asset_rows(rows: list[object]) -> list[object]:
    return sorted(
        rows,
        key=lambda row: (_aware_datetime(_row_value(row, "created_at")), str(_row_value(row, "id"))),
        reverse=True,
    )


def _build_asset_page(
    rows: list[object],
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[object], str | None]:
    ordered_rows = _sort_asset_rows(rows)

    start_index = 0
    if cursor is not None:
        try:
            cursor_uuid = str(uuid.UUID(cursor))
        except ValueError as exc:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor must be a valid asset UUID.",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc

        for index, row in enumerate(ordered_rows):
            if str(_row_value(row, "id")) == cursor_uuid:
                start_index = index + 1
                break
        else:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor does not match an asset in this result set.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    page = ordered_rows[start_index:start_index + limit]
    next_cursor = (
        str(_row_value(page[-1], "id"))
        if start_index + limit < len(ordered_rows) and page
        else None
    )
    return page, next_cursor


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        field = ".".join(str(part) for part in loc if part != "body") or "body"
        details.append(
            {
                "field": field,
                "message": error.get("msg", "Invalid value."),
            }
        )
    return details


def _track_background_task(task: asyncio.Task[Any]) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _publish_asset_evaluation_complete(row: object) -> None:
    payload = {
        "event": "ai_evaluation_complete",
        "asset_id": str(_row_value(row, "id")),
        "owner_id": str(_row_value(row, "owner_id")),
        "ai_score": _optional_float(_row_value(row, "ai_score")),
        "projected_roi": _optional_float(_row_value(row, "projected_roi")),
        "status": _row_value(row, "status"),
        "analysis": _optional_row_value(row, "ai_analysis"),
        "completed_at": _isoformat_utc(_row_value(row, "updated_at")),
    }
    await _event_bus.publish("ai.evaluation.complete", payload)


async def _run_asset_evaluation(
    asset_id: uuid.UUID,
    *,
    fallback_status: str,
) -> None:
    try:
        async with _runtime_engine().connect() as conn:
            asset_row = await get_asset_by_id(conn, asset_id)

        if asset_row is None:
            logger.warning("Asset disappeared before evaluation completed: %s", asset_id)
            return

        evaluation = evaluate_asset_submission(asset_row)

        async with _runtime_engine().connect() as conn:
            completed_row = await complete_asset_evaluation(
                conn,
                asset_id=asset_id,
                ai_score=evaluation.ai_score,
                ai_analysis=evaluation.ai_analysis,
                projected_roi=evaluation.projected_roi,
                status=evaluation.status,
            )

        if completed_row is None:
            logger.warning("Asset left evaluating state before persistence: %s", asset_id)
            return

        try:
            await _publish_asset_evaluation_complete(completed_row)
        except Exception:
            logger.exception("Asset evaluation event publish failed for %s", asset_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Asset evaluation failed for %s", asset_id)
        async with _runtime_engine().connect() as conn:
            await reset_asset_evaluation(
                conn,
                asset_id=asset_id,
                fallback_status=fallback_status,
            )


def _dispatch_asset_evaluation(
    asset_id: uuid.UUID,
    *,
    fallback_status: str,
) -> None:
    task = asyncio.create_task(
        _run_asset_evaluation(asset_id, fallback_status=fallback_status)
    )
    _track_background_task(task)


async def _get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> AuthenticatedPrincipal:
    if credentials is None:
        raise ContractError(
            code="authentication_required",
            message="Authentication is required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        claims = decode_token(
            credentials.credentials,
            _jwt_secret(),
            expected_type="access",
        )
    except JWTError as exc:
        raise _invalid_access_token_error() from exc

    user_id = _normalize_uuid_claim(claims.get("sub"))
    role = claims.get("role")
    if user_id is None or not isinstance(role, str):
        raise _invalid_access_token_error()

    async with _runtime_engine().connect() as conn:
        row = await get_user_by_id(conn, user_id)

    if row is None or _row_value(row, "deleted_at") is not None:
        raise _invalid_access_token_error()

    return AuthenticatedPrincipal(id=user_id, role=role)


def _require_roles(*allowed_roles: str):
    async def dependency(
        principal: AuthenticatedPrincipal = Depends(_get_current_principal),
    ) -> AuthenticatedPrincipal:
        if principal.role not in allowed_roles:
            raise ContractError(
                code="forbidden",
                message="You do not have permission to access this resource.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return principal

    return dependency

app = FastAPI(title="Tokenization Service", lifespan=_lifespan)
tapd_client = TapdClient(settings)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(
        code="validation_error",
        message="Request payload failed validation.",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        details=_validation_details(exc),
    )


@app.exception_handler(ContractError)
async def contract_exception_handler(request: Request, exc: ContractError):
    return _error(
        exc.code,
        exc.message,
        exc.status_code,
        details=exc.details,
    )

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.service_name,
        "env_profile": settings.env_profile,
    }


@app.get("/ready")
async def ready():
    payload = get_readiness_payload(settings)
    status_code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/tapd/info")
async def tapd_info():
    try:
        info = tapd_client.get_info()
        return MessageToDict(info)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to connect to tapd", "detail": str(e)},
        )


@app.get("/tapd/assets")
async def tapd_assets():
    try:
        assets = tapd_client.list_assets()
        return MessageToDict(assets)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to list assets from tapd", "detail": str(e)},
        )


@app.post(
    "/assets",
    status_code=status.HTTP_201_CREATED,
    response_model=AssetResponse,
    summary="Submit an asset for review",
)
async def submit_asset(
    body: AssetCreateRequest,
    principal: AuthenticatedPrincipal = Depends(_require_roles("seller", "admin")),
):
    async with _runtime_engine().connect() as conn:
        row = await create_asset(
            conn,
            owner_id=principal.id,
            name=body.name,
            description=body.description,
            category=body.category,
            valuation_sat=body.valuation_sat,
            documents_url=str(body.documents_url),
        )

    return AssetResponse(asset=_asset_out(row)).model_dump(mode="json")


@app.get(
    "/assets",
    status_code=status.HTTP_200_OK,
    response_model=AssetListResponse,
    summary="Return a filtered asset catalog",
)
async def get_assets(
    asset_status: AssetStatus | None = Query(default=None, alias="status"),
    category: AssetCategory | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        rows = await list_assets(
            conn,
            asset_status=asset_status,
            category=category,
        )

    page, next_cursor = _build_asset_page(rows, cursor=cursor, limit=limit)
    return AssetListResponse(
        assets=[_asset_out(row) for row in page],
        next_cursor=next_cursor,
    ).model_dump(mode="json")


@app.post(
    "/assets/{asset_id}/evaluate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssetEvaluationRequestResponse,
    summary="Request AI evaluation for an owned asset",
)
async def request_asset_evaluation(
    asset_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(_require_roles("seller", "admin")),
):
    async with _runtime_engine().connect() as conn:
        asset_row = await get_asset_by_id(conn, asset_id)
        if asset_row is None:
            raise _asset_not_found_error()
        if str(_row_value(asset_row, "owner_id")) != principal.id:
            raise _asset_ownership_error()

        previous_status = _row_value(asset_row, "status")
        if previous_status == "evaluating":
            raise _asset_evaluation_conflict_error("Asset evaluation is already in progress.")
        if previous_status == "tokenized":
            raise _asset_evaluation_conflict_error("Tokenized assets cannot be re-evaluated.")

        queued_row = await begin_asset_evaluation(
            conn,
            asset_id=asset_id,
            owner_id=principal.id,
        )

    if queued_row is None:
        raise _asset_evaluation_conflict_error(
            "Asset status changed before evaluation could start. Please retry."
        )

    try:
        _dispatch_asset_evaluation(asset_id, fallback_status=previous_status)
    except RuntimeError as exc:
        logger.exception("Failed to dispatch evaluation for asset %s", asset_id)
        async with _runtime_engine().connect() as conn:
            await reset_asset_evaluation(
                conn,
                asset_id=asset_id,
                fallback_status=previous_status,
            )
        raise ContractError(
            code="evaluation_dispatch_failed",
            message="Unable to start asset evaluation.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc

    return AssetEvaluationRequestResponse(
        message="Evaluation started",
        estimated_completion=datetime.now(tz=timezone.utc) + timedelta(minutes=5),
    ).model_dump(mode="json")


@app.get(
    "/assets/{asset_id}",
    status_code=status.HTTP_200_OK,
    response_model=AssetDetailResponse,
    summary="Return a single asset with AI and tokenization details",
)
async def get_asset(
    asset_id: uuid.UUID,
    _principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        row = await get_asset_by_id(conn, asset_id)

    if row is None:
        raise _asset_not_found_error()

    return AssetDetailResponse(asset=_asset_detail_out(row)).model_dump(
        mode="json",
        exclude_none=True,
    )

if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
