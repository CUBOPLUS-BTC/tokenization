from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any
import uuid

from fastapi import Depends, FastAPI, Query, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth.jwt_utils import decode_token
from common import get_readiness_payload, get_settings
from education.db import (
    create_enrollment,
    get_course_by_id,
    get_enrollment_by_id,
    get_enrollment_by_user_course,
    get_user_by_id,
    list_courses,
    update_enrollment_progress,
)
from education.schemas import (
    CourseDetailOut,
    CourseDetailResponse,
    CourseListResponse,
    CourseOut,
    EnrollmentOut,
    EnrollmentProgressUpdateRequest,
    EnrollmentResponse,
)


settings = get_settings(service_name="education", default_port=8004)
_bearer_scheme = HTTPBearer(auto_error=False)
_engine: AsyncEngine | object | None = None


class ContractError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
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
    await engine.dispose()


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _jwt_secret() -> str:
    return settings.jwt_secret or "dev-secret-change-me"


def _normalize_uuid_claim(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _row_value(row: object, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)

    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]

    if hasattr(row, key):
        return getattr(row, key)

    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return default


def _progress_number(value: object) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if value is None:
        return 0.0
    return float(value)


def _course_out(row: object) -> CourseOut:
    return CourseOut(
        id=str(_row_value(row, "id")),
        title=str(_row_value(row, "title")),
        description=str(_row_value(row, "description")),
        category=str(_row_value(row, "category")),
        difficulty=str(_row_value(row, "difficulty")),
    )


def _course_detail_out(row: object) -> CourseDetailOut:
    return CourseDetailOut(
        **_course_out(row).model_dump(),
        content_url=str(_row_value(row, "content_url")),
    )


def _enrollment_out(row: object) -> EnrollmentOut:
    return EnrollmentOut(
        id=str(_row_value(row, "id")),
        course_id=str(_row_value(row, "course_id")),
        progress=_progress_number(_row_value(row, "progress")),
        enrolled_at=_row_value(row, "enrolled_at"),
        completed_at=_row_value(row, "completed_at"),
    )


def _build_course_page(
    rows: list[object],
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[object], str | None]:
    start_index = 0

    if cursor is not None:
        try:
            cursor_uuid = str(uuid.UUID(cursor))
        except ValueError as exc:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor must be a valid course UUID.",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc

        for index, row in enumerate(rows):
            if str(_row_value(row, "id")) == cursor_uuid:
                start_index = index + 1
                break
        else:
            raise ContractError(
                code="invalid_cursor",
                message="Cursor does not match a course in this result set.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    page = rows[start_index:start_index + limit]
    next_cursor = str(_row_value(page[-1], "id")) if start_index + limit < len(rows) and page else None
    return page, next_cursor


def _course_not_found_error() -> ContractError:
    return ContractError(
        code="course_not_found",
        message="Course not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _enrollment_not_found_error() -> ContractError:
    return ContractError(
        code="enrollment_not_found",
        message="Enrollment not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _invalid_access_token_error() -> ContractError:
    return ContractError(
        code="invalid_token",
        message="Access token is invalid or expired.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


app = FastAPI(title="Education Service", lifespan=_lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(
        code="validation_error",
        message="Request payload failed validation.",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


@app.exception_handler(ContractError)
async def contract_exception_handler(request: Request, exc: ContractError):
    return _error(exc.code, exc.message, exc.status_code)


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
    role = str(claims.get("role") or "user")
    if user_id is None:
        raise _invalid_access_token_error()

    async with _runtime_engine().connect() as conn:
        row = await get_user_by_id(conn, user_id)

    if row is None or _row_value(row, "deleted_at") is not None:
        raise _invalid_access_token_error()

    return AuthenticatedPrincipal(id=user_id, role=role)


@app.get("/courses", response_model=CourseListResponse)
async def get_courses(
    category: str | None = Query(default=None, pattern="^(bitcoin|finance|programming|entrepreneurship)$"),
    difficulty: str | None = Query(default=None, pattern="^(beginner|intermediate|advanced)$"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    async with _runtime_engine().connect() as conn:
        rows = await list_courses(
            conn,
            category=category,
            difficulty=difficulty,
        )

    page, next_cursor = _build_course_page(rows, cursor=cursor, limit=limit)
    return CourseListResponse(
        courses=[_course_out(row) for row in page],
        next_cursor=next_cursor,
    ).model_dump(mode="json")


@app.get("/courses/{course_id}", response_model=CourseDetailResponse)
async def get_course(course_id: uuid.UUID):
    async with _runtime_engine().connect() as conn:
        row = await get_course_by_id(conn, course_id)

    if row is None:
        raise _course_not_found_error()

    return CourseDetailResponse(course=_course_detail_out(row)).model_dump(mode="json")


@app.post(
    "/courses/{course_id}/enroll",
    status_code=status.HTTP_201_CREATED,
    response_model=EnrollmentResponse,
)
async def enroll_in_course(
    course_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        course_row = await get_course_by_id(conn, course_id)
        if course_row is None:
            raise _course_not_found_error()

        existing_enrollment = await get_enrollment_by_user_course(
            conn,
            user_id=principal.id,
            course_id=course_id,
        )
        if existing_enrollment is not None:
            raise ContractError(
                code="enrollment_exists",
                message="You are already enrolled in this course.",
                status_code=status.HTTP_409_CONFLICT,
            )

        try:
            enrollment_row = await create_enrollment(
                conn,
                user_id=principal.id,
                course_id=course_id,
            )
        except IntegrityError as exc:
            raise ContractError(
                code="enrollment_exists",
                message="You are already enrolled in this course.",
                status_code=status.HTTP_409_CONFLICT,
            ) from exc

    return EnrollmentResponse(enrollment=_enrollment_out(enrollment_row)).model_dump(mode="json")


@app.patch("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def patch_enrollment(
    enrollment_id: uuid.UUID,
    body: EnrollmentProgressUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(_get_current_principal),
):
    async with _runtime_engine().connect() as conn:
        enrollment_row = await get_enrollment_by_id(conn, enrollment_id)
        if enrollment_row is None:
            raise _enrollment_not_found_error()

        if str(_row_value(enrollment_row, "user_id")) != principal.id:
            raise ContractError(
                code="forbidden",
                message="You do not have permission to access this resource.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        updated_row = await update_enrollment_progress(
            conn,
            enrollment_id=enrollment_id,
            user_id=principal.id,
            progress=body.progress,
        )

    if updated_row is None:
        raise _enrollment_not_found_error()

    return EnrollmentResponse(enrollment=_enrollment_out(updated_row)).model_dump(mode="json")


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


if __name__ == "__main__":
    uvicorn.run(app, host=settings.service_host, port=settings.service_port)
