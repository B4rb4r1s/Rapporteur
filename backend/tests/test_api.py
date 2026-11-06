from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from rapporteur_backend import tables as t
from rapporteur_backend.db import LOCAL_USER_ID
from rapporteur_backend.migrate import MIGRATION_HEAD, migrate
from rapporteur_contracts import ApiError, Conference, Person, Report, Section, Talk


def conference(client: TestClient, title: str = "Конференция") -> str:
    response = client.post("/api/v1/conferences", json={"title": title})
    assert response.status_code == 201, response.text
    Conference.model_validate(response.json())
    assert response.headers["etag"] == '"1"'
    return str(response.json()["id"])


def section(client: TestClient, conference_id: str) -> str:
    response = client.post(
        f"/api/v1/conferences/{conference_id}/sections",
        json={"title": "Секция", "date": "2026-11-04"},
    )
    assert response.status_code == 201, response.text
    Section.model_validate(response.json())
    return str(response.json()["id"])


def talk(client: TestClient, section_id: str) -> str:
    response = client.post(f"/api/v1/sections/{section_id}/talks", json={"title": "Доклад"})
    assert response.status_code == 201, response.text
    Talk.model_validate(response.json())
    return str(response.json()["id"])


def error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    value = ApiError.model_validate(response.json())
    assert value.code.value == code
    assert value.request_id == response.headers["x-request-id"]


def test_migration_is_idempotent_and_seeds_local(database: Engine) -> None:
    migrate(database)
    with database.connect() as conn:
        assert [tuple(row) for row in conn.execute(sa.select(t.users))] == [
            (LOCAL_USER_ID, "local")
        ]
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == MIGRATION_HEAD


def test_conference_crud_and_etag(client: TestClient, database: Engine) -> None:
    cid = conference(client)
    response = client.get(f"/api/v1/conferences/{cid}")
    assert response.json()["owner_id"] == str(LOCAL_USER_ID)
    replaced = client.put(
        f"/api/v1/conferences/{cid}",
        headers={"If-Match": response.headers["etag"]},
        json={"title": "Новое название"},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["version"] == 2 and replaced.headers["etag"] == '"2"'
    with database.connect() as conn:
        assert conn.scalar(sa.select(t.conferences.c.edited_by)) == LOCAL_USER_ID
    page = client.get("/api/v1/conferences?limit=1&offset=0").json()
    assert page["page"] == {"total": 1, "limit": 1, "offset": 0}
    assert page["items"][0]["title"] == "Новое название"
    assert (
        client.delete(f"/api/v1/conferences/{cid}", headers={"If-Match": '"2"'}).status_code == 204
    )
    error(client.get(f"/api/v1/conferences/{cid}"), 404, "not_found")


def test_stale_update_and_delete_preserve_data(client: TestClient) -> None:
    cid = conference(client)
    assert (
        client.put(
            f"/api/v1/conferences/{cid}", json={"title": "Первое"}, headers={"If-Match": '"1"'}
        ).status_code
        == 200
    )
    error(
        client.put(
            f"/api/v1/conferences/{cid}",
            json={"title": "Потерянная правка"},
            headers={"If-Match": '"1"'},
        ),
        409,
        "version_conflict",
    )
    error(
        client.delete(f"/api/v1/conferences/{cid}", headers={"If-Match": '"1"'}),
        409,
        "version_conflict",
    )
    assert client.get(f"/api/v1/conferences/{cid}").json()["title"] == "Первое"


def test_concurrent_edits_have_one_winner(client: TestClient) -> None:
    cid = conference(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda title: client.put(
                    f"/api/v1/conferences/{cid}", headers={"If-Match": '"1"'}, json={"title": title}
                ),
                ["A", "B"],
            )
        )
    assert sorted(result.status_code for result in results) == [200, 409]
    assert client.get(f"/api/v1/conferences/{cid}").json()["version"] == 2


def test_sections_have_etag_without_changing_contract(client: TestClient) -> None:
    sid = section(client, conference(client))
    current = client.get(f"/api/v1/sections/{sid}")
    assert "version" not in current.json() and current.headers["etag"] == '"1"'
    updated = client.put(
        f"/api/v1/sections/{sid}", headers={"If-Match": '"1"'}, json={"title": "Другая", "order": 2}
    )
    assert updated.status_code == 200 and updated.headers["etag"] == '"2"'
    error(
        client.put(
            f"/api/v1/sections/{sid}", headers={"If-Match": '"1"'}, json={"title": "Устарело"}
        ),
        409,
        "version_conflict",
    )


