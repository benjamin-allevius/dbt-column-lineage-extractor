# Type System Integration Analysis & Improvements

## Overview

This analysis examines the current implementation of type system integration in the dbt-column-lineage-extractor and identifies areas for improvement, particularly around:

1. **Better integration with dbt's type system and column metadata**
2. **Struct/nested columns (partially implemented)**
3. **Array operations**
4. **JSON path operations**

## Current Implementation Assessment

### 1. Column Type Handling

**Current State:**
- The `DBTNodeCatalog` class extracts column types as strings from catalog metadata
- Types are stored in the schema dictionary but not deeply analyzed or used for lineage decisions
- Basic type information is passed to SQLGlot but not leveraged for enhanced lineage tracking

```python
def get_column_types(self):
    return {
        col_name: col_info["type"] for col_name, col_info in self.columns.items()
    }
```

**Limitations:**
- Type information is treated as simple strings
- No type parsing or structural analysis
- Missing integration with SQLGlot's rich DataType system
- No type-aware lineage tracking

### 2. Struct/Nested Column Support (Partially Implemented)

**Current State:**
- Basic struct field access is supported (e.g., `address.city`)
- The `_extract_full_column_path_from_expression` method handles dot notation
- Test coverage exists for nested struct operations up to 5+ levels deep

**Strengths:**
- Handles basic struct field access patterns
- Supports deeply nested structures
- Works with BigQuery-style struct syntax

**Limitations:**
- No type-aware struct field validation
- No support for complex struct operations (casting, transformations)
- Missing support for struct array operations
- No integration with SQLGlot's struct type system

### 3. Array Operations (Not Implemented)

**Current State:**
- No specific handling for array operations
- SQLGlot supports rich array operations (UNNEST, EXPLODE, array indexing)
- dbt catalog contains array type information but it's not utilized

**Missing Capabilities:**
- UNNEST/EXPLODE operations tracking
- Array indexing lineage (e.g., `array_col[0]`)
- Array aggregation functions (ARRAY_AGG, etc.)
- Cross join unnest patterns

### 4. JSON Path Operations (Not Implemented)

**Current State:**
- No specific JSON path operation handling
- SQLGlot has comprehensive JSON path support
- JSON columns in catalog not specially processed

**Missing Capabilities:**
- JSON_EXTRACT operations
- JSON path expressions ($.field.subfield)
- JSON array operations
- Dynamic JSON field access

## Implementation Status

### ✅ Phase 1: Enhanced Type System Integration (COMPLETED)

**Status: IMPLEMENTED**

The enhanced type system integration has been successfully implemented with the following components:

1. **EnhancedDBTNodeCatalog Class**: A new enhanced version of the DBTNodeCatalog that provides:
   - SQLGlot DataType parsing from string types
   - Type-aware validation methods
   - Caching for performance optimization
   - Backward compatibility with existing code

2. **Type Detection Methods**:
   - `is_struct_column()`: Detects STRUCT/OBJECT/ROW types
   - `is_array_column()`: Detects ARRAY types
   - `is_json_column()`: Detects JSON/JSONB/VARIANT types

3. **Type Introspection Methods**:
   - `get_struct_fields()`: Extracts field names and types from struct columns
   - `get_array_element_type()`: Gets element type from array columns
   - `validate_column_access()`: Validates column path access patterns

4. **Comprehensive Test Coverage**: 11 test cases covering all functionality including:
   - Type detection for struct, array, and JSON columns
   - Column access validation
   - Error handling and fallback mechanisms
   - Caching behavior
   - Multi-dialect support

**Key Features**:
- ✅ SQLGlot DataType parsing with fallback to string-based detection
- ✅ Performance caching to avoid re-parsing types
- ✅ Type-aware column path validation
- ✅ Support for multiple SQL dialects
- ✅ Graceful error handling with warnings
- ✅ Full backward compatibility

## Proposed Improvements (Remaining Work)

### 2. Enhanced Array Operations Support (Next Priority)

#### Implementation Strategy:

