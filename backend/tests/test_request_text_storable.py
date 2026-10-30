"""BUG-537: request text PostgreSQL cannot store (U+0000) must be refused as a
422 naming the field, never reach asyncpg and become a 500. A 422 must also
never fail while reporting the rejected input back, which is what a lone
UTF-16 surrogate used to do. No DB: the request is refused before any handler
or query runs, and the stubbed session makes any input that slips through fail
loudly instead of passing."""

import json
from types import SimpleNamespace as Row
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.auth.dependencies import get_current_instructor
from app.db import TEXT_NOT_STORABLE, DbText, db_text, get_session
from app.main import app

NUL = "a\x00b"
SURROGATE = "a\ud800b"
INSTRUCTOR_ID = 9


def _resolve(schema, components):
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _text_schema(schema, components):
    """The string branch of a schema if it is free text (no enum, pattern or
    format of its own), else None."""
    schema = _resolve(schema, components)
    for alt in [schema, *schema.get("anyOf", [])]:
        alt = _resolve(alt, components)
        if alt.get("type") == "array":
            alt = _resolve(alt.get("items", {}), components)
        if alt.get("type") == "string":
            if any(
                key in alt for key in ("enum", "const", "pattern", "format", "contentMediaType")
            ):
                return None
            return alt
    return None