def test_at_most_five_sections(client: TestClient) -> None:
    cid = conference(client)
    for _ in range(5):
        section(client, cid)
    error(
        client.post(f"/api/v1/conferences/{cid}/sections", json={"title": "Шестая"}),
        422,
        "validation_error",
    )


def test_talk_participants_statuses_and_person_fk(client: TestClient) -> None:
    person = client.post("/api/v1/people", json={"full_name": "Иванов И. И."})
    assert person.status_code == 201
    Person.model_validate(person.json())
    pid = person.json()["id"]
    sid = section(client, conference(client))
    payload = {
        "title": "Доклад",
        "participants": [{"person_id": pid, "role": "speaker", "order": 1}],
    }
    response = client.post(f"/api/v1/sections/{sid}/talks", json=payload)
    assert response.status_code == 201, response.text
    tid = response.json()["id"]
    assert response.json()["participants"] == payload["participants"]
    assert (
        client.put(
            f"/api/v1/talks/{tid}",
            headers={"If-Match": '"1"'},
            json={**payload, "status": "matched"},
        ).status_code
        == 200
    )
    error(
        client.put(
            f"/api/v1/talks/{tid}",
            headers={"If-Match": '"2"'},
            json={**payload, "status": "reviewed"},
        ),
        409,
        "version_conflict",
    )
    error(
        client.delete(f"/api/v1/people/{pid}", headers={"If-Match": '"1"'}), 422, "validation_error"
    )
    assert client.get(f"/api/v1/people/{pid}").status_code == 200
    assert client.get(f"/api/v1/sections/{sid}/talks").json()["page"]["total"] == 1


def test_report_selection_versions_and_cascade(client: TestClient) -> None:
    cid = conference(client)
    sid = section(client, cid)
    tid = talk(client, sid)
    payload = {"selection": {"days": ["2026-11-04"], "section_ids": [sid], "talk_ids": [tid]}}
    report = client.post(f"/api/v1/conferences/{cid}/reports", json=payload)
    assert report.status_code == 201, report.text
    parsed = Report.model_validate(report.json())
    assert parsed.author_id == LOCAL_USER_ID
    rid = parsed.id
    assert client.get(f"/api/v1/reports/{rid}/versions").json()["items"] == []
    error(
        client.delete(f"/api/v1/talks/{tid}", headers={"If-Match": '"1"'}), 422, "validation_error"
    )
    assert client.get(f"/api/v1/talks/{tid}").status_code == 200
    updated = client.put(
        f"/api/v1/reports/{rid}",
        headers={"If-Match": '"1"'},
        json={**payload, "template_version": "starter-2"},
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert (
        client.delete(f"/api/v1/conferences/{cid}", headers={"If-Match": '"1"'}).status_code == 204
    )
    error(client.get(f"/api/v1/talks/{tid}"), 404, "not_found")
    error(client.get(f"/api/v1/reports/{rid}"), 404, "not_found")


def test_report_rejects_foreign_talk_and_wrong_day(client: TestClient) -> None:
    cid = conference(client)
    sid = section(client, cid)
    foreign = talk(client, section(client, conference(client, "Другая")))
    payload = {"selection": {"days": [], "section_ids": [], "talk_ids": [foreign]}}
    error(client.post(f"/api/v1/conferences/{cid}/reports", json=payload), 422, "validation_error")
    local = talk(client, sid)
    payload["selection"] = {"days": ["2020-01-01"], "section_ids": [], "talk_ids": [local]}
    error(client.post(f"/api/v1/conferences/{cid}/reports", json=payload), 422, "validation_error")
    assert client.get(f"/api/v1/conferences/{cid}/reports").json()["items"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"owner_id": str(uuid4())},
        {"version": 5},
        {"start_date": "2026-11-05", "end_date": "2026-11-04"},
    ],
)
def test_bad_conference_payload_is_not_saved(client: TestClient, body: dict[str, Any]) -> None:
    error(client.post("/api/v1/conferences", json=body), 422, "validation_error")
    assert client.get("/api/v1/conferences").json()["page"]["total"] == 0


@pytest.mark.parametrize("suffix", ["?limit=0", "?limit=101", "?offset=-1"])
def test_pagination_is_validated(client: TestClient, suffix: str) -> None:
    error(client.get("/api/v1/conferences" + suffix), 422, "validation_error")


