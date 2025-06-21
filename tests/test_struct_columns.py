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


@pytest.fixture()
def temp_deeply_nested_manifest_catalog(tmp_path: Path):
    """Create temporary manifest and catalog files with deeply nested struct columns (4+ levels)."""
    # Build manifest with deeply nested struct access
    manifest = {
        "nodes": {
            "model.test.deep_model": {
                "resource_type": "model",
                "path": "models/deep_model.sql",
                "compiled_code": (
                    "SELECT "
                    "company.departments.engineering.teams.backend.lead AS backend_lead, "
                    "company.departments.marketing.budget.q1.allocated AS q1_budget, "
                    "company.metadata.created.timestamp.utc AS created_utc, "
                    "id "
                    "FROM `my_project.my_dataset.organizations`"
                ),
                "depends_on": {"nodes": ["source.test.organizations"]},
            }
        },
        "sources": {
            "source.test.organizations": {
                "database": "my_project",
                "schema": "my_dataset",
                "name": "organizations",
                "resource_type": "source",
            }
        },
        "parent_map": {"model.test.deep_model": ["source.test.organizations"]},
        "child_map": {"source.test.organizations": ["model.test.deep_model"]},
    }

    catalog = {
        "nodes": {
            "model.test.deep_model": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "deep_model",
                },
                "columns": {
                    "BACKEND_LEAD": {
                        "type": "STRING",
                        "index": 1,
                        "name": "BACKEND_LEAD",
                    },
                    "Q1_BUDGET": {"type": "FLOAT64", "index": 2, "name": "Q1_BUDGET"},
                    "CREATED_UTC": {
                        "type": "TIMESTAMP",
                        "index": 3,
                        "name": "CREATED_UTC",
                    },
                    "ID": {"type": "INT64", "index": 4, "name": "ID"},
                },
            }
        },
        "sources": {
            "source.test.organizations": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "organizations",
                },
                "columns": {
                    "COMPANY": {
                        "type": (
                            "STRUCT<"
                            "departments STRUCT<"
                            "engineering STRUCT<"
                            "teams STRUCT<"
                            "backend STRUCT<lead STRING, size INT64>, "
                            "frontend STRUCT<lead STRING, size INT64>"
                            ">"
                            ">, "
                            "marketing STRUCT<"
                            "budget STRUCT<"
                            "q1 STRUCT<allocated FLOAT64, spent FLOAT64>, "
                            "q2 STRUCT<allocated FLOAT64, spent FLOAT64>"
                            ">"
                            ">"
                            ">, "
                            "metadata STRUCT<"
                            "created STRUCT<"
                            "timestamp STRUCT<utc TIMESTAMP, local TIMESTAMP>"
                            ">"
                            ">"
                            ">"
                        ),
                        "index": 1,
                        "name": "COMPANY",
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


def test_deeply_nested_struct_column_lineage(temp_deeply_nested_manifest_catalog):
    """Test lineage tracking for deeply nested struct fields (4+ levels deep)."""
    manifest_path, catalog_path = temp_deeply_nested_manifest_catalog

    extractor = DbtColumnLineageExtractor(
        manifest_path=manifest_path,
        catalog_path=catalog_path,
        selected_models=["model.test.deep_model"],
        dialect="bigquery",
    )

    lineage_map = extractor.build_lineage_map()
    lineage_to_parents = extractor.get_columns_lineage_from_sqlglot_lineage_map(
        lineage_map
    )

    model_lineage = lineage_to_parents["model.test.deep_model"]

    # Test 5-level deep nested struct: company.departments.engineering.teams.backend.lead
    backend_lead_parents = model_lineage["backend_lead"]
    assert {
        "column": "company.departments.engineering.teams.backend.lead",
        "dbt_node": "source.test.organizations",
    } in backend_lead_parents

    # Test 5-level deep nested struct: company.departments.marketing.budget.q1.allocated
    q1_budget_parents = model_lineage["q1_budget"]
    assert {
        "column": "company.departments.marketing.budget.q1.allocated",
        "dbt_node": "source.test.organizations",
    } in q1_budget_parents

    # Test 4-level deep nested struct: company.metadata.created.timestamp.utc
    created_utc_parents = model_lineage["created_utc"]
    assert {
        "column": "company.metadata.created.timestamp.utc",
        "dbt_node": "source.test.organizations",
    } in created_utc_parents

    # Test regular column access: id
    id_parents = model_lineage["id"]
    assert {
        "column": "id",
        "dbt_node": "source.test.organizations",
    } in id_parents


@pytest.fixture()
def temp_complex_struct_manifest_catalog(tmp_path: Path):
    """Create manifest and catalog files with complex struct expressions and joins."""
    # Build manifest with complex struct creation from joined tables
    manifest = {
        "nodes": {
            "model.test.complex_struct_model": {
                "resource_type": "model",
                "path": "models/complex_struct_model.sql",
                "compiled_code": (
                    "SELECT "
                    "a.id, "
                    "STRUCT(a.x + b.y AS from_both, a.x AS from_left, b.y AS from_right) AS mystruct "
                    "FROM `my_project.my_dataset.table_a` AS a "
                    "LEFT JOIN `my_project.my_other_dataset.table_b` AS b USING (id)"
                ),
                "depends_on": {"nodes": ["source.test.table_a", "source.test.table_b"]},
            }
        },
        "sources": {
            "source.test.table_a": {
                "database": "my_project",
                "schema": "my_dataset",
                "name": "table_a",
                "resource_type": "source",
            },
            "source.test.table_b": {
                "database": "my_project",
                "schema": "my_other_dataset",
                "name": "table_b",
                "resource_type": "source",
            },
        },
        "parent_map": {
            "model.test.complex_struct_model": [
                "source.test.table_a",
                "source.test.table_b",
            ]
        },
        "child_map": {
            "source.test.table_a": ["model.test.complex_struct_model"],
            "source.test.table_b": ["model.test.complex_struct_model"],
        },
    }

    catalog = {
        "nodes": {
            "model.test.complex_struct_model": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "complex_struct_model",
                },
                "columns": {
                    "ID": {"type": "INT64", "index": 1, "name": "ID"},
                    "MYSTRUCT": {
                        "type": (
                            "STRUCT<from_both INT64, from_left INT64, from_right INT64>"
                        ),
                        "index": 2,
                        "name": "MYSTRUCT",
                    },
                },
            }
        },
        "sources": {
            "source.test.table_a": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_dataset",
                    "name": "table_a",
                },
                "columns": {
                    "ID": {"type": "INT64", "index": 1, "name": "ID"},
                    "X": {"type": "INT64", "index": 2, "name": "X"},
                },
            },
            "source.test.table_b": {
                "metadata": {
                    "database": "my_project",
                    "schema": "my_other_dataset",
                    "name": "table_b",
                },
                "columns": {
                    "ID": {"type": "INT64", "index": 1, "name": "ID"},
                    "Y": {"type": "INT64", "index": 2, "name": "Y"},
                },
            },
        },
    }

    manifest_path = tmp_path / "manifest.json"
    catalog_path = tmp_path / "catalog.json"

    manifest_path.write_text(json.dumps(manifest))
    catalog_path.write_text(json.dumps(catalog))

    return str(manifest_path), str(catalog_path)


