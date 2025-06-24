import json
import os
from unittest.mock import patch

import pytest

from src.dbt_column_lineage_extractor.cli_root_sources import (
    extract_root_sources_for_models,
    main,
)


@pytest.fixture
def test_data_dir():
    return os.path.join("tests", "test_data")


@pytest.fixture
def test_output_dir(tmp_path):
    """Create a temporary directory for test outputs"""
    return tmp_path


@pytest.fixture
def sample_lineage_data():
    """Sample lineage data for testing root source extraction"""
    return {
        "model.test.target_model.column1": [
            {"model": "model.test.intermediate_model", "column": "intermediate_col1"}
        ],
        "model.test.target_model.column2": [
            {"model": "model.test.base__source_model", "column": "source_col2"}
        ],
        "model.test.intermediate_model.intermediate_col1": [
            {"model": "model.test.base__source_model", "column": "source_col1"}
        ],
        "model.test.intermediate_model.intermediate_col2": [
            {"model": "model.test.another_base__model", "column": "another_col"}
        ],
    }


@pytest.fixture
def sample_manifest_data():
    """Sample manifest data for testing"""
    return {
        "nodes": {
            "model.test.target_model": {"resource_type": "model"},
            "model.test.intermediate_model": {"resource_type": "model"},
            "model.test.base__source_model": {"resource_type": "model"},
            "model.test.another_base__model": {"resource_type": "model"},
            "model.test.skipped_model": {"resource_type": "model"},
        }
    }


class TestExtractRootSourcesForModels:
    """Test the extract_root_sources_for_models function"""

    def test_basic_root_source_extraction(self, sample_lineage_data):
        """Test basic root source extraction functionality"""
        target_models = ["model.test.target_model"]

        root_sources, affected_columns, skipped_models = (
            extract_root_sources_for_models(sample_lineage_data, target_models)
        )

        # Should find one root source that contributes to target_model
        # (model.test.another_base__model doesn't contribute to target_model)
        assert len(root_sources) == 1
        assert "model.test.base__source_model" in root_sources

        # Check columns from root sources
        assert "source_col1" in root_sources["model.test.base__source_model"]
        assert "source_col2" in root_sources["model.test.base__source_model"]

        # Check affected columns in target model
        assert len(affected_columns) == 1
        assert "model.test.target_model" in affected_columns
        assert "column1" in affected_columns["model.test.target_model"]
        assert "column2" in affected_columns["model.test.target_model"]

        # No skipped models without manifest
        assert len(skipped_models) == 0

    def test_multiple_target_models(self, sample_lineage_data):
        """Test with multiple target models"""
        target_models = ["model.test.target_model", "model.test.intermediate_model"]

        root_sources, affected_columns, skipped_models = (
            extract_root_sources_for_models(sample_lineage_data, target_models)
        )

        # Should still find the same root sources
        assert len(root_sources) == 2
        assert "model.test.base__source_model" in root_sources
        assert "model.test.another_base__model" in root_sources

        # Should have affected columns from both target models
        assert len(affected_columns) == 2
        assert "model.test.target_model" in affected_columns
        assert "model.test.intermediate_model" in affected_columns

    def test_with_manifest_data(self, sample_lineage_data, sample_manifest_data):
        """Test root source extraction with manifest data to identify skipped models"""
        target_models = ["model.test.target_model", "model.test.skipped_model"]

        root_sources, affected_columns, skipped_models = (
            extract_root_sources_for_models(
                sample_lineage_data, target_models, sample_manifest_data
            )
        )

        # Should identify the skipped model
        assert "model.test.skipped_model" in skipped_models
        assert len(skipped_models) == 1

    def test_empty_lineage_data(self):
        """Test with empty lineage data"""
        target_models = ["model.test.nonexistent"]

        root_sources, affected_columns, skipped_models = (
            extract_root_sources_for_models({}, target_models)
        )

        assert len(root_sources) == 0
        assert len(affected_columns) == 0
        assert len(skipped_models) == 0

    def test_no_target_models(self, sample_lineage_data):
        """Test with no target models specified"""
        target_models = []

        root_sources, affected_columns, skipped_models = (
            extract_root_sources_for_models(sample_lineage_data, target_models)
        )

        assert len(root_sources) == 0
        assert len(affected_columns) == 0
        assert len(skipped_models) == 0

    def test_circular_references(self):
        """Test handling of circular references in lineage"""
        circular_lineage = {
            "model.test.model_a.col1": [
                {"model": "model.test.model_b", "column": "col1"}
            ],
            "model.test.model_b.col1": [
                {"model": "model.test.model_a", "column": "col1"}
            ],
        }
        target_models = ["model.test.model_a"]

        # Should not crash with circular references
        root_sources, affected_columns, skipped_models = (
            extract_root_sources_for_models(circular_lineage, target_models)
        )

        # In this case, both models appear in lineage, so no root sources should be found
        assert len(root_sources) == 0