def test_validation_404_method_and_missing_version_share_error_format(client: TestClient) -> None:
    cid = conference(client)
    error(client.get(f"/api/v1/conferences/{uuid4()}"), 404, "not_found")
    error(client.get("/api/v1/conferences/not-a-uuid"), 422, "validation_error")
    error(client.put(f"/api/v1/conferences/{cid}", json={}), 422, "validation_error")
    response = client.patch(f"/api/v1/conferences/{cid}", json={})
    error(response, 405, "validation_error")
    assert "GET" in response.headers["allow"]


def test_ready_and_offline_openapi(client: TestClient) -> None:
    assert client.get("/ready").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/conferences" in schema["paths"]
    assert "/api/v1/jobs/{job_id}/cancel" in schema["paths"]
    assert client.get("/docs").status_code == 404


def test_person_update_and_delete_and_section_delete(client: TestClient) -> None:
    response = client.post("/api/v1/people", json={"full_name": "Автор"})
    pid = response.json()["id"]
    updated = client.put(
        f"/api/v1/people/{pid}",
        headers={"If-Match": '"1"'},
        json={"full_name": "Автор 2", "affiliation": "Институт"},
    )
    assert updated.status_code == 200 and updated.headers["etag"] == '"2"'
    assert client.get("/api/v1/people").json()["page"]["total"] == 1
    assert client.delete(f"/api/v1/people/{pid}", headers={"If-Match": '"2"'}).status_code == 204
    sid = section(client, conference(client))
    assert client.delete(f"/api/v1/sections/{sid}", headers={"If-Match": '"1"'}).status_code == 204
    error(client.get(f"/api/v1/sections/{sid}"), 404, "not_found")


def test_report_delete_removes_selection_constraints(client: TestClient) -> None:
    cid = conference(client)
    tid = talk(client, section(client, cid))
    payload = {"selection": {"days": [], "section_ids": [], "talk_ids": [tid]}}
    rid = client.post(f"/api/v1/conferences/{cid}/reports", json=payload).json()["id"]
    assert client.delete(f"/api/v1/reports/{rid}", headers={"If-Match": '"1"'}).status_code == 204
    assert client.delete(f"/api/v1/talks/{tid}", headers={"If-Match": '"1"'}).status_code == 204


def test_edited_by_uses_local_for_all_mutable_objects(client: TestClient, database: Engine) -> None:
    cid = conference(client)
    sid = section(client, cid)
    tid = talk(client, sid)
    client.post("/api/v1/people", json={"full_name": "Автор"})
    client.post(
        f"/api/v1/conferences/{cid}/reports",
        json={"selection": {"days": [], "section_ids": [], "talk_ids": [tid]}},
    )
    with database.connect() as conn:
        for table in [t.conferences, t.sections, t.talks, t.people, t.reports]:
            assert conn.scalar(sa.select(table.c.edited_by)) == LOCAL_USER_ID


def test_migration_downgrade_and_upgrade(database: Engine) -> None:
    from alembic import command

    from rapporteur_backend.migrate import migration_config

    with database.begin() as conn:
        config = migration_config()
        config.attributes["connection"] = conn
        command.downgrade(config, "base")
        assert conn.scalar(sa.text("SELECT to_regclass('public.conferences')")) is None
    migrate(database)
    with database.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(t.users)) == 1


def test_database_errors_do_not_expose_connection_credentials() -> None:
    from rapporteur_backend.app import create_app

    with TestClient(
        create_app(
            database_url="postgresql+psycopg://absent:do-not-expose@127.0.0.1:55439/postgres"
        )
    ) as client:
        response = client.get("/api/v1/conferences")
    error(response, 500, "internal_error")
    assert "do-not-expose" not in response.text
    assert "SELECT" not in response.text


@pytest.mark.parametrize("value", ['W/"1"', "1", '"0"', '"' + "9" * 5000 + '"'])
def test_invalid_etag_is_a_validation_error(client: TestClient, value: str) -> None:
    cid = conference(client)
    error(
        client.put(
            f"/api/v1/conferences/{cid}",
            json={"title": "Не сохранено"},
            headers={"If-Match": value},
        ),
        422,
        "validation_error",
    )
    assert client.get(f"/api/v1/conferences/{cid}").json()["version"] == 1


def test_saved_openapi_is_current() -> None:
    import json
    from pathlib import Path

    from rapporteur_backend.app import create_app

    saved = json.loads((Path(__file__).parents[1] / "openapi.json").read_text(encoding="utf-8"))
    assert saved == create_app().openapi()


def test_migration_matches_application_metadata(database: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with database.connect() as conn:
        context = MigrationContext.configure(conn)
        assert compare_metadata(context, t.metadata) == []