def test_complex_struct_expression_lineage(temp_complex_struct_manifest_catalog):
    """Test lineage tracking for structs with complex expressions and joins."""
    manifest_path, catalog_path = temp_complex_struct_manifest_catalog

    extractor = DbtColumnLineageExtractor(
        manifest_path=manifest_path,
        catalog_path=catalog_path,
        selected_models=["model.test.complex_struct_model"],
        dialect="bigquery",
    )

    lineage_map = extractor.build_lineage_map()
    lineage_to_parents = extractor.get_columns_lineage_from_sqlglot_lineage_map(
        lineage_map
    )

    model_lineage = lineage_to_parents["model.test.complex_struct_model"]

    # Test regular column: id should come from table_a (and potentially table_b due to join)
    id_parents = model_lineage["id"]
    assert {
        "column": "id",
        "dbt_node": "source.test.table_a",
    } in id_parents

    # Test the struct column itself: mystruct should have lineage to both source columns
    # This tests that complex expressions within struct constructors are properly tracked
    mystruct_parents = model_lineage["mystruct"]

    # The struct should have lineage to both x from table_a and y from table_b
    # since it contains expressions that reference both columns
    assert {
        "column": "x",
        "dbt_node": "source.test.table_a",
    } in mystruct_parents
    assert {
        "column": "y",
        "dbt_node": "source.test.table_b",
    } in mystruct_parents

    # Verify we have exactly the expected lineage (no extra dependencies)
    expected_parents = {
        ("x", "source.test.table_a"),
        ("y", "source.test.table_b"),
    }
    actual_parents = {
        (parent["column"], parent["dbt_node"]) for parent in mystruct_parents
    }
    assert actual_parents == expected_parents
