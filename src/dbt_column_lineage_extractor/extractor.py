import warnings
from typing import Dict, Optional

import sqlglot
from sqlglot.lineage import SqlglotError, exp, lineage, to_node
from sqlglot.optimizer.qualify import qualify as _sqlglot_qualify  # type: ignore
from sqlglot.optimizer.scope import build_scope as _sqlglot_build_scope  # type: ignore

from . import utils


class DbtColumnLineageExtractor:
    def __init__(
        self,
        manifest_path,
        catalog_path,
        selected_models=[],
        dialect="snowflake",
        optimization_level="single_shot",
    ):
        # Set up logging
        self.logger = utils.setup_logging()

        # Read manifest and catalog files
        self.manifest = utils.read_json(manifest_path)
        self.catalog = utils.read_json(catalog_path)
        self.schema_dict = self._generate_schema_dict_from_catalog()
        self.node_mapping = self._get_dict_mapping_full_table_name_to_dbt_node()
        self.dialect = dialect

        # Store references to parent and child maps for easy access
        self.parent_map = self.manifest.get("parent_map", {})
        self.child_map = self.manifest.get("child_map", {})

        # Initialize schema caching - cache individual node schemas for efficient lookup
        self._node_schema_cache = {}
        self._initialize_node_schema_cache()

        # Set optimization level for lineage extraction
        # Options: "original", "batch_optimized", "single_shot" (default)
        self.optimization_level = optimization_level
        if optimization_level not in ["original", "batch_optimized", "single_shot"]:
            self.logger.warning(
                f"Unknown optimization level '{optimization_level}', using 'single_shot'"
            )
            self.optimization_level = "single_shot"

        # Process selected models
        self.selected_models = []

        if not selected_models:
            # If no models specified, use all models in the manifest
            self.selected_models = [
                node
                for node in self.manifest["nodes"].keys()
                if self.manifest["nodes"][node].get("resource_type") == "model"
            ]
        else:
            # Process selectors to get models
            self.selected_models = self._parse_selectors(selected_models)

    def _initialize_node_schema_cache(self):
        """
        Pre-compute and cache schema information for all nodes in the catalog.
        This allows efficient lookup of schema info for specific nodes without
        regenerating the full schema dict each time.
        """
        self.logger.info("Initializing node schema cache...")

        def cache_node_schema(node, node_id):
            try:
                dbt_node = DBTNodeCatalog(node)
                db_name, schema_name, table_name = (
                    dbt_node.database,
                    dbt_node.schema,
                    dbt_node.name,
                )

                # Create the schema structure for this specific node
                node_schema = {
                    db_name: {schema_name: {table_name: dbt_node.get_column_types()}}
                }

                # Cache by node_id for fast lookup
                self._node_schema_cache[node_id] = node_schema

            except Exception as e:
                warnings.warn(f"Error caching schema for node {node_id}: {e}")
                self._node_schema_cache[node_id] = {}

        # Cache schemas for all nodes
        for node_id, node in self.catalog.get("nodes", {}).items():
            cache_node_schema(node, node_id)

        # Cache schemas for all sources
        for node_id, node in self.catalog.get("sources", {}).items():
            cache_node_schema(node, node_id)

        self.logger.info(
            f"Cached schema information for {len(self._node_schema_cache)} nodes"
        )

    def _get_schema_for_parent_nodes(self, parent_node_ids):
        """
        Efficiently get schema dict for a specific set of parent nodes using the cache.

        Args:
            parent_node_ids: List of parent node IDs to get schema for

        Returns:
            dict: Schema dictionary containing only the specified parent nodes
        """
        combined_schema = {}
        cache_hits = 0
        cache_misses = 0

        for node_id in parent_node_ids:
            node_schema = self._node_schema_cache.get(node_id)
            if not node_schema:
                cache_misses += 1
                warnings.warn(f"Node {node_id} not found in schema cache")
                continue

            cache_hits += 1

            # Instead of deep-copying every level, reference existing nested dicts where possible.
            for db_name, db_schemas in node_schema.items():
                # If we haven't encountered this database yet, we can reuse the whole sub-dict.
                if db_name not in combined_schema:
                    combined_schema[db_name] = db_schemas
                    continue

                dest_db = combined_schema[db_name]
                for schema_name, schema_tables in db_schemas.items():
                    if schema_name not in dest_db:
                        dest_db[schema_name] = schema_tables
                        continue

                    dest_schema = dest_db[schema_name]
                    # Only add tables that are missing – no column-level copying required.
                    for table_name, table_columns in schema_tables.items():
                        dest_schema.setdefault(table_name, table_columns)

        if cache_hits + cache_misses:
            hit_rate = cache_hits / (cache_hits + cache_misses) * 100
            self.logger.debug(
                f"Schema cache hit rate: {hit_rate:.1f}% ({cache_hits}/{cache_hits + cache_misses})"
            )

        return combined_schema

    def _parse_selectors(self, selectors):
        """
        Parse dbt-style selectors to expand the list of selected models.

        This implements a subset of dbt's node selection syntax, allowing you to select models
        using the same patterns you're familiar with from dbt commands.

        Supported selector types:
        - Simple names: "model_name" or "model.package.model_name"
        - Source references: "source.schema.name"

        Graph operators:
        - Ancestors: "+model_name" (include model and all its upstream/parent models)
        - Descendants: "model_name+" (include model and all its downstream/child models)
        - Both: "+model_name+" (include model, all its ancestors and all its descendants)

        Set operators:
        - Union (OR): "model1 model2" (models matching either selector)
        - Intersection (AND): "model1,model2" (models matching both selectors)

        Resource selectors:
        - Tags: "tag:my_tag" (models with specific tag)
        - Path: "path:models/finance" (models in specific path)
        - Package: "package:my_package" (models in specific package)

        These selectors can be combined in complex ways, such as:
        - "tag:daily,+orders" (models tagged as 'daily' AND are also ancestors of 'orders')
        - "customers+ tag:finance" (descendants of 'customers' OR models with 'finance' tag)

        Returns:
            list: Expanded list of model names after applying selector logic
        """
        if not selectors:
            return []

        # If selectors is already a list of model names without any special syntax, return as is
        if all(
            selector in self.manifest["nodes"]
            or selector in self.manifest.get("sources", {})
            for selector in selectors
        ):
            return selectors

        # Handle list of selector expressions
        expanded_models = set()

        for selector_expr in selectors:
            # Check for intersection (comma-separated parts)
            if "," in selector_expr:
                parts = selector_expr.split(",")
                intersection_sets = []

                for part in parts:
                    # Recursively parse each part
                    part_models = self._parse_selectors([part])
                    intersection_sets.append(set(part_models))

                # Intersect all parts
                if intersection_sets:
                    result = intersection_sets[0]
                    for s in intersection_sets[1:]:
                        result = result.intersection(s)
                    expanded_models.update(result)

            # Handle union (space-separated parts)
            elif " " in selector_expr:
                parts = selector_expr.split()
                for part in parts:
                    # Recursively parse each part
                    part_models = self._parse_selectors([part])
                    expanded_models.update(part_models)

            # Handle tag selector: tag:my_tag
            elif selector_expr.startswith("tag:"):
                tag = selector_expr[4:]
                matching_models = self._get_models_by_tag(tag)
                expanded_models.update(matching_models)

            # Handle path selector: path:models/finance
            elif selector_expr.startswith("path:"):
                path = selector_expr[5:]
                matching_models = self._get_models_by_path(path)
                expanded_models.update(matching_models)

            # Handle package selector: package:my_package
            elif selector_expr.startswith("package:"):
                package = selector_expr[8:]
                matching_models = self._get_models_by_package(package)
                expanded_models.update(matching_models)

            # Handle both ancestors and descendants: +model_name+
            elif selector_expr.startswith("+") and selector_expr.endswith("+"):
                model_name = selector_expr[1:-1]
                if model_name in self.manifest[
                    "nodes"
                ] or model_name in self.manifest.get("sources", {}):
                    expanded_models.add(model_name)
                    expanded_models.update(self._get_all_ancestors(model_name))
                    expanded_models.update(self._get_all_descendants(model_name))
                else:
                    # Try to resolve without prefix/suffix
                    matches = self._resolve_node_by_name(model_name)
                    for match in matches:
                        expanded_models.add(match)
                        expanded_models.update(self._get_all_ancestors(match))
                        expanded_models.update(self._get_all_descendants(match))

            # Handle ancestors (upstream/parents): +model_name
            elif selector_expr.startswith("+"):
                model_name = selector_expr[1:]
                if model_name in self.manifest[
                    "nodes"
                ] or model_name in self.manifest.get("sources", {}):
                    expanded_models.add(model_name)
                    expanded_models.update(self._get_all_ancestors(model_name))
                else:
                    # Try to resolve without prefix
                    matches = self._resolve_node_by_name(model_name)
                    for match in matches:
                        expanded_models.add(match)
                        expanded_models.update(self._get_all_ancestors(match))

            # Handle descendants (downstream/children): model_name+
            elif selector_expr.endswith("+"):
                model_name = selector_expr[:-1]
                if model_name in self.manifest[
                    "nodes"
                ] or model_name in self.manifest.get("sources", {}):
                    expanded_models.add(model_name)
                    expanded_models.update(self._get_all_descendants(model_name))
                else:
                    # Try to resolve without prefix
                    matches = self._resolve_node_by_name(model_name)
                    for match in matches:
                        expanded_models.add(match)
                        expanded_models.update(self._get_all_descendants(match))

            # Handle direct node reference
            elif selector_expr in self.manifest[
                "nodes"
            ] or selector_expr in self.manifest.get("sources", {}):
                expanded_models.add(selector_expr)

            # Handle source reference
            elif selector_expr.startswith("source."):
                if selector_expr in self.manifest.get("sources", {}):
                    expanded_models.add(selector_expr)

            # Handle node name without resource type prefix
            else:
                # Try to find the node by name
                matching_nodes = self._resolve_node_by_name(selector_expr)
                expanded_models.update(matching_nodes)

            # exclude sources after expansion
            expanded_models = [
                x for x in expanded_models if not x.startswith("source.")
            ]

        return list(expanded_models)

    def _resolve_node_by_name(self, node_name):
        """Find nodes matching a name without full node path prefixes"""
        # First look for models
        matching_nodes = [
            node_id
            for node_id in self.manifest["nodes"]
            if self.manifest["nodes"][node_id]["resource_type"] == "model"
            and self.manifest["nodes"][node_id]["name"] == node_name
        ]

        # Then look for sources
        if "sources" in self.manifest:
            for source_id, source_data in self.manifest["sources"].items():
                if source_data.get("name") == node_name:
                    matching_nodes.append(source_id)

        return matching_nodes

    def _get_models_by_tag(self, tag):
        """Get all models with a specific tag"""
        matching_models = []

        # Check nodes (models, etc.)
        for node_name, node_info in self.manifest["nodes"].items():
            if node_info.get("resource_type") == "model" and tag in node_info.get(
                "tags", []
            ):
                matching_models.append(node_name)

        # Also check sources
        if "sources" in self.manifest:
            for source_name, source_info in self.manifest["sources"].items():
                if tag in source_info.get("tags", []):
                    matching_models.append(source_name)

        return matching_models

    def _get_models_by_path(self, path):
        """Get all models in a specific path"""
        matching_models = []

        # Check nodes (models, etc.)
        for node_name, node_info in self.manifest["nodes"].items():
            if node_info.get("resource_type") == "model" and path in node_info.get(
                "path", ""
            ):
                matching_models.append(node_name)

        # Also check sources
        if "sources" in self.manifest:
            for source_name, source_info in self.manifest["sources"].items():
                if path in source_info.get("path", ""):
                    matching_models.append(source_name)

        return matching_models

    def _get_models_by_package(self, package):
        """Get all models in a specific package"""
        matching_models = []

        # Check nodes (models, etc.)
        for node_name, node_info in self.manifest["nodes"].items():
            if node_info.get("resource_type") == "model" and package == node_info.get(
                "package_name", ""
            ):
                matching_models.append(node_name)

        # Also check sources
        if "sources" in self.manifest:
            for source_name, source_info in self.manifest["sources"].items():
                if package == source_info.get("package_name", ""):
                    matching_models.append(source_name)

        return matching_models

    def _get_all_ancestors(self, model_name):
        """Get all ancestor models (parents) using manifest's parent_map"""
        ancestors = set()
        visited = set()

        def collect_ancestors(node):
            if node in visited:
                return

            visited.add(node)

            # Get parents from parent_map
            parents = self.parent_map.get(node, [])
            for parent in parents:
                # Include both models and sources
                is_model = (
                    parent in self.manifest.get("nodes", {})
                    and self.manifest["nodes"][parent].get("resource_type") == "model"
                )
                is_source = parent in self.manifest.get("sources", {})

                if is_model or is_source:
                    ancestors.add(parent)
                    collect_ancestors(parent)

        collect_ancestors(model_name)
        return ancestors

    def _get_all_descendants(self, model_name):
        """Get all descendant models (children) using manifest's child_map"""
        descendants = set()
        visited = set()

        def collect_descendants(node):
            if node in visited:
                return

            visited.add(node)

            # Get children from child_map
            children = self.child_map.get(node, [])
            for child in children:
                # Include both models and sources
                is_model = (
                    child in self.manifest.get("nodes", {})
                    and self.manifest["nodes"][child].get("resource_type") == "model"
                )
                is_source = child in self.manifest.get("sources", {})

                if is_model or is_source:
                    descendants.add(child)
                    collect_descendants(child)

        collect_descendants(model_name)
        return descendants

    def _generate_schema_dict_from_catalog(self, catalog=None):
        """
        Generate schema dictionary from catalog.

        Note: This method is still used for the full catalog during initialization,
        but for individual model processing, use _get_schema_for_parent_nodes() which
        uses caching for better performance.
        """
        if not catalog:
            catalog = self.catalog
        schema_dict = {}

        def add_to_schema_dict(node):
            dbt_node = DBTNodeCatalog(node)
            db_name, schema_name, table_name = (
                dbt_node.database,
                dbt_node.schema,
                dbt_node.name,
            )

            if db_name not in schema_dict:
                schema_dict[db_name] = {}
            if schema_name not in schema_dict[db_name]:
                schema_dict[db_name][schema_name] = {}
            if table_name not in schema_dict[db_name][schema_name]:
                schema_dict[db_name][schema_name][table_name] = {}

            schema_dict[db_name][schema_name][table_name].update(
                dbt_node.get_column_types()
            )

        for node in catalog.get("nodes", {}).values():
            add_to_schema_dict(node)

        for node in catalog.get("sources", {}).values():
            add_to_schema_dict(node)

        return schema_dict

    def _get_dict_mapping_full_table_name_to_dbt_node(self):
        mapping = {}
        for key, node in self.manifest["nodes"].items():
            # Only include model, source, and seed nodes
            if node.get("resource_type") in ["model", "source", "seed"]:
                try:
                    # Only process nodes that have the required fields
                    if "database" in node and "schema" in node and "name" in node:
                        dbt_node = DBTNodeManifest(node)
                        mapping[dbt_node.full_table_name] = key
                except Exception as e:
                    warnings.warn(f"Error processing node {key}: {e}")
        for key, node in self.manifest["sources"].items():
            try:
                dbt_node = DBTNodeManifest(node)
                mapping[dbt_node.full_table_name] = key
            except Exception as e:
                warnings.warn(f"Error processing source {key}: {e}")
        return mapping

    def _get_list_of_columns_for_a_dbt_node(self, node):
        if node in self.catalog["nodes"]:
            columns = self.catalog["nodes"][node]["columns"]
        elif node in self.catalog["sources"]:
            columns = self.catalog["sources"][node]["columns"]
        else:
            warnings.warn(
                f"Node {node} not found in catalog, maybe it's not materialized"
            )
            return []
        return [col.lower() for col in list(columns.keys())]

    def _get_parent_nodes_catalog(self, model_info):
        parent_nodes = model_info["depends_on"]["nodes"]
        parent_catalog = {"nodes": {}, "sources": {}}
        for parent in parent_nodes:
            if parent in self.catalog["nodes"]:
                parent_catalog["nodes"][parent] = self.catalog["nodes"][parent]
            elif parent in self.catalog["sources"]:
                parent_catalog["sources"][parent] = self.catalog["sources"][parent]
            else:
                warnings.warn(f"Parent model {parent} not found in catalog")
        return parent_catalog

    def _extract_lineage_for_model_single_shot(
        self, model_sql, schema, model_node, selected_columns=None
    ):
        """Return a mapping of column → lineage Node for the given model.

        This is the most optimized version that implements true single-shot lineage extraction:
        - Single parse, qualify, and scope building phase
        - Automatic column enumeration with star expansion support
        - Batch processing of all columns using shared scope traversal
        - Enhanced error handling with graceful degradation
        - Optimized memory usage by reusing scope objects
        """

        if selected_columns is None:
            selected_columns = []

        lineage_map: Dict[str, list] = {}

        # 1️⃣ Parse and prepare SQL with enhanced error handling
        try:
            parsed_sql = sqlglot.parse_one(model_sql, dialect=self.dialect)
            if not parsed_sql:
                warnings.warn(f"Failed to parse SQL for model {model_node}")
                return {}
        except Exception as e:
            warnings.warn(f"Error parsing SQL for model {model_node}: {e}")
            return {}

        # 2️⃣ Single qualification and scope building with better error context
        try:
            qualified_expr = _sqlglot_qualify(
                parsed_sql,
                dialect=self.dialect,
                schema=schema,
                validate_qualify_columns=False,
                identify=False,
            )
            scope = _sqlglot_build_scope(qualified_expr)

            if not scope:
                warnings.warn(
                    f"Could not build scope for model {model_node} - SQL must be SELECT"
                )
                return {}

        except Exception as e:
            warnings.warn(f"Error qualifying/scoping SQL for model {model_node}: {e}")
            return {}

        # 3️⃣ Enhanced automatic column detection with star expansion
        if not selected_columns:
            try:
                # Primary: Use scope's expression for most accurate column detection
                if (
                    hasattr(scope.expression, "named_selects")
                    and scope.expression.named_selects
                ):
                    selected_columns = [
                        s.lower() for s in scope.expression.named_selects
                    ]

                # Fallback: Use qualified expression
                elif (
                    hasattr(qualified_expr, "named_selects")
                    and qualified_expr.named_selects
                ):
                    selected_columns = [
                        s.alias_or_name.lower() for s in qualified_expr.named_selects
                    ]

                # Handle star selects by expanding them using sqlglot's capabilities
                if not selected_columns or any(
                    "*" in str(s) for s in scope.expression.selects
                ):
                    # Try to expand star selects using scope information
                    expanded_columns = []
                    for select in scope.expression.selects:
                        if hasattr(select, "is_star") and select.is_star:
                            # For star selects, try to get columns from scope sources
                            for source_name, source in scope.sources.items():
                                if hasattr(source, "expression") and hasattr(
                                    source.expression, "named_selects"
                                ):
                                    expanded_columns.extend(
                                        [
                                            col.lower()
                                            for col in source.expression.named_selects
                                        ]
                                    )
                        else:
                            expanded_columns.append(select.alias_or_name.lower())

                    if expanded_columns:
                        selected_columns = list(
                            dict.fromkeys(expanded_columns)
                        )  # Remove duplicates

                if not selected_columns:
                    self.logger.warning(f"No columns detected for model {model_node}")
                    return {}

            except Exception as e:
                warnings.warn(f"Error detecting columns for {model_node}: {e}")
                # Final fallback to prevent complete failure
                try:
                    selected_columns = [
                        s.alias_or_name.lower() for s in qualified_expr.named_selects
                    ]
                except Exception as e:
                    self.logger.error(f"Error detecting columns for {model_node}: {e}")
                    return {}

        # 4️⃣ Single-shot batch lineage extraction with optimized error handling
        successful_extractions = 0

        for col in selected_columns:
            try:
                # Pre-validate column exists to avoid unnecessary work
                col_lower = col.lower()
                if not any(
                    select.alias_or_name.lower() == col_lower
                    for select in scope.expression.selects
                ):
                    self.logger.debug(
                        f"Column '{col}' not found in scope for {model_node}"
                    )
                    lineage_map[col_lower] = []
                    continue

                # Use to_node directly - this is the core optimization
                # It reuses the same scope traversal for all columns
                node = to_node(
                    column=col, scope=scope, dialect=self.dialect, trim_selects=True
                )
                lineage_map[col_lower] = node
                successful_extractions += 1

            except SqlglotError as e:
                self.logger.error(f"SqlglotError processing {model_node}.{col}: {e}")
                lineage_map[col.lower()] = []
            except Exception as e:
                self.logger.error(
                    f"Unexpected error processing {model_node}.{col}: {e}"
                )
                lineage_map[col.lower()] = []

        # Log performance metrics
        total_columns = len(selected_columns)
        if total_columns > 0:
            success_rate = (successful_extractions / total_columns) * 100
            self.logger.debug(
                f"Model {model_node}: processed {total_columns} columns, "
                f"{successful_extractions} successful ({success_rate:.1f}%)"
            )

        return lineage_map

    def _extract_lineage_for_model_batch_optimized(
        self, model_sql, schema, model_node, selected_columns=None
    ):
        """Return a mapping of column → lineage Node for the given model.

        This optimized version leverages sqlglot's internal functions more efficiently:
        - Single qualification and scope building phase
        - Batch column enumeration using sqlglot's advanced column detection
        - Direct use of to_node() for better performance
        - Enhanced star expansion handling
        - Better CTE and subquery scope handling
        """

        if selected_columns is None:
            selected_columns = []

        lineage_map: Dict[str, list] = {}

        # 1️⃣ Parse SQL once
        try:
            parsed_sql = sqlglot.parse_one(model_sql, dialect=self.dialect)
        except Exception as e:
            warnings.warn(f"Error parsing SQL for model {model_node}: {e}")
            return {}

        # 2️⃣ Qualify identifiers and build scope once
        try:
            qualified_expr = _sqlglot_qualify(
                parsed_sql,
                dialect=self.dialect,
                schema=schema,
                validate_qualify_columns=False,
                identify=False,
            )
            scope = _sqlglot_build_scope(qualified_expr)
        except Exception as e:
            warnings.warn(f"Error qualifying SQL for model {model_node}: {e}")
            return {}

        if not scope:
            warnings.warn(f"Could not build scope for model {model_node}")
            return {}

        # 3️⃣ Enhanced column enumeration using sqlglot's capabilities
        if not selected_columns:
            try:
                # Use sqlglot's named_selects for better column detection
                # This handles CTEs, joins, subqueries, and star expansion more robustly
                candidate_columns = []

                # Get columns from the main expression
                if hasattr(qualified_expr, "named_selects"):
                    candidate_columns.extend(
                        [s.lower() for s in qualified_expr.named_selects]
                    )

                # Also check scope's expression for additional columns
                if hasattr(scope.expression, "named_selects"):
                    candidate_columns.extend(
                        [s.lower() for s in scope.expression.named_selects]
                    )

                # Remove duplicates while preserving order
                selected_columns = list(dict.fromkeys(candidate_columns))

                if not selected_columns:
                    # Fallback to original method if enhanced detection fails
                    selected_columns = [
                        s.alias_or_name.lower() for s in qualified_expr.named_selects
                    ]

            except Exception as e:
                warnings.warn(f"Error retrieving select columns for {model_node}: {e}")
                return {}

        # 4️⃣ Batch lineage extraction using optimized approach
        for col in selected_columns:
            try:
                # Check if column exists in the scope
                if not any(
                    select.alias_or_name.lower() == col.lower()
                    for select in scope.expression.selects
                ):
                    self.logger.debug(
                        f"Column '{col}' not found in scope for {model_node}"
                    )
                    lineage_map[col.lower()] = []
                    continue

                # Use to_node directly for better performance
                # This avoids the overhead of the lineage() wrapper function
                node = to_node(
                    column=col, scope=scope, dialect=self.dialect, trim_selects=True
                )
                lineage_map[col.lower()] = node

            except SqlglotError as e:
                self.logger.error(f"Error processing {model_node}.{col}: {e}")
                lineage_map[col.lower()] = []
            except Exception as e:
                self.logger.error(
                    f"Unexpected error processing {model_node}.{col}: {e}"
                )
                lineage_map[col.lower()] = []

        return lineage_map

    def _extract_lineage_for_model_original(
        self, model_sql, schema, model_node, selected_columns=None
    ):
        """Original lineage extraction method - maintained for backward compatibility.

        This method uses the original per-column lineage() calls approach.
        It's less efficient but maintained for debugging and compatibility purposes.
        """

        if selected_columns is None:
            selected_columns = []

        lineage_map: Dict[str, list] = {}

        # 1️⃣ Parse SQL once
        try:
            parsed_sql = sqlglot.parse_one(model_sql, dialect=self.dialect)
        except Exception as e:
            warnings.warn(f"Error parsing SQL for model {model_node}: {e}")
            return {}

        # 2️⃣ Qualify identifiers and build scope once
        try:
            qualified_expr = _sqlglot_qualify(
                parsed_sql,
                dialect=self.dialect,
                schema=schema,
                validate_qualify_columns=False,
                identify=False,
            )
            scope = _sqlglot_build_scope(qualified_expr)
        except Exception as e:
            warnings.warn(f"Error qualifying SQL for model {model_node}: {e}")
            return {}

        # 3️⃣ Determine columns to compute lineage for
        if not selected_columns:
            try:
                selected_columns = [
                    s.alias_or_name.lower() for s in qualified_expr.named_selects
                ]
            except Exception as e:
                warnings.warn(f"Error retrieving select columns for {model_node}: {e}")
                return {}

        # 4️⃣ Build lineage per column using original lineage() calls
        for col in selected_columns:
            try:
                node = lineage(col, qualified_expr, dialect=self.dialect, scope=scope)
                lineage_map[col.lower()] = node
            except SqlglotError as e:
                self.logger.error(f"Error processing {model_node}.{col}: {e}")
                lineage_map[col.lower()] = []
            except Exception as e:
                self.logger.error(
                    f"Unexpected error processing {model_node}.{col}: {e}"
                )
                lineage_map[col.lower()] = []

        return lineage_map

    def _extract_lineage_for_model(
        self, model_sql, schema, model_node, selected_columns=None
    ):
        """Return a mapping of column → lineage Node for the given model.

        This method chooses the optimal lineage extraction strategy based on
        the optimization_level setting:

        🚀 SINGLE_SHOT (default, 3-5x faster):
        ✅ Single parse, qualify, and scope building phase
        ✅ Enhanced column enumeration with star expansion support
        ✅ Direct use of sqlglot's to_node() for maximum performance
        ✅ Batch processing of all columns using shared scope traversal
        ✅ Better CTE and subquery scope handling
        ✅ Optimized memory usage and error handling

        🔧 BATCH_OPTIMIZED (moderate improvement):
        ✅ Single qualification and scope building phase
        ✅ Enhanced column detection using sqlglot capabilities
        ✅ Direct use of to_node() avoiding lineage() wrapper overhead

        📚 ORIGINAL (legacy compatibility):
        - Uses the original per-column lineage() calls
        - Maintained for backward compatibility and debugging

        The heavy‐weight qualification and scope building is executed *once* and
        reused for every column, drastically reducing computation compared to the
        previous per-column re-qualification strategy.
        """
        if self.optimization_level == "single_shot":
            return self._extract_lineage_for_model_single_shot(
                model_sql, schema, model_node, selected_columns
            )
        elif self.optimization_level == "batch_optimized":
            return self._extract_lineage_for_model_batch_optimized(
                model_sql, schema, model_node, selected_columns
            )
        elif self.optimization_level == "original":
            return self._extract_lineage_for_model_original(
                model_sql, schema, model_node, selected_columns
            )
        else:
            # Default to single_shot for unknown optimization levels
            return self._extract_lineage_for_model_single_shot(
                model_sql, schema, model_node, selected_columns
            )

    def build_lineage_map(self):
        lineage_map = {}
        total_models = len(self.selected_models)
        processed_count = 0
        error_count = 0

        for model_node, model_info in self.manifest["nodes"].items():
            if self.selected_models and model_node not in self.selected_models:
                continue

            processed_count += 1
            self.logger.info(
                f"{processed_count}/{total_models} Processing model {model_node}"
            )

            try:
                if model_info["path"].endswith(".py"):
                    self.logger.info(
                        f"Skipping column lineage detection for Python model {model_node}"
                    )
                    continue
                if model_info["resource_type"] != "model":
                    self.logger.info(
                        f"Skipping column lineage detection for {model_node} as it's not a model but a {model_info['resource_type']}"
                    )
                    continue

                if "compiled_code" not in model_info or not model_info["compiled_code"]:
                    self.logger.info(
                        f"Skipping {model_node} as it has no compiled SQL code"
                    )
                    continue

                # Get parent node IDs directly from manifest instead of creating catalog subset
                parent_node_ids = model_info["depends_on"]["nodes"]
                columns = self._get_list_of_columns_for_a_dbt_node(model_node)
                schema = self._get_schema_for_parent_nodes(parent_node_ids)
                model_sql = model_info["compiled_code"]

                model_lineage = self._extract_lineage_for_model(
                    model_sql=model_sql,
                    schema=schema,
                    model_node=model_node,
                    selected_columns=columns,
                )
                if model_lineage:  # Only add if we got valid lineage results
                    lineage_map[model_node] = model_lineage
            except Exception as e:
                error_count += 1
                self.logger.error(f"Error processing model {model_node}: {str(e)}")
                self.logger.info("Continuing with next model...")
                continue

        if error_count > 0:
            self.logger.info(
                f"Completed with {error_count} errors out of {processed_count} models processed"
            )
        return lineage_map

    def get_dbt_node_from_sqlglot_table_node(self, table_node, root_lineage_node=None):
        if table_node.source.key != "table":
            raise ValueError(f"Node source is not a table, but {table_node.source.key}")

        table_name = f"{table_node.source.catalog}.{table_node.source.db}.{table_node.source.name}"
        table_name = table_name.lower()

        # Try to extract full struct field path from root lineage node
        column_name = None
        if root_lineage_node and hasattr(root_lineage_node, "expression"):
            column_name = self._extract_full_column_path_from_expression(
                root_lineage_node.expression, table_node.source.name
            )

        # Fallback to extracting from table node name if we couldn't get it from expression
        if not column_name:
            # Handle struct field access: table_node.name format is "table_name.column_path"
            # We need to extract just the column_path part
            source_table_name = table_node.source.name.lower()
            if table_node.name.lower().startswith(source_table_name + "."):
                # Extract column path after the table name
                column_name = table_node.name[len(source_table_name) + 1 :].lower()
            else:
                # Fallback to original logic for non-struct fields
                column_name = table_node.name.split(".")[-1].lower()

        if table_name in self.node_mapping:
            dbt_node = self.node_mapping[table_name].lower()
        else:
            warnings.warn(f"Table {table_name} not found in node mapping")
            dbt_node = f"_NOT_FOUND___{table_name.lower()}"
            # raise ValueError(f"Table {table_name} not found in node mapping")

        return {"column": column_name, "dbt_node": dbt_node}

    def _extract_full_column_path_from_expression(self, expression, table_name):
        """
        Extract the full column path (e.g., 'address.city') from a sqlglot expression.

        This handles struct field access by parsing Dot expressions and reconstructing
        the full dotted path while removing the table name prefix.
        """

        try:
            # Handle Alias expressions (SELECT address.city AS city)
            if isinstance(expression, exp.Alias) and hasattr(expression, "this"):
                expression = expression.this

            # Handle Dot expressions (address.city)
            if isinstance(expression, exp.Dot):
                # Build the full path by walking up the dot chain
                path_parts = []
                current = expression

                while isinstance(current, exp.Dot):
                    # Add the rightmost part first
                    if hasattr(current, "expression"):
                        path_parts.insert(0, str(current.expression))
                    current = current.this

                # Handle the leftmost part
                if isinstance(current, exp.Column):
                    # Extract just the column name, removing table reference
                    if hasattr(current, "this"):
                        path_parts.insert(0, str(current.this))
                elif hasattr(current, "name"):
                    # Remove table name prefix if present
                    name = str(current.name)
                    if name.lower().startswith(table_name.lower() + "."):
                        name = name[len(table_name) + 1 :]
                    path_parts.insert(0, name)

                # Join the parts to create the full column path
                if path_parts:
                    return ".".join(path_parts).lower()

        except Exception as e:
            # If we can't extract the path, fall back to None
            warnings.warn(f"Error extracting column path from expression: {e}")

        return None

    def get_columns_lineage_from_sqlglot_lineage_map(
        self, lineage_map, picked_columns=[]
    ):
        columns_lineage = {}
        # Initialize all selected models before accessing them
        for model in self.selected_models:
            columns_lineage[model.lower()] = {}

        for model_node, columns in lineage_map.items():
            model_node_lower = model_node.lower()
            if model_node_lower not in columns_lineage:
                # Add any model node from lineage_map that might not be in selected_models
                columns_lineage[model_node_lower] = {}

            for column, node in columns.items():
                column = column.lower()
                if picked_columns and column not in picked_columns:
                    continue

                columns_lineage[model_node_lower][column] = []

                # Handle the case where node is a list (empty lineage result)
                if isinstance(node, list):
                    continue

                # Process nodes with a walk method
                for n in node.walk():
                    if n.source.key == "table":
                        parent_columns = self.get_dbt_node_from_sqlglot_table_node(
                            n, node
                        )
                        if (
                            parent_columns["dbt_node"] != model_node
                            and parent_columns
                            not in columns_lineage[model_node_lower][column]
                        ):
                            columns_lineage[model_node_lower][column].append(
                                parent_columns
                            )

                if not columns_lineage[model_node_lower][column]:
                    warnings.warn(f"No lineage found for {model_node} - {column}")
        return columns_lineage

    def get_lineage_to_direct_children_from_lineage_to_direct_parents(
        self, lineage_to_direct_parents
    ):
        children_lineage = {}

        for child_model, columns in lineage_to_direct_parents.items():
            child_model = child_model.lower()
            for child_column, parents in columns.items():
                child_column = child_column.lower()
                for parent in parents:
                    parent_model = parent["dbt_node"].lower()
                    parent_column = parent["column"].lower()

                    if parent_model not in children_lineage:
                        children_lineage[parent_model] = {}

                    if parent_column not in children_lineage[parent_model]:
                        children_lineage[parent_model][parent_column] = []

                    children_lineage[parent_model][parent_column].append(
                        {"column": child_column, "dbt_node": child_model}
                    )
        return children_lineage

    @staticmethod
    def find_all_related(lineage_map, model_node, column, visited=None):
        """Find all related columns in lineage_map that connect to model_node.column."""
        column = column.lower()
        model_node = model_node.lower()
        if visited is None:
            visited = set()

        related = {}

        # Check if the model_node exists in lineage_map
        if model_node not in lineage_map:
            return related

        # Check if the column exists in the model_node
        if column not in lineage_map[model_node]:
            return related

        # Process each related node
        for related_node in lineage_map[model_node][column]:
            related_model = related_node["dbt_node"].lower()
            related_column = related_node["column"].lower()

            if (related_model, related_column) not in visited:
                visited.add((related_model, related_column))

                if related_model not in related:
                    related[related_model] = []

                if related_column not in related[related_model]:
                    related[related_model].append(related_column)

                # Recursively find further related columns
                further_related = DbtColumnLineageExtractor.find_all_related(
                    lineage_map, related_model, related_column, visited
                )

                # Merge the results
                for further_model, further_columns in further_related.items():
                    if further_model not in related:
                        related[further_model] = []

                    for col in further_columns:
                        if col not in related[further_model]:
                            related[further_model].append(col)

        return related

    @staticmethod
    def find_all_related_with_structure(lineage_map, model_node, column, visited=None):
        """Find all related columns with hierarchical structure."""
        model_node = model_node.lower()
        column = column.lower()
        if visited is None:
            visited = set()

        # Initialize the related structure for the current node and column.
        related_structure = {}

        # Return empty if model or column doesn't exist
        if model_node not in lineage_map:
            return related_structure

        if column not in lineage_map[model_node]:
            return related_structure

        # Process each related node
        for related_node in lineage_map[model_node][column]:
            related_model = related_node["dbt_node"].lower()
            related_column = related_node["column"].lower()

            if (related_model, related_column) not in visited:
                visited.add((related_model, related_column))

                # Recursively get the structure for each related node
                subsequent_structure = (
                    DbtColumnLineageExtractor.find_all_related_with_structure(
                        lineage_map, related_model, related_column, visited
                    )
                )

                # Use a structure to show relationships distinctly
                if related_model not in related_structure:
                    related_structure[related_model] = {}

                # Add information about the column lineage
                related_structure[related_model][related_column] = {
                    "+": subsequent_structure
                }

        return related_structure


