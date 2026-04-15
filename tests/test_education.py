from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
import os
import sys
from typing import Any, NamedTuple
from unittest.mock import ANY, AsyncMock, MagicMock, patch
import uuid

import pytest
from fastapi.testclient import TestClient

from services.auth.jwt_utils import issue_token_pair


class FakeUser(NamedTuple):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime
    deleted_at: datetime | None


class FakeCourse(NamedTuple):
    id: uuid.UUID
    title: str
    description: str
    content_url: str
    category: str
    difficulty: str
    is_published: bool
    created_at: datetime
    updated_at: datetime


class FakeEnrollment(NamedTuple):
    id: uuid.UUID
    user_id: uuid.UUID
    course_id: uuid.UUID
    progress: Decimal
    enrolled_at: datetime
    completed_at: datetime | None


class _FetchOneResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def fetchone(self) -> object:
        return self._row


def _make_fake_user(*, role: str = "user") -> FakeUser:
    return FakeUser(
        id=uuid.uuid4(),
        email="learner@example.com",
        display_name="Learner",
        role=role,
        created_at=datetime.now(tz=timezone.utc),
        deleted_at=None,
    )


def _make_course(
    *,
    category: str = "bitcoin",
    difficulty: str = "beginner",
    created_at: datetime | None = None,
) -> FakeCourse:
    now = created_at or datetime.now(tz=timezone.utc)
    return FakeCourse(
        id=uuid.uuid4(),
        title="Bitcoin Fundamentals",
        description="Learn the basics of Bitcoin and self-custody.",
        content_url="https://education.example.com/courses/bitcoin-fundamentals",
        category=category,
        difficulty=difficulty,
        is_published=True,
        created_at=now,
        updated_at=now,
    )


def _make_enrollment(
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    progress: Decimal | str = Decimal("0.00"),
    completed_at: datetime | None = None,
) -> FakeEnrollment:
    return FakeEnrollment(
        id=uuid.uuid4(),
        user_id=user_id,
        course_id=course_id,
        progress=Decimal(str(progress)),
        enrolled_at=datetime.now(tz=timezone.utc),
        completed_at=completed_at,
    )


@pytest.fixture()
def education_settings() -> dict[str, str]:
    return {
        "ENV_PROFILE": "local",
        "WALLET_SERVICE_URL": "http://wallet:8001",
        "TOKENIZATION_SERVICE_URL": "http://tokenization:8002",
        "MARKETPLACE_SERVICE_URL": "http://marketplace:8003",
        "EDUCATION_SERVICE_URL": "http://education:8004",
        "NOSTR_SERVICE_URL": "http://nostr:8005",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "testdb",
        "POSTGRES_USER": "user",
        "DATABASE_URL": "postgresql://user:pass@localhost/testdb",
        "REDIS_URL": "redis://localhost:6379/0",
        "BITCOIN_RPC_HOST": "localhost",
        "BITCOIN_RPC_PORT": "18443",
        "BITCOIN_RPC_USER": "bitcoin",
        "BITCOIN_NETWORK": "regtest",
        "LND_GRPC_HOST": "localhost",
        "LND_GRPC_PORT": "10009",
        "LND_MACAROON_PATH": "tests/fixtures/admin.macaroon",
        "LND_TLS_CERT_PATH": "tests/fixtures/tls.cert",
        "TAPD_GRPC_HOST": "localhost",
        "TAPD_GRPC_PORT": "10029",
        "TAPD_MACAROON_PATH": "tests/fixtures/tapd.macaroon",
        "TAPD_TLS_CERT_PATH": "tests/fixtures/tapd.cert",
        "NOSTR_RELAYS": "wss://relay.example.com",
        "JWT_SECRET": "test-secret-key-for-education-tests",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "TOTP_ISSUER": "Platform",
        "LOG_LEVEL": "INFO",
    }


@pytest.fixture()
def client(education_settings):
    fake_conn = AsyncMock()

    @asynccontextmanager
    async def _fake_connect():
        yield fake_conn

    fake_engine = MagicMock()
    fake_engine.connect = _fake_connect
    fake_engine.dispose = AsyncMock()

    with patch.dict(os.environ, education_settings, clear=False):
        for module_name in (
            "services.education.main",
            "services.education.db",
            "services.education.schemas",
            "common",
            "common.config",
        ):
            sys.modules.pop(module_name, None)

        import services.education.main as education_main

        education_main._engine = fake_engine
        app = education_main.app
        app.router.lifespan_context = None

        yield TestClient(app, raise_server_exceptions=True), education_main.settings


