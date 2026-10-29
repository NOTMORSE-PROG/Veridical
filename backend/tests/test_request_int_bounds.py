"""BUG-536: an integer in a URL path, query, header or JSON body that
PostgreSQL's BIGINT cannot hold must be refused as a client error, never reach
asyncpg and become a 500. No DB: the request is refused before any handler or
query runs."""

from types import SimpleNamespace as Row

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import BIGINT_MAX, BIGINT_MIN, get_session
from app.main import app


def _int_schema(schema):
    if schema.get("type") == "integer":
        return schema
    return next((alt for alt in schema.get("anyOf", []) if alt.get("type") == "integer"), None)


def _int_params():
    found = []
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            params = operation.get("parameters", [])
            for param in params:
                schema = _int_schema(param.get("schema", {}))
                if schema is not None:
                    found.append((method, path, param, schema, params))
    return found


def _body_ints():
    """Every integer field a JSON request body can carry, nested models and
    list items included, as (operation, field trail, schema)."""
    spec = app.openapi()
    components = spec["components"]["schemas"]
    found = []

    def walk(schema, operation, trail, seen):
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name not in seen:
                walk(components[name], operation, trail, seen | {name})
            return
        for alt in schema.get("anyOf", []) + schema.get("oneOf", []) + schema.get("allOf", []):
            walk(alt, operation, trail, seen)
        if schema.get("type") == "integer":
            found.append((operation, trail, schema))
        if schema.get("type") == "array" and "items" in schema:
            walk(schema["items"], operation, trail + "[]", seen)
        for name, sub in schema.get("properties", {}).items():
            walk(sub, operation, f"{trail}.{name}", seen)

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for media in operation.get("requestBody", {}).get("content", {}).values():
                walk(media.get("schema", {}), f"{method.upper()} {path}", "body", frozenset())
    return found


def _fits_bigint(schema):
    if "minimum" not in schema or "maximum" not in schema:
        return False
    # FastAPI's OpenAPI model stores body bounds as floats, and BIGINT_MAX
    # rounds up to 2**63 there; compare at the precision the schema has.
    low, high = schema["minimum"], schema["maximum"]
    if isinstance(low, float) or isinstance(high, float):
        return float(low) >= float(BIGINT_MIN) and float(high) <= float(BIGINT_MAX)
    return low >= BIGINT_MIN and high <= BIGINT_MAX


INT_PARAMS = _int_params()
BODY_INTS = _body_ints()
INSTRUCTOR_ID = 9


def test_every_integer_request_parameter_fits_bigint():
    assert len(INT_PARAMS) > 50  # the walk really found the id routes
    loose = [
        f"{method.upper()} {path} {param['in']}:{param['name']} {schema}"
        for method, path, param, schema, _ in INT_PARAMS
        if not _fits_bigint(schema)
    ]
    assert loose == [], "type these parameters DbInt (app.db): " + "; ".join(loose)


def test_list_paging_cannot_overflow_the_query_offset():
    # Each bound alone fitting BIGINT is not enough: the list query computes
    # OFFSET (page - 1) * page_size, which must fit as well.
    paged = {}
    for method, path, param, schema, _ in INT_PARAMS:
        if param["in"] == "query" and param["name"] in ("page", "page_size"):
            paged.setdefault((method, path), {})[param["name"]] = schema.get("maximum")
    pairs = {key: bounds for key, bounds in paged.items() if len(bounds) == 2}
    assert len(pairs) >= 4  # archive, audit, library, manuscripts
    overflow = [
        f"{method.upper()} {path} {bounds}"
        for (method, path), bounds in pairs.items()
        if (bounds["page"] - 1) * bounds["page_size"] > BIGINT_MAX
    ]
    assert overflow == [], "cap the page number: " + "; ".join(overflow)


@pytest.mark.parametrize("path", ["/archive", "/library", "/manuscripts", "/audit"])
def test_page_past_the_list_cap_is_refused(client, path):
    cap = max(get_settings().list_max_page, get_settings().audit_list_max_page)

    response = client.get(path, params={"page": cap + 1})

    assert response.status_code == 422, response.text
    assert any(error["loc"][-1] == "page" for error in response.json()["detail"])


def test_every_integer_body_field_fits_bigint():
    assert len(BODY_INTS) > 20  # the walk really reached the request models
    loose = [
        f"{operation} {trail} {schema}"
        for operation, trail, schema in BODY_INTS
        if not _fits_bigint(schema)
    ]
    assert loose == [], "type these fields DbInt (app.db): " + "; ".join(loose)


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


def _request(method, path, target, value, params):
    url, query, headers = path, {}, {}
    for param in params:
        name, where = param["name"], param["in"]
        if param is target:
            given = str(value)
        elif _int_schema(param.get("schema", {})) is not None:
            # An expected-instructor header must name the signed-in instructor.
            given = str(INSTRUCTOR_ID) if where == "header" else "1"
        elif where == "path":
            given = "a" * 64
        else:
            continue
        if where == "path":
            url = url.replace("{" + name + "}", given)
        elif where == "query":
            query[name] = given
        elif where == "header":
            headers[name] = given
    return method.upper(), url, query, headers


@pytest.mark.parametrize("value", [BIGINT_MAX + 1, BIGINT_MIN - 1])
@pytest.mark.parametrize(
    "method,path,target,params",
    [(m, p, param, params) for m, p, param, _, params in INT_PARAMS],
    ids=[f"{m.upper()} {p} {param['name']}" for m, p, param, _, _ in INT_PARAMS],
)
def test_integer_outside_bigint_is_refused_not_a_server_error(
    client, method, path, target, params, value
):
    method, url, query, headers = _request(method, path, target, value, params)
    response = client.request(method, url, params=query, headers=headers)

    assert response.status_code == 422, response.text
    assert any(error["loc"][-1] == target["name"] for error in response.json()["detail"])


# The body fields that reached a query unbounded and returned 500 against a real
# database before this fix.
FAMILY = "d702b00a-c18f-4053-af7f-b7e365a3a6e8"
BODY_CASES = [
    ("PATCH", "/manuscripts/1/group", "program_id"),
    ("POST", "/check-runs", "manuscript_id"),
    ("PUT", f"/rubric-families/{FAMILY}/program", "program_id"),
]


@pytest.mark.parametrize("method,url,field", BODY_CASES)
def test_body_id_outside_bigint_is_refused_not_a_server_error(client, method, url, field):
    response = client.request(
        method,
        url,
        json={field: BIGINT_MAX + 1},
        headers={"X-Veridical-Expected-Instructor": str(INSTRUCTOR_ID)},
    )

    assert response.status_code == 422, response.text
    assert any(
        error["loc"][-1] == field and error["type"] == "less_than_equal"
        for error in response.json()["detail"]
    )