class TestCliRootSources:
    """Test the CLI functionality"""

    def test_cli_with_existing_lineage_file(
        self, test_data_dir, test_output_dir, tmp_path
    ):
        """Test CLI with existing lineage file"""
        # Create a test lineage file
        lineage_file = tmp_path / "test_lineage.json"
        lineage_data = {
            "model.jaffle_shop.customers": {
                "customer_id": [
                    {"model": "source.jaffle_shop.raw_customers", "column": "id"}
                ]
            }
        }
        with open(lineage_file, "w") as f:
            json.dump(lineage_data, f)

        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.jaffle_shop.customers"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = str(lineage_file)
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "both"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 0

            # Check if output file was created
            assert os.path.exists(os.path.join(test_output_dir, "root_sources.json"))

    def test_cli_generate_lineage_data(self, test_data_dir, test_output_dir):
        """Test CLI that generates lineage data from manifest/catalog"""
        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.jaffle_shop.customers"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "both"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 0

            # Check if output file was created
            assert os.path.exists(os.path.join(test_output_dir, "root_sources.json"))

    def test_cli_with_model_list_json(self, test_data_dir, test_output_dir):
        """Test CLI with model list from JSON file"""
        model_list_path = os.path.join(test_output_dir, "model_list.json")
        models = ["model.jaffle_shop.customers"]

        # Create model list JSON file
        with open(model_list_path, "w") as f:
            json.dump(models, f)

        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = []
            mock_args.return_value.model_list_json = model_list_path
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "json"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 0

            # Verify the output contains the specified models
            with open(os.path.join(test_output_dir, "root_sources.json")) as f:
                output_data = json.load(f)
                assert "target_models" in output_data
                assert output_data["target_models"]  # Should have processed models

    def test_cli_with_invalid_model_list_json(self, test_data_dir, test_output_dir):
        """Test CLI with invalid model list JSON"""
        invalid_model_list = os.path.join(test_output_dir, "invalid_models.json")
        with open(invalid_model_list, "w") as f:
            json.dump({"not_a_list": "this should fail"}, f)

        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = []
            mock_args.return_value.model_list_json = invalid_model_list
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "summary"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 1  # Should return error code

    def test_cli_missing_lineage_file(self, test_data_dir, test_output_dir):
        """Test CLI with missing lineage file"""
        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.jaffle_shop.customers"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = "/nonexistent/file.json"
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "summary"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 1  # Should return error code

    def test_cli_no_models_specified(self, test_data_dir, test_output_dir):
        """Test CLI with no models specified (should process all models)"""
        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = []
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = (
                "both"  # Changed to "both" to create JSON file
            )
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 0

            # Should have processed some models
            with open(os.path.join(test_output_dir, "root_sources.json")) as f:
                output_data = json.load(f)
                assert len(output_data["target_models"]) > 0

    def test_cli_summary_format_only(self, test_data_dir, test_output_dir):
        """Test CLI with summary format only"""
        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.jaffle_shop.customers"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "summary"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 0

    def test_cli_json_format_only(self, test_data_dir, test_output_dir):
        """Test CLI with JSON format only"""
        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.jaffle_shop.customers"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "json"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 0

            # Verify JSON output structure
            with open(os.path.join(test_output_dir, "root_sources.json")) as f:
                output_data = json.load(f)
                assert "target_models" in output_data
                assert "root_sources" in output_data
                assert "affected_columns" in output_data
                assert "skipped_models" in output_data
                assert "summary" in output_data

    def test_cli_continue_on_error(self, test_data_dir, test_output_dir):
        """Test CLI with continue_on_error flag"""
        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = "/nonexistent/manifest.json"
            mock_args.return_value.catalog = "/nonexistent/catalog.json"
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.test.model"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "summary"
            mock_args.return_value.continue_on_error = True

            result = main()
            assert result == 1  # Should still return error but handle gracefully

    def test_cli_with_no_root_sources_found(
        self, test_data_dir, test_output_dir, tmp_path
    ):
        """Test CLI when no root sources are found"""
        # Create a lineage file with no root sources (all models have parents)
        lineage_file = tmp_path / "no_roots_lineage.json"
        lineage_data = {
            "model.test.model_a": {
                "col1": [{"model": "model.test.model_b", "column": "col1"}]
            },
            "model.test.model_b": {
                "col1": [{"model": "model.test.model_a", "column": "col1"}]
            },
        }
        with open(lineage_file, "w") as f:
            json.dump(lineage_data, f)

        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.test.model_a"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = str(lineage_file)
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "summary"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 0  # Should succeed but with no root sources

    @patch(
        "src.dbt_column_lineage_extractor.cli_root_sources.DbtColumnLineageExtractor"
    )
    def test_cli_extractor_exception_handling(
        self, mock_extractor_class, test_data_dir, test_output_dir
    ):
        """Test CLI handling of exceptions from DbtColumnLineageExtractor"""
        # Make the extractor raise an exception
        mock_extractor_class.side_effect = Exception("Test exception")

        with patch("argparse.ArgumentParser.parse_args") as mock_args:
            mock_args.return_value.manifest = os.path.join(
                test_data_dir, "inputs", "manifest.json"
            )
            mock_args.return_value.catalog = os.path.join(
                test_data_dir, "inputs", "catalog.json"
            )
            mock_args.return_value.dialect = "snowflake"
            mock_args.return_value.model = ["model.jaffle_shop.customers"]
            mock_args.return_value.model_list_json = None
            mock_args.return_value.lineage_parents_file = None
            mock_args.return_value.output_dir = str(test_output_dir)
            mock_args.return_value.output_format = "summary"
            mock_args.return_value.continue_on_error = False

            result = main()
            assert result == 1  # Should return error code