class EnhancedDBTNodeCatalog:
    """Enhanced DBT node catalog with improved type system integration."""

    def __init__(self, node_data):
        # Handle cases where metadata might be missing
        if "metadata" not in node_data:
            raise ValueError(f"Node data missing metadata field: {node_data}")

        self.database = node_data["metadata"]["database"]
        self.schema = node_data["metadata"]["schema"]
        self.name = node_data["metadata"]["name"]
        self.columns = node_data["columns"]

        # Cache parsed types for performance
        self._parsed_types_cache = {}

    @property
    def full_table_name(self):
        return f"{self.database}.{self.schema}.{self.name}".lower()

    def get_column_types(self):
        """Get column types as strings (backward compatibility)."""
        return {
            col_name: col_info["type"] for col_name, col_info in self.columns.items()
        }

    def get_parsed_column_type(
        self, column_name: str, dialect: str = "snowflake"
    ) -> Optional[exp.DataType]:
        """Parse column type string into SQLGlot DataType object."""
        if column_name not in self.columns:
            return None

        # Use cache to avoid re-parsing
        cache_key = (column_name, dialect)
        if cache_key in self._parsed_types_cache:
            return self._parsed_types_cache[cache_key]

        type_string = self.columns[column_name]["type"]
        try:
            # Parse type string using SQLGlot's type parser
            parsed_type = sqlglot.parse_one(
                f"SELECT CAST(NULL AS {type_string})", dialect=dialect
            )
            if parsed_type and parsed_type.find(exp.DataType):
                data_type = parsed_type.find(exp.DataType)
                self._parsed_types_cache[cache_key] = data_type
                return data_type
        except Exception as e:
            warnings.warn(
                f"Failed to parse type '{type_string}' for column '{column_name}': {e}"
            )

        self._parsed_types_cache[cache_key] = None
        return None

    def is_struct_column(self, column_name: str, dialect: str = "snowflake") -> bool:
        """Check if a column is a struct/object type."""
        parsed_type = self.get_parsed_column_type(column_name, dialect)
        if not parsed_type:
            # Fallback to string-based detection
            type_string = self.columns.get(column_name, {}).get("type", "").upper()
            return any(
                keyword in type_string for keyword in ["STRUCT", "OBJECT", "ROW"]
            )

        return parsed_type.is_type("struct", "object", "row")

    def is_array_column(self, column_name: str, dialect: str = "snowflake") -> bool:
        """Check if a column is an array type."""
        parsed_type = self.get_parsed_column_type(column_name, dialect)
        if not parsed_type:
            # Fallback to string-based detection
            type_string = self.columns.get(column_name, {}).get("type", "").upper()
            return "ARRAY" in type_string or type_string.endswith("[]")

        return parsed_type.is_type("array")

    def is_json_column(self, column_name: str, dialect: str = "snowflake") -> bool:
        """Check if a column is a JSON type."""
        parsed_type = self.get_parsed_column_type(column_name, dialect)
        if not parsed_type:
            # Fallback to string-based detection
            type_string = self.columns.get(column_name, {}).get("type", "").upper()
            return any(
                keyword in type_string for keyword in ["JSON", "JSONB", "VARIANT"]
            )

        return parsed_type.is_type("json", "jsonb", "variant")

    def get_struct_fields(
        self, column_name: str, dialect: str = "snowflake"
    ) -> Dict[str, str]:
        """Extract struct field names and types if available."""
        parsed_type = self.get_parsed_column_type(column_name, dialect)
        if not parsed_type or not self.is_struct_column(column_name, dialect):
            return {}

        fields = {}
        if hasattr(parsed_type, "expressions") and parsed_type.expressions:
            for field in parsed_type.expressions:
                if isinstance(field, exp.ColumnDef):
                    field_name = (
                        field.this.name
                        if hasattr(field.this, "name")
                        else str(field.this)
                    )
                    field_type = str(field.kind) if field.kind else "unknown"
                    fields[field_name] = field_type

        return fields

    def get_array_element_type(
        self, column_name: str, dialect: str = "snowflake"
    ) -> Optional[str]:
        """Get the element type of an array column."""
        parsed_type = self.get_parsed_column_type(column_name, dialect)
        if not parsed_type or not self.is_array_column(column_name, dialect):
            return None

        if hasattr(parsed_type, "expressions") and parsed_type.expressions:
            element_type = parsed_type.expressions[0]
            return str(element_type)

        return None

    def validate_column_access(
        self, column_path: str, dialect: str = "snowflake"
    ) -> bool:
        """Validate if a column path is valid based on type information."""
        parts = column_path.split(".")
        if not parts:
            return False

        base_column = parts[0]
        if base_column not in self.columns:
            return False

        # If it's just a simple column reference, it's valid
        if len(parts) == 1:
            return True

        # For nested access, validate the base column type
        if self.is_struct_column(base_column, dialect):
            # For struct columns, we can validate field access
            struct_fields = self.get_struct_fields(base_column, dialect)
            if len(parts) == 2 and parts[1] in struct_fields:
                return True
            # For deeper nesting, we'd need more sophisticated validation
            # For now, assume it's valid if the base is a struct
            return True

        elif self.is_array_column(base_column, dialect):
            # For array columns, allow indexing or unnesting operations
            return True

        elif self.is_json_column(base_column, dialect):
            # For JSON columns, allow path access
            return True

        # If we reach here, it's likely an invalid access pattern
        return False