def _body_text_trails(schema, components, trail="", seen=frozenset()):
    """Field trails (a.b[].c) of every free-text field a request body carries."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name not in seen:
            yield from _body_text_trails(components[name], components, trail, seen | {name})
        return
    for alt in schema.get("anyOf", []):
        yield from _body_text_trails(alt, components, trail, seen)
    if trail and _text_schema(schema, components) is not None:
        yield trail
        return
    if schema.get("type") == "array" and "items" in schema:
        yield from _body_text_trails(schema["items"], components, trail + "[]", seen)
    for name, sub in schema.get("properties", {}).items():
        yield from _body_text_trails(sub, components, f"{trail}.{name}" if trail else name, seen)


def _text_inputs():
    """Every free-text request input, as (operation, location, trail, params)."""
    spec = app.openapi()
    components = spec["components"]["schemas"]
    found = []
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            operation_id = f"{method.upper()} {path}"
            params = operation.get("parameters", [])
            for param in params:
                if param["in"] in ("path", "query") and _text_schema(
                    param.get("schema", {}), components
                ):
                    found.append((operation_id, param["in"], param["name"], params))
            for ctype, media in operation.get("requestBody", {}).get("content", {}).items():
                for trail in _body_text_trails(media.get("schema", {}), components):
                    found.append((operation_id, ctype.split(";")[0], trail, params))
    return found


# Passwords are only ever hashed, never stored or compared as text, so they
# stay plain str: refusing one would lock out an account for no reason.
NOT_STORED = {
    ("POST /auth/login", "password"),
    ("POST /auth/password", "current_password"),
    ("POST /auth/password", "new_password"),
}
TEXT_INPUTS = [row for row in _text_inputs() if (row[0], row[2]) not in NOT_STORED]


@pytest.fixture
def client():
    saved = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_current_instructor] = lambda: Row(id=INSTRUCTOR_ID)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def _fill_path(path, params, target_name=None, value=None):
    url = path
    for param in params:
        if param["in"] != "path":
            continue
        given = value if param["name"] == target_name else "1"
        schema = param.get("schema", {})
        if param["name"] != target_name and schema.get("type") == "string":
            given = "a" * 64  # satisfies the hex-identity patterns
        url = url.replace("{" + param["name"] + "}", quote(given, safe=""))
    return url


def _body_with(trail, value):
    body = value
    for step in reversed(trail.split(".")):
        name, lists = step.rstrip("[]"), step.count("[]")
        for _ in range(lists):
            body = [body]
        body = {name: body}
    return body


def _loc_for(trail):
    loc = []
    for step in trail.split("."):
        loc.append(step.rstrip("[]"))
        loc.extend([0] * step.count("[]"))
    return loc


def test_the_walk_finds_the_text_inputs():
    where = {location for _, location, _, _ in TEXT_INPUTS}
    assert len(TEXT_INPUTS) > 40
    assert {"path", "query", "application/json", "multipart/form-data"} <= where


@pytest.mark.parametrize(
    "operation,location,field,params",
    TEXT_INPUTS,
    ids=[f"{op} {location}:{field}" for op, location, field, _ in TEXT_INPUTS],
)
def test_text_postgres_cannot_store_is_refused_not_a_server_error(
    client, operation, location, field, params
):
    method, path = operation.split(" ", 1)
    headers = {"X-Veridical-Expected-Instructor": str(INSTRUCTOR_ID)}
    if location == "path":
        url = _fill_path(path, params, field, NUL)
        response = client.request(method, url, headers=headers)
        wanted = ["path", field]
    elif location == "query":
        url = _fill_path(path, params)
        response = client.request(method, url, params={field: NUL}, headers=headers)
        wanted = ["query", field]
    elif location == "application/json":
        url = _fill_path(path, params)
        response = client.request(method, url, json=_body_with(field, NUL), headers=headers)
        wanted = ["body", *_loc_for(field)]
    else:
        url = _fill_path(path, params)
        name = field.rstrip("[]")
        response = client.request(
            method,
            url,
            data={name: NUL},
            files={"file": ("a.pdf", b"%PDF-1.4\n", "application/pdf")},
            headers=headers,
        )
        wanted = None

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    if wanted is None:
        # Library submissions validate their form through a model, so the
        # error names the field without the "body" prefix.
        assert any(error["loc"][-1] in (field.rstrip("[]"), 0) for error in errors), errors
    else:
        assert any(list(error["loc"])[: len(wanted)] == wanted for error in errors), errors


class _Note(BaseModel):
    note: db_text(10) | None = None


class _Names(BaseModel):
    names: list[DbText]


@pytest.mark.parametrize(
    "text",
    [
        "Juan Dela Cruz",
        "Señora Peña",
        "naïve café",
        "资料",
        "🙂 ok",
        " spaced ",
        "line\nbreak\ttab",
    ],
)
def test_ordinary_text_is_kept_as_is(text):
    assert _Names(names=[text]).names == [text]


@pytest.mark.parametrize("text", [NUL, "\x00", "\x00 at the start", "at the end \x00"])
def test_text_postgres_cannot_store_is_refused(text):
    with pytest.raises(ValidationError) as caught:
        _Names(names=[text])
    error = caught.value.errors()[0]
    assert error["type"] == "text_not_storable"
    assert error["msg"] == TEXT_NOT_STORABLE


@pytest.mark.parametrize("escape", ["\\ud800", "\\udfff"])
def test_a_lone_surrogate_in_json_is_refused_by_the_json_parser(escape):
    # Request bodies are parsed as JSON, where a lone surrogate escape is
    # refused before DbText runs; query, path and form text arrive as decoded
    # UTF-8 and cannot carry one.
    with pytest.raises(ValidationError) as caught:
        _Names.model_validate_json('{"names": ["a' + escape + 'b"]}')
    assert caught.value.errors()[0]["type"] in ("json_invalid", "string_unicode")


def test_the_fields_own_limits_still_apply():
    assert _Note(note=None).note is None
    with pytest.raises(ValidationError) as caught:
        _Note(note="x" * 11)
    assert caught.value.errors()[0]["type"] == "string_too_long"


def test_optional_library_text_keeps_its_length_message():
    from app.library.schemas import LibraryEntryUpdate, LibrarySubmissionIn

    for model in (LibraryEntryUpdate, LibrarySubmissionIn):
        with pytest.raises(ValidationError) as caught:
            model(title="x" * 501)
        error = caught.value.errors()[0]
        assert error["type"] == "string_too_long", error
        assert error["msg"] == "String should have at most 500 characters"


@pytest.mark.parametrize("path", ["/auth/me", "/manuscripts"])
def test_a_session_cookie_postgres_cannot_hold_is_no_session(path):
    # "\000" in a quoted cookie value decodes to U+0000; it must read as
    # signed out, not reach the session lookup.
    saved = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: object()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(path, headers={"cookie": 'veridical_session="\\000"'})
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)

    assert response.status_code == 401, response.text


def test_signing_out_with_such_a_cookie_does_not_fail():
    saved = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: object()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/auth/logout", headers={"cookie": 'veridical_session="\\000"'})
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)

    assert response.status_code < 500, response.text


def test_a_surrogate_in_a_text_field_is_reported_as_a_422(client):
    # Refused by the str type, then echoed back in the error: the default UTF-8
    # rendering of that echo is what used to fail with a 500.
    content = json.dumps({"reason": SURROGATE, "expected_revision": 0})
    response = client.post(
        "/flags/1/override", content=content, headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.content.isascii()
    errors = response.json()["detail"]
    assert any(error["loc"] == ["body", "reason"] for error in errors)


def test_a_surrogate_in_a_non_text_field_is_still_reported_as_a_422(client):
    # The share link's expiry is a datetime: the field refuses the value, and
    # reporting it back (the error echoes the input) must not fail.
    content = json.dumps({"expires_at": SURROGATE})
    response = client.post(
        "/check-runs/1/share", content=content, headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.content.isascii()
    assert response.json()["detail"]


def test_the_422_body_keeps_fastapis_shape(client):
    response = client.get("/library", params={"page": "x"})

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert {"type", "loc", "msg", "input"} <= set(error)
    assert error["loc"] == ["query", "page"]
