import warnings
from unittest.mock import patch

from src.dbt_column_lineage_extractor.extractor import EnhancedDBTNodeCatalog


class TestEnhancedTypeSystemIntegration:
    """Test suite for enhanced type system integration features."""

    def test_enhanced_catalog_initialization(self):
        """Test that EnhancedDBTNodeCatalog initializes correctly."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {
                "id": {"type": "INTEGER"},
                "name": {"type": "VARCHAR(100)"},
                "address": {"type": "STRUCT<city STRING, state STRING>"},
                "tags": {"type": "ARRAY<STRING>"},
                "metadata": {"type": "JSON"},
            },
        }

        catalog = EnhancedDBTNodeCatalog(node_data)
        assert catalog.database == "test_db"
        assert catalog.schema == "test_schema"
        assert catalog.name == "test_table"
        assert catalog.full_table_name == "test_db.test_schema.test_table"

    def test_struct_column_detection(self):
        """Test struct column type detection."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {
                "simple_col": {"type": "VARCHAR(100)"},
                "struct_col": {"type": "STRUCT<city STRING, state STRING>"},
                "object_col": {"type": "OBJECT"},
                "row_col": {"type": "ROW(id INT, name STRING)"},
            },
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # Test struct detection
        assert not catalog.is_struct_column("simple_col")
        assert catalog.is_struct_column("struct_col")
        assert catalog.is_struct_column("object_col")
        assert catalog.is_struct_column("row_col")

    def test_array_column_detection(self):
        """Test array column type detection."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {
                "simple_col": {"type": "VARCHAR(100)"},
                "array_col": {"type": "ARRAY<STRING>"},
                "array_col2": {"type": "STRING[]"},
                "array_int": {"type": "ARRAY<INTEGER>"},
            },
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # Test array detection
        assert not catalog.is_array_column("simple_col")
        assert catalog.is_array_column("array_col")
        assert catalog.is_array_column("array_col2")
        assert catalog.is_array_column("array_int")

    def test_json_column_detection(self):
        """Test JSON column type detection."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {
                "simple_col": {"type": "VARCHAR(100)"},
                "json_col": {"type": "JSON"},
                "jsonb_col": {"type": "JSONB"},
                "variant_col": {"type": "VARIANT"},
            },
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # Test JSON detection
        assert not catalog.is_json_column("simple_col")
        assert catalog.is_json_column("json_col")
        assert catalog.is_json_column("jsonb_col")
        assert catalog.is_json_column("variant_col")

    def test_column_access_validation(self):
        """Test column access path validation."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {
                "simple_col": {"type": "VARCHAR(100)"},
                "struct_col": {"type": "STRUCT<city STRING, state STRING>"},
                "array_col": {"type": "ARRAY<STRING>"},
                "json_col": {"type": "JSON"},
            },
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # Test simple column access
        assert catalog.validate_column_access("simple_col")
        assert not catalog.validate_column_access("nonexistent_col")

        # Test struct field access
        assert catalog.validate_column_access("struct_col")
        assert catalog.validate_column_access(
            "struct_col.city"
        )  # Should be valid for struct

        # Test array access
        assert catalog.validate_column_access("array_col")

        # Test JSON path access
        assert catalog.validate_column_access("json_col")
        assert catalog.validate_column_access(
            "json_col.path"
        )  # Should be valid for JSON

    def test_backward_compatibility(self):
        """Test that enhanced catalog maintains backward compatibility."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {"id": {"type": "INTEGER"}, "name": {"type": "VARCHAR(100)"}},
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # Test backward compatibility with get_column_types
        column_types = catalog.get_column_types()
        assert column_types == {"id": "INTEGER", "name": "VARCHAR(100)"}

    def test_type_parsing_fallback(self):
        """Test that type parsing gracefully falls back to string detection."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {
                "struct_col": {"type": "STRUCT<complex_type>"},
                "array_col": {"type": "ARRAY<CUSTOM_TYPE>"},
            },
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # These should still work via string-based fallback detection
        # even if SQLGlot parsing fails
        assert catalog.is_struct_column("struct_col")
        assert catalog.is_array_column("array_col")

    @patch("src.dbt_column_lineage_extractor.extractor.sqlglot.parse_one")
    def test_type_parsing_error_handling(self, mock_parse_one):
        """Test error handling when type parsing fails."""
        # Mock sqlglot to raise an exception
        mock_parse_one.side_effect = Exception("Parse error")

        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {"struct_col": {"type": "STRUCT<city STRING>"}},
        }

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            catalog = EnhancedDBTNodeCatalog(node_data)

            # This should still work via fallback
            result = catalog.is_struct_column("struct_col")
            assert result is True

            # Check that a warning was issued
            assert len(w) > 0
            assert "Failed to parse type" in str(w[0].message)

    def test_type_caching(self):
        """Test that parsed types are cached for performance."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {"test_col": {"type": "VARCHAR(100)"}},
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # First call should populate cache
        result1 = catalog.get_parsed_column_type("test_col")

        # Second call should use cache
        result2 = catalog.get_parsed_column_type("test_col")

        # Results should be the same
        assert result1 == result2

        # Cache should have the entry
        cache_key = ("test_col", "snowflake")
        assert cache_key in catalog._parsed_types_cache

    def test_nonexistent_column_handling(self):
        """Test handling of requests for nonexistent columns."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {"existing_col": {"type": "VARCHAR(100)"}},
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # Test nonexistent column
        assert catalog.get_parsed_column_type("nonexistent") is None
        assert not catalog.is_struct_column("nonexistent")
        assert not catalog.is_array_column("nonexistent")
        assert not catalog.is_json_column("nonexistent")
        assert not catalog.validate_column_access("nonexistent")

    def test_multiple_dialects(self):
        """Test that the enhanced catalog works with different SQL dialects."""
        node_data = {
            "metadata": {
                "database": "test_db",
                "schema": "test_schema",
                "name": "test_table",
            },
            "columns": {"array_col": {"type": "ARRAY<STRING>"}},
        }

        catalog = EnhancedDBTNodeCatalog(node_data)

        # Test with different dialects
        for dialect in ["snowflake", "postgres", "bigquery"]:
            assert catalog.is_array_column("array_col", dialect=dialect)
            assert catalog.validate_column_access("array_col", dialect=dialect)