```python
class EnhancedDBTNodeCatalog(DBTNodeCatalog):
    def __init__(self, node_data, dialect="snowflake"):
        super().__init__(node_data)
        self.dialect = dialect
        self._parsed_types = {}
        self._parse_column_types()

    def _parse_column_types(self):
        """Parse string types into SQLGlot DataType objects"""
        for col_name, type_str in self.columns.items():
            try:
                parsed_type = exp.DataType.build(
                    col_info["type"],
                    dialect=self.dialect
                )
                self._parsed_types[col_name] = parsed_type
            except Exception as e:
                # Fallback to string type
                self._parsed_types[col_name] = type_str

    def get_column_type_parsed(self, column_name):
        """Get parsed DataType object for a column"""
        return self._parsed_types.get(column_name)

    def is_struct_column(self, column_name):
        """Check if a column is a struct type"""
        parsed_type = self.get_column_type_parsed(column_name)
        return (isinstance(parsed_type, exp.DataType) and
                parsed_type.is_type(exp.DataType.Type.STRUCT))

    def is_array_column(self, column_name):
        """Check if a column is an array type"""
        parsed_type = self.get_column_type_parsed(column_name)
        return (isinstance(parsed_type, exp.DataType) and
                parsed_type.is_type(exp.DataType.Type.ARRAY))

    def is_json_column(self, column_name):
        """Check if a column is a JSON type"""
        parsed_type = self.get_column_type_parsed(column_name)
        return (isinstance(parsed_type, exp.DataType) and
                parsed_type.is_type(exp.DataType.Type.JSON))
```

### 2. Improved Struct Column Processing

#### Enhanced Struct Field Validation:

```python
def _validate_struct_field_access(self, struct_column, field_path, table_schema):
    """Validate that a struct field path exists in the schema"""
    if not self.is_struct_column(struct_column):
        return False

    struct_type = self.get_column_type_parsed(struct_column)
    current_type = struct_type

    for field in field_path.split('.'):
        if not current_type.is_type(exp.DataType.Type.STRUCT):
            return False

        # Find field in struct
        field_found = False
        for field_def in current_type.expressions:
            if field_def.this.name.lower() == field.lower():
                current_type = field_def.kind
                field_found = True
                break

        if not field_found:
            return False

    return True

def _get_struct_field_type(self, struct_column, field_path, table_schema):
    """Get the type of a specific struct field"""
    # Implementation to traverse struct type and return field type
    pass
```

#### Struct Array Support:

```python
def _handle_struct_array_access(self, expression, table_name):
    """Handle struct array access patterns like struct_array[0].field"""
    if isinstance(expression, exp.Bracket):
        # Handle array indexing
        array_expr = expression.this
        index_expr = expression.expressions[0]

        if isinstance(array_expr, exp.Dot):
            # This might be struct_array[0].field
            return self._extract_struct_array_path(array_expr, index_expr, table_name)

    return None
```

### 3. Array Operations Support

#### UNNEST/EXPLODE Tracking:

```python
def _detect_array_operations(self, parsed_sql):
    """Detect array operations in SQL and track their lineage"""
    array_ops = []

    # Find UNNEST operations
    for unnest in parsed_sql.find_all(exp.Unnest):
        array_ops.append({
            'type': 'UNNEST',
            'expression': unnest,
            'source_arrays': self._extract_unnest_sources(unnest)
        })

    # Find EXPLODE operations
    for explode in parsed_sql.find_all(exp.Explode):
        array_ops.append({
            'type': 'EXPLODE',
            'expression': explode,
            'source_arrays': self._extract_explode_sources(explode)
        })

    return array_ops

def _extract_unnest_sources(self, unnest_expr):
    """Extract source arrays from UNNEST expression"""
    sources = []
    for expr in unnest_expr.expressions:
        if isinstance(expr, exp.Column):
            sources.append({
                'column': expr.name,
                'table': expr.table,
                'type': 'column'
            })
        # Handle more complex expressions
    return sources
```

#### Array Indexing Support:

```python
def _handle_array_indexing(self, expression, table_name):
    """Handle array indexing like array_col[0] or array_col[OFFSET(0)]"""
    if isinstance(expression, exp.Bracket):
        array_expr = expression.this
        index_expr = expression.expressions[0]

        if isinstance(array_expr, exp.Column):
            return {
                'type': 'array_index',
                'array_column': array_expr.name,
                'index': str(index_expr),
                'table': table_name
            }

    return None
```

### 4. JSON Path Operations Support

#### JSON Path Expression Parsing:

```python
def _detect_json_operations(self, parsed_sql):
    """Detect JSON operations and extract path information"""
    json_ops = []

    # Find JSON_EXTRACT operations
    for json_extract in parsed_sql.find_all(exp.JSONExtract):
        json_ops.append({
            'type': 'JSON_EXTRACT',
            'expression': json_extract,
            'source_column': self._extract_json_source(json_extract),
            'path': self._extract_json_path(json_extract)
        })

    # Find JSON_EXTRACT_SCALAR operations
    for json_scalar in parsed_sql.find_all(exp.JSONExtractScalar):
        json_ops.append({
            'type': 'JSON_EXTRACT_SCALAR',
            'expression': json_scalar,
            'source_column': self._extract_json_source(json_scalar),
            'path': self._extract_json_path(json_scalar)
        })

    return json_ops

def _extract_json_path(self, json_expr):
    """Extract JSON path from JSON operation"""
    path_expr = json_expr.expression
    if isinstance(path_expr, exp.Literal):
        return path_expr.this
    # Handle more complex path expressions
    return str(path_expr)
```

#### JSON Column Validation:

```python
def _validate_json_path(self, json_column, json_path, table_schema):
    """Validate JSON path against known schema if available"""
    if not self.is_json_column(json_column):
        return False

    # For now, assume all JSON paths are valid
    # Could be enhanced with JSON schema validation
    return True
```

### 5. Enhanced Lineage Extraction

#### Type-Aware Lineage Processing:

```python
def _extract_lineage_for_model_enhanced(self, model_sql, schema, model_node, selected_columns=[]):
    """Enhanced lineage extraction with type awareness"""
    lineage_map = {}

    try:
        parsed_sql = sqlglot.parse_one(model_sql, dialect=self.dialect)
    except Exception as e:
        warnings.warn(f"Error parsing SQL for model {model_node}: {str(e)}")
        return {}

    # Detect special operations
    array_ops = self._detect_array_operations(parsed_sql)
    json_ops = self._detect_json_operations(parsed_sql)
    struct_ops = self._detect_struct_operations(parsed_sql)

    # Process each column with type awareness
    for column_name in selected_columns:
        try:
            # Get base lineage
            lineage_node = lineage(
                column_name, parsed_sql, schema=schema, dialect=self.dialect
            )

            # Enhance with type-specific information
            enhanced_lineage = self._enhance_lineage_with_types(
                lineage_node, column_name, array_ops, json_ops, struct_ops, schema
            )

            lineage_map[column_name] = enhanced_lineage

        except Exception as e:
            self.logger.error(f"Error processing {model_node}.{column_name}: {e}")
            lineage_map[column_name] = []

    return lineage_map
```

### 6. Testing Strategy

#### Enhanced Test Coverage:

```python
def test_array_unnest_lineage():
    """Test lineage tracking through UNNEST operations"""
    # Create test case with UNNEST
    manifest = create_test_manifest("""
        SELECT
            item_name,
            item_price
        FROM source_table
        CROSS JOIN UNNEST(items_array) AS item_table(item_name, item_price)
    """)

    # Verify lineage tracks through UNNEST
    # Expected: item_name -> source_table.items_array.item_name

def test_json_extract_lineage():
    """Test lineage tracking through JSON operations"""
    # Create test case with JSON_EXTRACT
    manifest = create_test_manifest("""
        SELECT
            JSON_EXTRACT_SCALAR(user_data, '$.profile.name') AS user_name,
            JSON_EXTRACT_SCALAR(user_data, '$.profile.age') AS user_age
        FROM source_table
    """)

    # Verify lineage tracks through JSON paths
    # Expected: user_name -> source_table.user_data.profile.name

def test_complex_struct_array_lineage():
    """Test lineage through complex struct array operations"""
    # Test nested struct arrays with UNNEST
    pass
```

## Implementation Priority

### Phase 1: Foundation (High Priority)
1. Enhanced type parsing and integration
2. Improved struct field validation
3. Basic array operation detection

### Phase 2: Core Features (Medium Priority)
1. UNNEST/EXPLODE lineage tracking
2. JSON path operation support
3. Array indexing support

### Phase 3: Advanced Features (Lower Priority)
1. Complex struct array operations
2. Dynamic JSON schema validation
3. Type-based optimization hints

## Benefits

1. **Improved Accuracy**: Type-aware lineage tracking reduces false positives
2. **Better Coverage**: Support for modern data warehouse patterns (arrays, JSON, structs)
3. **Enhanced Usability**: More precise lineage for complex nested data structures
4. **Future-Proofing**: Foundation for additional type-based features

## Considerations

1. **Performance**: Type parsing and validation may add overhead
2. **Complexity**: Additional code complexity for type handling
3. **Compatibility**: Ensure backward compatibility with existing functionality
4. **Testing**: Comprehensive test coverage needed for new type-aware features

This analysis provides a roadmap for significantly enhancing the type system integration while maintaining the tool's existing functionality and performance characteristics.