def _issue_access_token(user: FakeUser, secret: str) -> str:
    return issue_token_pair(
        user_id=str(user.id),
        role=user.role,
        wallet_id=None,
        secret=secret,
    ).access_token


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def test_public_users_can_list_courses(client):
    app_client, _ = client
    newest = _make_course(
        category="bitcoin",
        difficulty="beginner",
        created_at=datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc),
    )
    older = _make_course(
        category="bitcoin",
        difficulty="beginner",
        created_at=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
    )
    list_courses_mock = AsyncMock(return_value=[newest, older])

    with patch("services.education.main.list_courses", list_courses_mock):
        response = app_client.get("/courses?category=bitcoin&difficulty=beginner&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] == str(newest.id)
    assert body["courses"] == [
        {
            "id": str(newest.id),
            "title": newest.title,
            "description": newest.description,
            "category": newest.category,
            "difficulty": newest.difficulty,
        }
    ]
    list_courses_mock.assert_awaited_once_with(
        ANY,
        category="bitcoin",
        difficulty="beginner",
    )


def test_public_users_can_view_course_details(client):
    app_client, _ = client
    course = _make_course()

    with patch("services.education.main.get_course_by_id", AsyncMock(return_value=course)):
        response = app_client.get(f"/courses/{course.id}")

    assert response.status_code == 200
    assert response.json()["course"] == {
        "id": str(course.id),
        "title": course.title,
        "description": course.description,
        "category": course.category,
        "difficulty": course.difficulty,
        "content_url": course.content_url,
    }


def test_authenticated_user_can_enroll_in_course(client):
    app_client, settings = client
    learner = _make_fake_user()
    access_token = _issue_access_token(learner, settings.jwt_secret)
    course = _make_course()
    enrollment = _make_enrollment(user_id=learner.id, course_id=course.id)
    create_enrollment_mock = AsyncMock(return_value=enrollment)

    with (
        patch("services.education.main.get_user_by_id", AsyncMock(return_value=learner)),
        patch("services.education.main.get_course_by_id", AsyncMock(return_value=course)),
        patch("services.education.main.get_enrollment_by_user_course", AsyncMock(return_value=None)),
        patch("services.education.main.create_enrollment", create_enrollment_mock),
    ):
        response = app_client.post(
            f"/courses/{course.id}/enroll",
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 201
    assert response.json()["enrollment"] == {
        "id": str(enrollment.id),
        "course_id": str(course.id),
        "progress": 0.0,
        "enrolled_at": enrollment.enrolled_at.isoformat().replace("+00:00", "Z"),
        "completed_at": None,
    }
    create_enrollment_mock.assert_awaited_once_with(
        ANY,
        user_id=str(learner.id),
        course_id=course.id,
    )


def test_enrollment_is_unique_per_user_and_course(client):
    app_client, settings = client
    learner = _make_fake_user()
    access_token = _issue_access_token(learner, settings.jwt_secret)
    course = _make_course()
    existing_enrollment = _make_enrollment(user_id=learner.id, course_id=course.id)

    with (
        patch("services.education.main.get_user_by_id", AsyncMock(return_value=learner)),
        patch("services.education.main.get_course_by_id", AsyncMock(return_value=course)),
        patch("services.education.main.get_enrollment_by_user_course", AsyncMock(return_value=existing_enrollment)),
        patch("services.education.main.create_enrollment", AsyncMock()) as create_enrollment_mock,
    ):
        response = app_client.post(
            f"/courses/{course.id}/enroll",
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "enrollment_exists",
        "message": "You are already enrolled in this course.",
    }
    create_enrollment_mock.assert_not_called()


def test_authenticated_user_can_update_own_progress(client):
    app_client, settings = client
    learner = _make_fake_user()
    access_token = _issue_access_token(learner, settings.jwt_secret)
    course = _make_course()
    existing_enrollment = _make_enrollment(user_id=learner.id, course_id=course.id, progress="20.00")
    completed_at = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
    updated_enrollment = existing_enrollment._replace(
        progress=Decimal("100.00"),
        completed_at=completed_at,
    )
    update_progress_mock = AsyncMock(return_value=updated_enrollment)

    with (
        patch("services.education.main.get_user_by_id", AsyncMock(return_value=learner)),
        patch("services.education.main.get_enrollment_by_id", AsyncMock(return_value=existing_enrollment)),
        patch("services.education.main.update_enrollment_progress", update_progress_mock),
    ):
        response = app_client.patch(
            f"/enrollments/{existing_enrollment.id}",
            headers=_auth_headers(access_token),
            json={"progress": 100},
        )

    assert response.status_code == 200
    assert response.json()["enrollment"] == {
        "id": str(existing_enrollment.id),
        "course_id": str(course.id),
        "progress": 100.0,
        "enrolled_at": existing_enrollment.enrolled_at.isoformat().replace("+00:00", "Z"),
        "completed_at": "2026-04-14T15:00:00Z",
    }
    update_progress_mock.assert_awaited_once_with(
        ANY,
        enrollment_id=existing_enrollment.id,
        user_id=str(learner.id),
        progress=100.0,
    )


def test_progress_update_rejects_values_outside_0_to_100(client):
    app_client, settings = client
    learner = _make_fake_user()
    access_token = _issue_access_token(learner, settings.jwt_secret)
    existing_enrollment = _make_enrollment(user_id=learner.id, course_id=uuid.uuid4(), progress="20.00")

    with (
        patch("services.education.main.get_user_by_id", AsyncMock(return_value=learner)),
        patch("services.education.main.get_enrollment_by_id", AsyncMock(return_value=existing_enrollment)),
        patch("services.education.main.update_enrollment_progress", AsyncMock()) as update_progress_mock,
    ):
        response = app_client.patch(
            f"/enrollments/{existing_enrollment.id}",
            headers=_auth_headers(access_token),
            json={"progress": 150},
        )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "validation_error",
        "message": "Request payload failed validation.",
    }
    update_progress_mock.assert_not_called()


def test_progress_update_rejects_other_users_enrollment(client):
    app_client, settings = client
    learner = _make_fake_user()
    other_user = _make_fake_user()
    access_token = _issue_access_token(learner, settings.jwt_secret)
    existing_enrollment = _make_enrollment(user_id=other_user.id, course_id=uuid.uuid4(), progress="20.00")

    with (
        patch("services.education.main.get_user_by_id", AsyncMock(return_value=learner)),
        patch("services.education.main.get_enrollment_by_id", AsyncMock(return_value=existing_enrollment)),
        patch("services.education.main.update_enrollment_progress", AsyncMock()) as update_progress_mock,
    ):
        response = app_client.patch(
            f"/enrollments/{existing_enrollment.id}",
            headers=_auth_headers(access_token),
            json={"progress": 45.5},
        )

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "forbidden",
        "message": "You do not have permission to access this resource.",
    }
    update_progress_mock.assert_not_called()


