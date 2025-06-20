import json
from pathlib import Path

import pytest  # type: ignore

# Import via module to avoid static analysis issues when the package is in a local path.
from src.dbt_column_lineage_extractor.extractor import DbtColumnLineageExtractor


@pytest.fixture()
def temp_manifest_catalog(tmp_path: Path):
    """Create temporary manifest and catalog files with a struct column."""
    # Build minimal manifest
    manifest = {
        "nodes": {
            "model.test.struct_model": {
                "resource_type": "model",
                "path": "models/struct_model.sql",
                "compiled_code": (
                    "SELECT address.city AS city, id FROM `my_project.my_dataset.source_table`"
                ),
                "depends_on": {"nodes": ["source.test.source_table"]},
            }
        },
        "sources": {
            "source.test.source_table": {
                "database": "my_project",
                "schema": "my_dataset",
                "name": "source_table",
                "resource_type": "source",
            }
        },
        "parent_map": {"model.test.struct_model": ["source.test.source_table"]},
        "child_map": {"source.test.source_table": ["model.test.struct_model"]},
    }

    catalog = {
        "nodes": {
            "model.test.struct_model": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "struct_model",
                },
                "columns": {
                    "CITY": {"type": "STRING", "index": 1, "name": "CITY"},
                    "ID": {"type": "INT64", "index": 2, "name": "ID"},
                },
            }
        },
        "sources": {
            "source.test.source_table": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "source_table",
                },
                "columns": {
                    "ADDRESS": {
                        "type": "STRUCT<city STRING, state STRING>",
                        "index": 1,
                        "name": "ADDRESS",
                    },
                    "ID": {"type": "INT64", "index": 2, "name": "ID"},
                },
            }
        },
    }

    manifest_path = tmp_path / "manifest.json"
    catalog_path = tmp_path / "catalog.json"

    manifest_path.write_text(json.dumps(manifest))
    catalog_path.write_text(json.dumps(catalog))

    return str(manifest_path), str(catalog_path)


def test_struct_column_lineage(temp_manifest_catalog):
    manifest_path, catalog_path = temp_manifest_catalog

    extractor = DbtColumnLineageExtractor(
        manifest_path=manifest_path,
        catalog_path=catalog_path,
        selected_models=["model.test.struct_model"],
        dialect="bigquery",
    )

    lineage_map = extractor.build_lineage_map()
    lineage_to_parents = extractor.get_columns_lineage_from_sqlglot_lineage_map(
        lineage_map
    )

    city_parents = lineage_to_parents["model.test.struct_model"]["city"]

    # We expect the parent to be the struct field address.city on the source table
    assert {
        "column": "address.city",
        "dbt_node": "source.test.source_table",
    } in city_parents


@pytest.fixture()
def temp_nested_manifest_catalog(tmp_path: Path):
    """Create temporary manifest and catalog files with nested struct columns."""
    # Build manifest with nested struct access
    manifest = {
        "nodes": {
            "model.test.user_model": {
                "resource_type": "model",
                "path": "models/user_model.sql",
                "compiled_code": (
                    "SELECT "
                    "profile.personal.first_name AS first_name, "
                    "profile.personal.last_name AS last_name, "
                    "profile.contact.email AS email, "
                    "address.city AS city, "
                    "id "
                    "FROM `my_project.my_dataset.users`"
                ),
                "depends_on": {"nodes": ["source.test.users"]},
            }
        },
        "sources": {
            "source.test.users": {
                "database": "my_project",
                "schema": "my_dataset",
                "name": "users",
                "resource_type": "source",
            }
        },
        "parent_map": {"model.test.user_model": ["source.test.users"]},
        "child_map": {"source.test.users": ["model.test.user_model"]},
    }

    catalog = {
        "nodes": {
            "model.test.user_model": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "user_model",
                },
                "columns": {
                    "FIRST_NAME": {"type": "STRING", "index": 1, "name": "FIRST_NAME"},
                    "LAST_NAME": {"type": "STRING", "index": 2, "name": "LAST_NAME"},
                    "EMAIL": {"type": "STRING", "index": 3, "name": "EMAIL"},
                    "CITY": {"type": "STRING", "index": 4, "name": "CITY"},
                    "ID": {"type": "INT64", "index": 5, "name": "ID"},
                },
            }
        },
        "sources": {
            "source.test.users": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "users",
                },
                "columns": {
                    "PROFILE": {
                        "type": (
                            "STRUCT<personal STRUCT<first_name STRING, last_name STRING>, "
                            "contact STRUCT<email STRING, phone STRING>>"
                        ),
                        "index": 1,
                        "name": "PROFILE",
                    },
                    "ADDRESS": {
                        "type": "STRUCT<city STRING, state STRING, zip STRING>",
                        "index": 2,
                        "name": "ADDRESS",
                    },
                    "ID": {"type": "INT64", "index": 3, "name": "ID"},
                },
            }
        },
    }

    manifest_path = tmp_path / "manifest.json"
    catalog_path = tmp_path / "catalog.json"

    manifest_path.write_text(json.dumps(manifest))
    catalog_path.write_text(json.dumps(catalog))

    return str(manifest_path), str(catalog_path)


def test_nested_struct_column_lineage(temp_nested_manifest_catalog):
    """Test lineage tracking for nested struct fields (e.g., profile.personal.first_name)."""
    manifest_path, catalog_path = temp_nested_manifest_catalog

    extractor = DbtColumnLineageExtractor(
        manifest_path=manifest_path,
        catalog_path=catalog_path,
        selected_models=["model.test.user_model"],
        dialect="bigquery",
    )

    lineage_map = extractor.build_lineage_map()
    lineage_to_parents = extractor.get_columns_lineage_from_sqlglot_lineage_map(
        lineage_map
    )

    model_lineage = lineage_to_parents["model.test.user_model"]

    # Test nested struct field access: profile.personal.first_name
    first_name_parents = model_lineage["first_name"]
    assert {
        "column": "profile.personal.first_name",
        "dbt_node": "source.test.users",
    } in first_name_parents

    # Test nested struct field access: profile.personal.last_name
    last_name_parents = model_lineage["last_name"]
    assert {
        "column": "profile.personal.last_name",
        "dbt_node": "source.test.users",
    } in last_name_parents

    # Test nested struct field access: profile.contact.email
    email_parents = model_lineage["email"]
    assert {
        "column": "profile.contact.email",
        "dbt_node": "source.test.users",
    } in email_parents

    # Test regular struct field access: address.city
    city_parents = model_lineage["city"]
    assert {
        "column": "address.city",
        "dbt_node": "source.test.users",
    } in city_parents

    # Test regular column access: id
    id_parents = model_lineage["id"]
    assert {
        "column": "id",
        "dbt_node": "source.test.users",
    } in id_parents
