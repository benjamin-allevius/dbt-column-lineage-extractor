import argparse
import os
from collections import defaultdict

from . import utils
from .extractor import DbtColumnLineageExtractor


def extract_root_sources_for_models(
    lineage_to_direct_parents, target_models, manifest_data=None, catalog_data=None
):
    """
    Extract root source models and their contributing columns for specific target models.

    Args:
        lineage_to_direct_parents: Dictionary containing lineage to direct parents data
        target_models: List of target model node IDs to find root sources for
        manifest_data: Optional manifest data to identify skipped models
        catalog_data: Optional catalog data to get actual columns for target models

    Returns:
        tuple: (root_sources dict, affected_columns dict, skipped_models list)
    """
    # Find all models that appear as sources (in parent lists) vs targets (keys)
    all_source_models = set()
    all_target_models = set()

    # Extract model names from lineage keys (remove column part)
    for model_col_key in lineage_to_direct_parents.keys():
        parts = model_col_key.split(".")
        model_name = ".".join(parts[:-1])
        all_target_models.add(model_name)

    # Collect all models that appear as parents
    for model_col, parents in lineage_to_direct_parents.items():
        for parent_info in parents:
            # Handle both formats: {"model": "..."} and {"dbt_node": "..."}
            parent_model = parent_info.get("model") or parent_info.get("dbt_node")
            if parent_model:
                all_source_models.add(parent_model)

    # Root sources are models that appear as sources but never as targets
    root_source_models = all_source_models - all_target_models

    # Group columns by root source model, but only for specified target models
    root_sources = defaultdict(set)
    affected_columns = defaultdict(set)

    # Helper function to get actual columns for a model from catalog
    def get_actual_columns_for_model(model_node):
        """Get the actual columns that exist in a model according to the catalog."""
        if not catalog_data:
            return None

        # Check nodes first, then sources
        if model_node in catalog_data.get("nodes", {}):
            return [
                col.lower()
                for col in catalog_data["nodes"][model_node]["columns"].keys()
            ]
        elif model_node in catalog_data.get("sources", {}):
            return [
                col.lower()
                for col in catalog_data["sources"][model_node]["columns"].keys()
            ]
        else:
            return None

    # Find which columns from root sources contribute to the specified target models
    # We need to trace recursively through the lineage to find all paths
    def find_contributing_sources(current_model, current_column, visited=None):
        """Recursively find all root sources that contribute to a model.column"""
        if visited is None:
            visited = set()

        model_col_key = f"{current_model}.{current_column}"
        if model_col_key in visited:
            return
        visited.add(model_col_key)

        # Track that this column in the target model is affected
        if current_model in target_models:
            affected_columns[current_model].add(current_column)

        # Get direct parents for this model.column combination
        parents = lineage_to_direct_parents.get(model_col_key, [])

        for parent_info in parents:
            # Handle both formats: {"model": "..."} and {"dbt_node": "..."}
            parent_model = parent_info.get("model") or parent_info.get("dbt_node")
            parent_column = parent_info["column"]

            if parent_model and parent_model in root_source_models:
                # This is a root source - record it
                root_sources[parent_model].add(parent_column)
            elif parent_model:
                # Continue tracing upstream
                find_contributing_sources(parent_model, parent_column, visited)

    # Start tracing from actual columns in the target models (if catalog available)
    # or fall back to columns in lineage data
    for target_model in target_models:
        actual_columns = get_actual_columns_for_model(target_model)

        if actual_columns:
            # Use actual columns from catalog
            for column_name in actual_columns:
                model_col_key = f"{target_model}.{column_name}"
                if model_col_key in lineage_to_direct_parents:
                    find_contributing_sources(target_model, column_name)
        else:
            # Fall back to columns from lineage data for this target model
            for model_col_key in lineage_to_direct_parents.keys():
                # Extract model name (everything except the last part which is the column)
                parts = model_col_key.split(".")
                model_name = ".".join(parts[:-1])
                column_name = parts[-1]
                if model_name == target_model:
                    find_contributing_sources(model_name, column_name)

    # Convert sets to sorted lists for JSON serialization
    root_sources_result = {}
    for model, columns in root_sources.items():
        root_sources_result[model] = sorted(list(columns))

    affected_columns_result = {}
    for model, columns in affected_columns.items():
        affected_columns_result[model] = sorted(list(columns))

    # Identify potentially skipped models if manifest is provided
    skipped_models = []
    if manifest_data:
        try:
            # Get all model nodes from manifest
            manifest_models = set()
            for node_id, node_info in manifest_data.get("nodes", {}).items():
                if node_info.get("resource_type") == "model":
                    manifest_models.add(node_id)

            # Find models in manifest that don't appear in lineage data
            models_in_lineage = all_source_models | all_target_models
            potentially_skipped = manifest_models - models_in_lineage

            # Filter to only target models that were requested but skipped
            skipped_models = [
                model for model in potentially_skipped if model in target_models
            ]

        except Exception:
            pass  # Skip analysis if manifest processing fails

    return root_sources_result, affected_columns_result, skipped_models


