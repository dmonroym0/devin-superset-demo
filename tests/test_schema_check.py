import copy

from app.schema_check import load_local_triage_schema, schema_diff


def test_identical_schemas_have_no_diff():
    schema = load_local_triage_schema()
    assert schema_diff(schema, copy.deepcopy(schema)) is None


def test_changed_enum_names_the_path():
    schema = load_local_triage_schema()
    remote = copy.deepcopy(schema)
    remote["properties"]["cves"]["items"]["properties"]["confidence"]["enum"] = ["high", "low"]
    assert schema_diff(schema, remote) == "properties.cves.items.properties.confidence.enum"


def test_missing_key_and_list_item():
    assert schema_diff({"a": 1}, {"a": 1, "b": 2}) == "b"
    assert schema_diff({"r": ["x", "y"]}, {"r": ["x", "z"]}) == "r[1]"
    assert schema_diff([1], [1, 2]) == "<root>"


def test_type_difference_is_a_diff():
    assert schema_diff({"line": 1}, {"line": True}) == "line"