# Analysis: Type System Improvements for dbt-column-lineage-extractor

## Overview

This document outlines potential improvements to the type system and lineage extraction capabilities, particularly focusing on complex struct operations and nested field tracking.

## Current Limitations

### 1. Struct Field Lineage in Complex Aggregations

**Problem**: When struct fields are created through complex aggregations (like `array_agg` with conditional `struct()` constructors), the lineage extractor cannot trace individual struct fields back to their source columns.

**Example**:
```sql
-- Works: Simple struct construction
SELECT struct(page_type, section_name) as shop_app FROM source_table

-- Doesn't work: Complex aggregated struct
SELECT
  array_agg(
    if(
      is_native,
      struct(
        page_type,
        section_name,
        y_pos,
        section_y_pos,
        absolute_y_pos,
        algorithm_id,
        click_count
      ),
      NULL
    )
    IGNORE NULLS
  ) as per_page_position_algo
FROM source_table
GROUP BY user_id, product_id
```

### 2. Nested Struct Field Access

**Problem**: Deeply nested struct fields (e.g., `shop_app.per_page_position_algo.absolute_y_pos`) show empty lineage because sqlglot's lineage tracking loses context within aggregation functions.

**Current Behavior**:
- ✅ `shop_app` → Has lineage to source columns
- ❌ `shop_app.per_page_position_algo` → Empty lineage
- ❌ `shop_app.per_page_position_algo.absolute_y_pos` → Empty lineage

---

## What Would Be Needed to Support Complex Aggregated Nested Structs

### 1. Enhanced AST Analysis

**Current Approach**: sqlglot's `lineage()` function works at the expression level but doesn't deeply analyze the contents of aggregation functions.

**Needed Enhancement**:
```python
def enhanced_struct_lineage_analysis(expression):
    """
    Custom AST walker that:
    1. Identifies struct constructors within aggregations
    2. Maps struct field names to their source expressions
    3. Tracks conditional logic within aggregations
    """

    # Walk through the AST looking for patterns like:
    # array_agg(struct(field1, field2, ...))
    # struct(field1, field2, ...)
    # if(condition, struct(...), null)

    struct_field_mappings = {}

    for node in expression.walk():
        if isinstance(node, exp.Struct):
            # Extract field mappings from struct constructor
            field_mappings = extract_struct_field_sources(node)
            struct_field_mappings.update(field_mappings)

    return struct_field_mappings
```

### 2. Struct Field Source Mapping

**Technical Approach**:
```python
class StructFieldLineageExtractor:
    def extract_struct_field_sources(self, struct_node):
        """
        Parse struct constructor to map field names to source columns.

        Example:
        struct(page_type, section_name as section, y_pos)

        Returns:
        {
            'page_type': 'page_type',
            'section': 'section_name',
            'y_pos': 'y_pos'
        }
        """
        field_mappings = {}

        for i, expression in enumerate(struct_node.expressions):
            if isinstance(expression, exp.Alias):
                # Named field: section_name as section
                field_name = expression.alias
                source_expr = expression.this
            else:
                # Unnamed field: page_type
                field_name = self._extract_field_name(expression)
                source_expr = expression

            # Recursively extract source columns from the expression
            source_columns = self._extract_source_columns(source_expr)
            field_mappings[field_name] = source_columns

        return field_mappings
```

### 3. Aggregation Context Tracking

**Challenge**: Aggregations like `array_agg()` create arrays of structs, but we need to track that the struct fields still come from the original source columns.

**Solution**:
```python
class AggregationContextTracker:
    def track_aggregation_sources(self, agg_node):
        """
        Track source columns through aggregation functions.

        For: array_agg(struct(page_type, section_name))
        Still maps:
        - page_type → source.page_type
        - section_name → source.section_name
        """

        if isinstance(agg_node, exp.ArrayAgg):
            # Extract the inner expression (struct constructor)
            inner_expr = agg_node.this

            if isinstance(inner_expr, exp.If):
                # Handle conditional aggregation: if(condition, struct(...), null)
                struct_expr = inner_expr.args.get('this')  # The true case
                if isinstance(struct_expr, exp.Struct):
                    return self.extract_struct_field_sources(struct_expr)
            elif isinstance(inner_expr, exp.Struct):
                return self.extract_struct_field_sources(inner_expr)

        return {}
```

### 4. Conditional Logic Analysis

**Pattern**: `if(condition, struct(...), null)` within aggregations