def main():
    parser = argparse.ArgumentParser(
        description="Find root sources that contribute to specified target models"
    )
    parser.add_argument(
        "--manifest",
        default="./inputs/manifest.json",
        help="Path to the manifest.json file, default to ./inputs/manifest.json",
    )
    parser.add_argument(
        "--catalog",
        default="./inputs/catalog.json",
        help="Path to the catalog.json file, default to ./inputs/catalog.json",
    )
    parser.add_argument(
        "--dialect",
        default="snowflake",
        help="SQL dialect to use, default is snowflake, more dialects at "
        "https://github.com/tobymao/sqlglot/tree/v25.24.5/sqlglot/dialects",
    )
    parser.add_argument(
        "--model",
        nargs="*",
        default=[],
        help="""List of target models to find root sources for using dbt-style selectors:
            - Simple model names: model_name
            - Include ancestors: +model_name (include upstream/parent models)
            - Include descendants: model_name+ (include downstream/child models)
            - Union (either): "model1 model2" (models matching either selector)
            - Intersection (both): "model1,model2" (models matching both selectors)
            - Tag filtering: tag:my_tag (models with specific tag)
            - Path filtering: path:models/finance (models in specific path)
            - Package filtering: package:my_package (models in specific package)
            If no models specified, all models will be processed.""",
    )
    parser.add_argument(
        "--model-list-json",
        help="Path to a JSON file containing a list of models to find root sources for. "
        "If specified, this takes precedence over --model",
    )
    parser.add_argument(
        "--lineage-parents-file",
        help="Path to existing lineage_to_direct_parents.json file. "
        "If not provided, lineage will be generated from manifest/catalog",
    )
    parser.add_argument(
        "--output-dir",
        default="./outputs",
        help="Output directory for files, default to ./outputs",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "summary", "both"],
        default="both",
        help="Output format: json file, summary to console, or both. Default is both.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing even if some models fail",
    )

    args = parser.parse_args()

    # Set up logging
    logger = utils.setup_logging()

    try:
        # Create output directory if it doesn't exist
        os.makedirs(args.output_dir, exist_ok=True)

        # Determine target models
        target_models = args.model
        if args.model_list_json:
            try:
                target_models = utils.read_json(args.model_list_json)
                if not isinstance(target_models, list):
                    raise ValueError("The JSON file must contain a list of model names")
            except Exception as e:
                logger.error(f"Error reading model list from JSON file: {e}")
                return 1

        # Create extractor to expand model selectors (needed regardless of lineage source)
        extractor = DbtColumnLineageExtractor(
            manifest_path=args.manifest,
            catalog_path=args.catalog,
            selected_models=target_models,
            dialect=args.dialect,
        )
        target_models = extractor.selected_models
        logger.info(
            f"Processing {len(target_models)} target models after selector expansion"
        )

        # Get lineage data - either from existing file or generate it
        lineage_to_direct_parents = None
        manifest_data = extractor.manifest

        if args.lineage_parents_file:
            # Use existing lineage file
            try:
                lineage_to_direct_parents_raw = utils.read_dict_from_file(
                    args.lineage_parents_file
                )
                # Transform from nested format to flattened format
                lineage_to_direct_parents = {}
                for (
                    model_name,
                    model_columns,
                ) in lineage_to_direct_parents_raw.items():
                    for column_name, parents in model_columns.items():
                        flattened_key = f"{model_name}.{column_name}"
                        lineage_to_direct_parents[flattened_key] = parents

                logger.info(f"Loaded lineage data from {args.lineage_parents_file}")
            except FileNotFoundError:
                logger.error(
                    f"Error: Could not find lineage file: {args.lineage_parents_file}"
                )
                logger.info(
                    "Run dbt_column_lineage_direct first to generate lineage data."
                )
                return 1
        else:
            # Generate lineage data using the same extractor
            logger.info("Generating lineage data from manifest and catalog...")
            try:
                lineage_map = extractor.build_lineage_map()
                if not lineage_map:
                    logger.warning(
                        "Warning: No valid lineage was generated. "
                        "Check for errors above."
                    )
                    if not args.continue_on_error:
                        return 1

                lineage_to_direct_parents_raw = (
                    extractor.get_columns_lineage_from_sqlglot_lineage_map(lineage_map)
                )

                # Transform from nested format to flattened format
                # expected by extract_root_sources_for_models
                lineage_to_direct_parents = {}
                for model_name, model_columns in lineage_to_direct_parents_raw.items():
                    for column_name, parents in model_columns.items():
                        # Create flattened key: "model_name.column_name"
                        flattened_key = f"{model_name}.{column_name}"
                        lineage_to_direct_parents[flattened_key] = parents

                logger.info("Lineage data generated successfully")

            except Exception as e:
                logger.error(f"Error generating lineage data: {str(e)}")
                if not args.continue_on_error:
                    raise
                return 1

        # If no target models specified, use all models from lineage data
        if not target_models:
            # Extract unique model names from lineage keys
            all_models = set()
            for model_col_key in lineage_to_direct_parents.keys():
                # Extract model name (everything except the last part which is the column)
                parts = model_col_key.split(".")
                model_name = ".".join(parts[:-1])
                all_models.add(model_name)
            target_models = list(all_models)
            logger.info(
                f"No target models specified, processing all {len(target_models)} models "
                f"from lineage data"
            )

        # Extract root sources
        root_sources, affected_columns, skipped_models = (
            extract_root_sources_for_models(
                lineage_to_direct_parents,
                target_models,
                manifest_data,
                extractor.catalog,
            )
        )

        if not root_sources:
            logger.warning("No root sources found for the specified target models.")
            return 0

        # Generate outputs based on format
        if args.output_format in ["json", "both"]:
            # Save to JSON file
            output_data = {
                "target_models": target_models,
                "root_sources": root_sources,
                "affected_columns": affected_columns,
                "skipped_models": skipped_models,
                "summary": {
                    "total_target_models": len(target_models),
                    "total_root_sources": len(root_sources),
                    "total_root_columns": sum(
                        len(cols) for cols in root_sources.values()
                    ),
                },
            }

            output_file = os.path.join(args.output_dir, "root_sources.json")
            utils.write_dict_to_file(output_data, output_file)
            logger.info(f"Root sources data saved to {output_file}")

        if args.output_format in ["summary", "both"]:
            # Print summary to console
            logger.info("\n" + "=" * 70)
            logger.info("ROOT SOURCE MODELS AND CONTRIBUTING COLUMNS")
            logger.info("=" * 70)

            total_models = len(root_sources)
            total_columns = sum(len(cols) for cols in root_sources.values())

            logger.info(
                f"\nFound {total_models} root source models with "
                f"{total_columns} contributing columns for {len(target_models)} target models:\n"
            )

            for model, columns in sorted(root_sources.items()):
                logger.info(f"📊 {model}")
                logger.info(f"   Columns ({len(columns)}): {', '.join(columns)}")
                logger.info("")

            logger.info("=" * 70)
            logger.info(
                f"Summary: {total_models} root sources, {total_columns} columns"
            )

            # Show affected columns in target models
            if affected_columns:
                logger.info("\n" + "-" * 70)
                logger.info("TARGET MODELS AND AFFECTED COLUMNS")
                logger.info("-" * 70)
                for model, columns in sorted(affected_columns.items()):
                    logger.info(f"🎯 {model}")
                    logger.info(
                        f"   Affected columns ({len(columns)}): {', '.join(columns)}"
                    )
                    logger.info("")

            # Report skipped models if any
            if skipped_models:
                logger.info("\n" + "!" * 70)
                logger.info("POTENTIALLY SKIPPED TARGET MODELS")
                logger.info("!" * 70)
                logger.info(
                    f"\nFound {len(skipped_models)} target models that may have been skipped:\n"
                )
                for model in sorted(skipped_models):
                    logger.info(f"⚠️  {model}")
                logger.info("\nThese models were in manifest but not in lineage data.")
                logger.info("Check for 'compiled_code' issues in these models.")

        return 0

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