def test_update_enrollment_progress_db_marks_completion(education_settings):
    with patch.dict(os.environ, education_settings, clear=False):
        for module_name in ("services.education.db", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.education.db as education_db

    enrollment_id = uuid.uuid4()
    user_id = uuid.uuid4()
    updated_row = {
        "id": enrollment_id,
        "user_id": user_id,
        "course_id": uuid.uuid4(),
        "progress": Decimal("100.00"),
        "enrolled_at": datetime.now(tz=timezone.utc),
        "completed_at": datetime.now(tz=timezone.utc),
    }
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(return_value=_FetchOneResult(updated_row))
    fake_conn.commit = AsyncMock()

    result = asyncio.run(
        education_db.update_enrollment_progress(
            fake_conn,
            enrollment_id=enrollment_id,
            user_id=user_id,
            progress=100,
        )
    )

    assert result == updated_row
    fake_conn.commit.assert_awaited_once()


def test_create_enrollment_db_commits_new_record(education_settings):
    with patch.dict(os.environ, education_settings, clear=False):
        for module_name in ("services.education.db", "common", "common.config"):
            sys.modules.pop(module_name, None)

        import services.education.db as education_db

    enrollment_row = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "progress": Decimal("0.00"),
        "enrolled_at": datetime.now(tz=timezone.utc),
        "completed_at": None,
    }
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock(return_value=_FetchOneResult(enrollment_row))
    fake_conn.commit = AsyncMock()

    result = asyncio.run(
        education_db.create_enrollment(
            fake_conn,
            user_id=enrollment_row["user_id"],
            course_id=enrollment_row["course_id"],
        )
    )

    assert result == enrollment_row
    fake_conn.commit.assert_awaited_once()