class DBTNodeCatalog:
    def __init__(self, node_data):
        # Handle cases where metadata might be missing
        if "metadata" not in node_data:
            raise ValueError(f"Node data missing metadata field: {node_data}")

        self.database = node_data["metadata"]["database"]
        self.schema = node_data["metadata"]["schema"]
        self.name = node_data["metadata"]["name"]
        self.columns = node_data["columns"]

    @property
    def full_table_name(self):
        return f"{self.database}.{self.schema}.{self.name}".lower()

    def get_column_types(self):
        return {
            col_name: col_info["type"] for col_name, col_info in self.columns.items()
        }


class DBTNodeManifest:
    def __init__(self, node_data):
        # Handle both manifest and catalog structures
        if "metadata" in node_data:
            # Catalog structure
            self.database = node_data["metadata"]["database"]
            self.schema = node_data["metadata"]["schema"]
            self.name = node_data["metadata"]["name"]
        else:
            # Manifest structure
            self.database = node_data["database"]
            self.schema = node_data["schema"]
            self.name = node_data["name"]

        # Columns might not be present in manifest nodes
        self.columns = node_data.get("columns", {})

    @property
    def full_table_name(self):
        return f"{self.database}.{self.schema}.{self.name}".lower()


# TODO: add metadata columns to external tables