**Needed Enhancement**:
```python
def analyze_conditional_struct_construction(if_node):
    """
    Analyze conditional struct construction patterns.

    For: if(is_native, struct(page_type, section_name), null)
    Maps struct fields to source columns while preserving the condition context.
    """

    condition = if_node.this
    true_case = if_node.args.get('this')
    false_case = if_node.args.get('false')

    # Extract struct field mappings from the true case
    if isinstance(true_case, exp.Struct):
        struct_mappings = extract_struct_field_sources(true_case)

        # Preserve condition context for more advanced analysis
        return {
            'condition': condition,
            'struct_fields': struct_mappings,
            'fallback': false_case
        }

    return {}
```

### 5. Nested Path Resolution

**Challenge**: Resolving paths like `shop_app.per_page_position_algo.absolute_y_pos`

**Solution**:
```python
class NestedPathResolver:
    def resolve_nested_struct_path(self, column_path, struct_definitions):
        """
        Resolve nested struct paths to their source columns.

        Args:
            column_path: "shop_app.per_page_position_algo.absolute_y_pos"
            struct_definitions: Mapping of struct fields to their sources

        Returns:
            Source column information for the deeply nested field
        """

        path_parts = column_path.split('.')
        current_struct = struct_definitions

        for part in path_parts:
            if part in current_struct:
                current_struct = current_struct[part]
            else:
                return None  # Path not found

        return current_struct
```

---

## Implementation Approaches

### Approach 1: Custom sqlglot Extension

**Pros**:
- Leverages existing sqlglot infrastructure
- Maintains compatibility with current codebase
- Can contribute improvements back to sqlglot

**Cons**:
- Requires deep understanding of sqlglot internals
- May have performance implications
- Complex to implement correctly

**Implementation Strategy**:
```python
class EnhancedLineageExtractor:
    def __init__(self, base_extractor):
        self.base_extractor = base_extractor
        self.struct_analyzers = [
            SimpleStructAnalyzer(),
            AggregatedStructAnalyzer(),
            ConditionalStructAnalyzer(),
        ]

    def extract_lineage_with_struct_support(self, sql, schema):
        # First run standard sqlglot lineage
        base_lineage = self.base_extractor.lineage(sql, schema)

        # Then enhance with struct-specific analysis
        enhanced_lineage = self.enhance_with_struct_analysis(base_lineage, sql)

        return enhanced_lineage
```

### Approach 2: Pattern-Based Analysis

**Pros**:
- More targeted and efficient
- Easier to implement and maintain
- Can handle specific common patterns

**Cons**:
- Less general than full AST analysis
- May miss edge cases
- Requires pattern identification and maintenance

**Implementation Strategy**:
```python
class PatternBasedStructAnalyzer:
    def __init__(self):
        self.patterns = [
            ArrayAggStructPattern(),
            ConditionalStructPattern(),
            NestedStructPattern(),
        ]

    def analyze_struct_patterns(self, sql):
        struct_lineage = {}

        for pattern in self.patterns:
            matches = pattern.find_matches(sql)
            for match in matches:
                lineage_info = pattern.extract_lineage(match)
                struct_lineage.update(lineage_info)

        return struct_lineage
```

### Approach 3: Hybrid Approach

**Recommended**: Combine both approaches for maximum coverage

```python
class HybridStructLineageExtractor:
    def extract_struct_lineage(self, sql, schema):
        # 1. Pattern-based analysis for common cases
        pattern_results = self.pattern_analyzer.analyze(sql)

        # 2. Enhanced AST analysis for complex cases
        ast_results = self.ast_analyzer.analyze(sql, schema)

        # 3. Merge and deduplicate results
        return self.merge_results(pattern_results, ast_results)
```

---

## Implementation Challenges

### 1. Performance Impact

**Challenge**: Deep AST analysis can be computationally expensive

**Mitigation Strategies**:
- Implement caching for repeated analyses
- Use pattern matching to avoid full AST traversal when possible
- Provide configuration options to disable complex analysis when not needed

### 2. SQL Dialect Variations

**Challenge**: Different SQL dialects have different struct syntax

**Solutions**:
- Implement dialect-specific struct analyzers
- Leverage sqlglot's dialect abstraction layer
- Test against multiple dialects

### 3. Edge Cases and Complexity

**Challenge**: Struct construction can be arbitrarily complex

**Management Strategies**:
- Start with common patterns and incrementally add support
- Provide clear documentation on supported vs. unsupported patterns
- Implement graceful fallbacks for unsupported cases

### 4. Maintenance Overhead

**Challenge**: Complex analysis code requires ongoing maintenance

