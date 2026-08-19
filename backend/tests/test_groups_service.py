"""V-062 unit tests: name normalization is pure and needs no DB.

`app/groups/service.py::normalize_group_name` must stay byte-for-byte in
sync with migration 0025's backfill SQL
(`lower(trim(regexp_replace(x, '\\s+', ' ', 'g')))`) -- see that
function's own docstring for why. These cases are chosen to exercise
exactly what that SQL expression does: collapse internal whitespace runs,
trim the boundary, lowercase. The live cross-check against the REAL SQL
(not just Python's own idea of what the SQL does) lives in
`test_migration_0025_backfill.py`, since it needs a database connection.
"""

from app.groups.service import DEFAULT_GROUP_LABEL, normalize_group_name


def test_case_and_whitespace_variants_normalize_to_the_same_key():
    assert normalize_group_name("Group 4") == normalize_group_name("group 4")
    assert normalize_group_name("  Group   4  ") == normalize_group_name("Group 4")
    assert normalize_group_name("GROUP 4") == normalize_group_name("group 4")


def test_distinct_names_normalize_to_distinct_keys():
    assert normalize_group_name("Group 4") != normalize_group_name("Group 5")


def test_default_label_normalizes_consistently():
    assert normalize_group_name(DEFAULT_GROUP_LABEL) == normalize_group_name("ungrouped")


def test_boundary_tab_normalizes_the_same_as_a_boundary_space():
    """`backend-critic` (V-062 review), live-reproduced: a leading/trailing
    TAB is not "whitespace" to Postgres's `trim()` (ASCII-space-only), only
    to `regexp_replace(..., '\\s+', ...)` -- collapse-then-trim (the fixed
    order) makes this a non-issue on the Python side; this pins that."""
    assert normalize_group_name("\tTab Team\t") == normalize_group_name("Tab Team")
    assert normalize_group_name("\tTab Team\t") == normalize_group_name(" Tab Team ")
