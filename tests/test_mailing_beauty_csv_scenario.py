"""Сценарій: Beauty новини.html + clients_20261008_1106.csv (як у проді)."""

import json
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from config import ROOT_DIR
from web.mailing_helpers import emails_from_upload_bytes
from web.mailing_body_staging import load_staged_body, save_staged_body
from web.routes.emails import (
    MailingSendJsonPayload,
    _mailing_custom_recipients_for_mode,
    _mailing_mode_from_target,
)

HTML_PATH = Path(ROOT_DIR) / "Beauty новини.html"
CSV_PATH = Path(ROOT_DIR) / "clients_20261008_1106.csv"


@pytest.fixture(scope="module")
def beauty_html() -> str:
    assert HTML_PATH.is_file(), f"Покладіть {HTML_PATH.name} у корінь проєкту"
    return HTML_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def clients_csv_bytes() -> bytes:
    assert CSV_PATH.is_file(), f"Покладіть {CSV_PATH.name} у корінь проєкту"
    return CSV_PATH.read_bytes()


def test_csv_parses_thousands_of_emails(clients_csv_bytes: bytes):
    emails, err = emails_from_upload_bytes("clients_20261008_1106.csv", clients_csv_bytes)
    assert err is None
    assert len(emails) >= 10_000, f"очікували ~13k email, отримали {len(emails)}"


def test_old_bug_payload_with_csv_in_custom_field_was_huge(clients_csv_bytes: bytes):
    """Раніше при mode=all у JSON летів весь textarea — часто туди вставляли CSV."""
    csv_text = clients_csv_bytes.decode("utf-8-sig", errors="ignore")
    old_payload = {
        "subject": "Beauty Новини",
        "body_key": "abc",
        "target_mode": "all",
        "custom_recipients": csv_text,
    }
    new_payload = {
        "subject": "Beauty Новини",
        "body_key": "abc",
        "target_mode": "all",
        "custom_recipients": _mailing_custom_recipients_for_mode("all", csv_text),
    }
    old_size = len(json.dumps(old_payload, ensure_ascii=False))
    new_size = len(json.dumps(new_payload, ensure_ascii=False))
    assert old_size > 800_000, f"старий payload мав бути ~1MB, зараз {old_size}"
    assert new_size < 5000, f"новий payload має бути малий, зараз {new_size}"


def test_staging_roundtrip_beauty_html(beauty_html: str, tmp_path, monkeypatch):
    monkeypatch.setattr("web.mailing_body_staging._STAGING_DIR", tmp_path)
    key = save_staged_body(beauty_html.encode("utf-8"), user="tester")
    loaded = load_staged_body(key, user="tester")
    assert loaded == beauty_html
    assert len(beauty_html) > 40_000


@pytest.fixture
def mailing_api_client(monkeypatch):
    from web.auth import get_current_user
    from web.main import app
    from web.routes.emails import _require_marketing_access
    from web.security import verify_csrf_dependency, verify_csrf_header_dependency

    monkeypatch.setattr("web.main.WEB_ALLOWED_HOSTS", ())

    from starlette.requests import Request as StarletteRequest

    def _user(request: StarletteRequest):
        return "mailing_tester"

    def _marketing(request: StarletteRequest):
        return None

    async def _csrf_header(request: StarletteRequest):
        return None

    async def _csrf_form(request: StarletteRequest, csrf_token: str = None):
        return None

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[_require_marketing_access] = _marketing
    app.dependency_overrides[verify_csrf_header_dependency] = _csrf_header
    app.dependency_overrides[verify_csrf_dependency] = _csrf_form
    with TestClient(app, raise_server_exceptions=True, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


def test_api_stage_body_and_send_json_with_csv_file(
    mailing_api_client: TestClient,
    beauty_html: str,
    clients_csv_bytes: bytes,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("web.mailing_body_staging._STAGING_DIR", tmp_path)
    monkeypatch.setattr("web.mailing_recipients_upload._UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)

    stage = mailing_api_client.post(
        "/clients/mailings/stage-body",
        content=beauty_html.encode("utf-8"),
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Accept": "application/json",
            "X-CSRF-Token": "test",
        },
    )
    assert stage.status_code == 200, stage.text
    body_key = stage.json()["body_key"]

    payload = MailingSendJsonPayload(
        subject="Beauty Новини: знижки, акції, подарунки 🎁",
        body_key=body_key,
        is_html=True,
        target_mode="custom",
        custom_recipients="",
        estimated_recipients=0,
    )
    payload_json = payload.model_dump()
    assert len(json.dumps(payload_json)) < 8000

    def _discard_task(coro):
        coro.close()
        return None

    with patch("web.routes.emails.find_active_mailing_job", return_value=None):
        with patch("web.routes.emails.asyncio.create_task", side_effect=_discard_task):
            resp = mailing_api_client.post(
                "/clients/mailings/send-json-with-file",
                data={"payload": json.dumps(payload_json, ensure_ascii=False)},
                files={
                    "recipients_file": (
                        "clients_20261008_1106.csv",
                        io.BytesIO(clients_csv_bytes),
                        "text/csv",
                    )
                },
                headers={"Accept": "application/json", "X-CSRF-Token": "test"},
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("ok") is True
    assert data.get("job_id")

    # mode=all + випадковий CSV у полі — у JSON його більше немає
    mode = _mailing_mode_from_target("all")
    csv_text = clients_csv_bytes.decode("utf-8-sig", errors="ignore")
    assert _mailing_custom_recipients_for_mode(mode, csv_text) == ""


def test_api_send_json_all_mode_tiny_payload(
    mailing_api_client: TestClient,
    beauty_html: str,
    clients_csv_bytes: bytes,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("web.mailing_body_staging._STAGING_DIR", tmp_path)

    stage = mailing_api_client.post(
        "/clients/mailings/stage-body",
        content=beauty_html.encode("utf-8"),
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Accept": "application/json",
            "X-CSRF-Token": "test",
        },
    )
    body_key = stage.json()["body_key"]
    csv_text = clients_csv_bytes.decode("utf-8-sig", errors="ignore")

    payload = {
        "subject": "Beauty Новини",
        "body_key": body_key,
        "is_html": True,
        "target_mode": "all",
        "custom_recipients": csv_text,
        "estimated_recipients": 12000,
    }
    # Сервер обрізає custom при all — емуляція як після фіксу на API
    payload["custom_recipients"] = _mailing_custom_recipients_for_mode("all", csv_text)
    assert len(json.dumps(payload)) < 8000

    def _discard_task(coro):
        coro.close()
        return None

    with patch("web.routes.emails.find_active_mailing_job", return_value=None):
        with patch("web.routes.emails.asyncio.create_task", side_effect=_discard_task):
            resp = mailing_api_client.post(
                "/clients/mailings/send-json",
                json=payload,
                headers={"Accept": "application/json", "X-CSRF-Token": "test"},
            )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("job_id")