**Mitigation**:
- Comprehensive test suite with various struct patterns
- Clear separation of concerns between analyzers
- Extensive documentation and examples

---

## Recommended Implementation Plan

### Phase 1: Foundation (2-3 weeks)
1. Implement basic struct field source mapping
2. Add support for simple aggregated structs
3. Create test suite for struct lineage patterns

### Phase 2: Complex Patterns (3-4 weeks)
1. Add conditional struct construction support
2. Implement nested path resolution
3. Add array aggregation context tracking

### Phase 3: Integration & Optimization (2-3 weeks)
1. Integrate with existing lineage extraction
2. Performance optimization and caching
3. Comprehensive testing and validation

### Phase 4: Advanced Features (2-3 weeks)
1. Multi-dialect support
2. Advanced pattern recognition
3. Documentation and examples

---

## Conclusion

Supporting lineage for complex aggregated nested structs requires significant enhancements to the current approach. While challenging, it's achievable through a combination of enhanced AST analysis, pattern recognition, and careful handling of aggregation contexts.

The recommended hybrid approach balances implementation complexity with functionality, providing a path forward that can be implemented incrementally while maintaining system stability and performance.

## Additional Considerations for Complex Aggregated Nested Structs

Based on the analysis of your specific use case with the `feature_store__primitive__user_product_daily_clicks` model, here are some additional considerations:

### 1. BigQuery-Specific Struct Semantics

**Your Model Pattern**:
```sql
struct(
  struct(
    sum(if(is_native, click_count, NULL)) AS click_count,
    sum(if(is_native AND section_name IS DISTINCT FROM 'orders', click_count, NULL)) AS click_count_excluding_orders_section
  ) AS overall,
  array_agg(
    if(
      is_native,
      struct(
        page_type,
        section_name,
        y_pos,
        section_y_pos,
        absolute_y_pos,
        algorithm_id,
        click_count
      ),
      NULL
    )
    IGNORE NULLS
  ) AS per_page_position_algo
) AS shop_app
```

**Challenge**: This creates a nested struct with both aggregated scalar values and arrays of structs.

**Enhancement Needed**:
```python
class BigQueryStructAggregationAnalyzer:
    def analyze_nested_struct_with_mixed_aggregations(self, struct_node):
        """
        Handle patterns like:
        struct(
          struct(sum(field1)) as scalar_agg,
          array_agg(struct(field2, field3)) as array_agg
        )
        """

        field_lineage = {}

        for field_expr in struct_node.expressions:
            if isinstance(field_expr, exp.Alias):
                field_name = field_expr.alias
                field_value = field_expr.this

                # Handle nested struct with aggregations
                if isinstance(field_value, exp.Struct):
                    nested_lineage = self.analyze_aggregated_struct(field_value)
                    field_lineage[field_name] = nested_lineage

                # Handle array aggregation of structs
                elif isinstance(field_value, exp.ArrayAgg):
                    array_lineage = self.analyze_array_agg_struct(field_value)
                    field_lineage[field_name] = array_lineage

        return field_lineage
```

### 2. Multi-Level Grouping Context

**Your Pattern**: Aggregations at multiple levels (per user_id/product_id, then per page_type/section/etc.)

**Needed Enhancement**:
```python
def track_multi_level_aggregation_context(self, group_by_columns, agg_expressions):
    """
    Track how aggregations affect struct field lineage across multiple grouping levels.

    For your case:
    - GROUP BY user_id, product_id at the top level
    - Conditional aggregations within struct constructors
    """

    # Track which source columns contribute to each aggregated field
    # considering the grouping context
    pass
```

### 3. Type-Aware Lineage Tracking

**Enhancement**: Use BigQuery's type system to better understand struct field relationships

```python
class TypeAwareStructLineageExtractor:
    def extract_with_type_information(self, sql, schema):
        """
        Use BigQuery's STRUCT type information to:
        1. Understand expected struct field types
        2. Validate lineage mappings against type constraints
        3. Provide better error messages for type mismatches
        """

        # Analyze struct types from catalog
        struct_types = self.extract_struct_types_from_catalog(schema)

        # Match struct construction patterns with expected types
        lineage_with_types = self.match_construction_to_types(sql, struct_types)

        return lineage_with_types
```

This analysis shows that while the current empty lineage for nested struct fields is expected given sqlglot's limitations, there are clear technical paths forward to implement support for these complex patterns. The key is implementing a combination of enhanced AST analysis, pattern recognition, and type-aware processing.
