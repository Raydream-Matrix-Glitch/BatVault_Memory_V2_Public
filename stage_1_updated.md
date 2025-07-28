# Milestone 0 + 1 Combined Changes - Bootstrapping + Ingest V2

## 0. New Package: core-storage

### ArangoDB Adapters Package
- **Location**: `packages/core_storage/`
- **Purpose**: Centralized ArangoDB operations for BatVault

**Files Added:**
- `pyproject.toml` - Package configuration with python-arango and pydantic dependencies
- `src/core_storage/__init__.py` - Package exports
- `src/core_storage/arangodb.py` - Main ArangoStore class

**ArangoStore Features:**
- Database and collection initialization
- Graph management with edge definitions
- Node and edge upsert operations
- Field and relation catalog management
- Snapshot etag handling
- Enriched data retrieval for decisions, events, and transitions

---

## 1. Configuration Updates

### Enhanced Settings
- **File**: `packages/core_config/src/core_config/settings.py`

**New Configuration Options:**
- `arango_graph_name` - Graph name configuration
- `arango_catalog_collection` - Catalog collection name
- `arango_meta_collection` - Metadata collection name
- Vector index settings (embedding dimensions, metrics, HNSW parameters)

---

## 2. Docker Infrastructure

### Updated Python Dockerfile
- **File**: `ops/docker/Dockerfile.python`

**Changes:**
- Added `core_storage` package installation
- Maintains proper dependency order for shared packages

---

## 3. JSON Schema Validation

### Strict V2 Schemas
- **Location**: `services/ingest/schemas/json-v2/`

**Schema Files:**
- `decision.schema.json` - Decision object validation
- `event.schema.json` - Event object validation  
- `transition.schema.json` - Transition object validation

**Validation Rules:**
- ID pattern enforcement: `^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$`
- Required fields per object type
- Timestamp format validation (ISO date-time)
- Enum constraints for transition relations

---

## 4. Data Normalization Pipeline

### Normalization Engine
- **File**: `services/ingest/src/ingest/pipeline/normalize.py`

**Normalization Features:**
- **ID Slugification**: Converts invalid IDs to compliant format
- **Timestamp Standardization**: Converts all timestamps to UTC ISO format
- **Text Normalization**: Unicode normalization, whitespace cleanup, length limits
- **Tag Processing**: Lowercase, deduplication, sorting
- **Summary Repair**: Auto-generates summaries for events when missing

**Functions:**
- `normalize_decision()` - Decision-specific normalization
- `normalize_event()` - Event-specific normalization with summary repair
- `normalize_transition()` - Transition-specific normalization
- `derive_backlinks()` - Ensures bidirectional relationships

---

## 5. Graph Database Operations

### Graph Upsert Pipeline
- **File**: `services/ingest/src/ingest/pipeline/graph_upsert.py`

**Operations:**
- Node upserts for decisions, events, and transitions
- Edge creation for `LED_TO` relationships (events → decisions)
- Edge creation for `CAUSAL_PRECEDES` relationships (transitions)
- Maintains graph integrity with proper edge definitions

---

## 6. Catalog Management

### Field and Relation Catalogs
- **File**: `services/ingest/src/ingest/catalog/field_catalog.py`

**Catalog Features:**
- **Field Aliases**: Maps alternative field names to canonical ones
- **Dynamic Field Discovery**: Observes actual fields in data
- **Relation Catalog**: Defines available relationship types
- **Zero-code Compatibility**: Supports field additions without code changes

---

## 7. Enhanced Ingest CLI

### Complete Pipeline Implementation
- **File**: `services/ingest/src/ingest/cli.py`

**Pipeline Steps:**
1. **Validation**: Schema validation using JSON Schema
2. **Normalization**: Data cleaning and standardization
3. **Derivation**: Backlink generation and relationship repair
4. **Graph Upsert**: Database persistence
5. **Catalog Publishing**: Field and relation catalog updates

**Features:**
- Comprehensive error reporting
- Snapshot etag computation
- Structured logging with stage tracking
- Batch processing of JSON files

---

## 8. Memory API Enhancement

### Real Data Service
- **File**: `services/memory_api/src/memory_api/app.py`

**New Endpoints:**

**Catalog Endpoints:**
- `GET /api/schema/fields` - Field catalog with aliases
- `GET /api/schema/rels` - Available relationship types

**Enrichment Endpoints:**
- `GET /api/enrich/decision/{node_id}` - Normalized decision data
- `GET /api/enrich/event/{node_id}` - Normalized event data
- `GET /api/enrich/transition/{node_id}` - Normalized transition data

**Features:**
- Snapshot etag headers (`x-snapshot-etag`)
- Real ArangoDB integration
- Proper error handling (404 for missing items)
- Health checks with database connectivity

---

## 9. Contract Testing

### Orphan and Empty Array Handling
- **File**: `services/ingest/tests/test_contract_orphans.py`

**Test Coverage:**
- Empty array handling for relationships
- Orphaned reference tolerance
- Backlink derivation validation
- Transition attachment verification

---

## 10. Memory API Testing

### Service Validation
- **File**: `services/memory_api/tests/test_enrich_stubs.py`

**Test Coverage:**
- Catalog endpoint functionality
- Response structure validation
- Mock store integration

---

## 11. Updated Seed Script

### Enhanced Memory Seeding
- **File**: `scripts/seed_memory.sh`

**New Behavior:**
- Validates JSON against schemas
- Normalizes all data
- Populates ArangoDB with graph structure
- Publishes field and relation catalogs
- Sets snapshot etag for cache invalidation

---

## Key Improvements Summary

### Data Quality
- Strict JSON schema validation
- Comprehensive data normalization
- Automated text repair and cleanup
- Consistent timestamp handling

### Graph Integrity
- Bidirectional relationship enforcement
- Proper edge creation and management
- Orphan-tolerant referential integrity
- Deterministic ID generation

### API Enhancement
- Real database-backed endpoints
- Snapshot etag support for caching
- Structured error responses
- Field and relation discovery

### Operational Excellence
- Comprehensive logging and monitoring
- Contract-based testing
- Batch processing capabilities
- Zero-downtime field additions

## 12. Enhanced Core Utilities

### Canonical ID Slugification
- **File**: `packages/core-utils/src/core_utils/ids.py`

**New Functions:**
- `slugify_id()` - Canonical slug transformation with NFKC normalization
- Enhanced `compute_request_id()` and `idempotency_key()` functions

**Features:**
- Unicode normalization (NFKC)
- Lowercase conversion
- Special character mapping to hyphens
- Hyphen collapse and trimming
- Regex compliance validation

### Core Utils Testing
- **File**: `packages/core-utils/tests/test_slugify.py`
- Comprehensive tests for slug transformation rules
- Regex pattern validation

---

## 13. Milestone 0 Strict Validation

### Enhanced Ingest Validation
- **File**: `services/ingest/src/ingest/cli.py` (M0 version)

**Validation Rules:**
- **ID Regex**: Strict enforcement of `^[a-z0-9][a-z0-9-]{2,}[a-z0-9]# Milestone 0 + 1 Combined Changes - Bootstrapping + Ingest V2

## 0. New Package: core-storage

### ArangoDB Adapters Package
- **Location**: `packages/core_storage/`
- **Purpose**: Centralized ArangoDB operations for BatVault

**Files Added:**
- `pyproject.toml` - Package configuration with python-arango and pydantic dependencies
- `src/core_storage/__init__.py` - Package exports
- `src/core_storage/arangodb.py` - Main ArangoStore class

**ArangoStore Features:**
- Database and collection initialization
- Graph management with edge definitions
- Node and edge upsert operations
- Field and relation catalog management
- Snapshot etag handling
- Enriched data retrieval for decisions, events, and transitions

---

## 1. Configuration Updates

### Enhanced Settings
- **File**: `packages/core_config/src/core_config/settings.py`

**New Configuration Options:**
- `arango_graph_name` - Graph name configuration
- `arango_catalog_collection` - Catalog collection name
- `arango_meta_collection` - Metadata collection name
- Vector index settings (embedding dimensions, metrics, HNSW parameters)

---

## 2. Docker Infrastructure

### Updated Python Dockerfile
- **File**: `ops/docker/Dockerfile.python`

**Changes:**
- Added `core_storage` package installation
- Maintains proper dependency order for shared packages

---

## 3. JSON Schema Validation

### Strict V2 Schemas
- **Location**: `services/ingest/schemas/json-v2/`

**Schema Files:**
- `decision.schema.json` - Decision object validation
- `event.schema.json` - Event object validation  
- `transition.schema.json` - Transition object validation

**Validation Rules:**
- ID pattern enforcement: `^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$`
- Required fields per object type
- Timestamp format validation (ISO date-time)
- Enum constraints for transition relations

---

## 4. Data Normalization Pipeline

### Normalization Engine
- **File**: `services/ingest/src/ingest/pipeline/normalize.py`

**Normalization Features:**
- **ID Slugification**: Converts invalid IDs to compliant format
- **Timestamp Standardization**: Converts all timestamps to UTC ISO format
- **Text Normalization**: Unicode normalization, whitespace cleanup, length limits
- **Tag Processing**: Lowercase, deduplication, sorting
- **Summary Repair**: Auto-generates summaries for events when missing

**Functions:**
- `normalize_decision()` - Decision-specific normalization
- `normalize_event()` - Event-specific normalization with summary repair
- `normalize_transition()` - Transition-specific normalization
- `derive_backlinks()` - Ensures bidirectional relationships

---

## 5. Graph Database Operations

### Graph Upsert Pipeline
- **File**: `services/ingest/src/ingest/pipeline/graph_upsert.py`

**Operations:**
- Node upserts for decisions, events, and transitions
- Edge creation for `LED_TO` relationships (events → decisions)
- Edge creation for `CAUSAL_PRECEDES` relationships (transitions)
- Maintains graph integrity with proper edge definitions

---

## 6. Catalog Management

### Field and Relation Catalogs
- **File**: `services/ingest/src/ingest/catalog/field_catalog.py`

**Catalog Features:**
- **Field Aliases**: Maps alternative field names to canonical ones
- **Dynamic Field Discovery**: Observes actual fields in data
- **Relation Catalog**: Defines available relationship types
- **Zero-code Compatibility**: Supports field additions without code changes

---

## 7. Enhanced Ingest CLI

### Complete Pipeline Implementation
- **File**: `services/ingest/src/ingest/cli.py`

**Pipeline Steps:**
1. **Validation**: Schema validation using JSON Schema
2. **Normalization**: Data cleaning and standardization
3. **Derivation**: Backlink generation and relationship repair
4. **Graph Upsert**: Database persistence
5. **Catalog Publishing**: Field and relation catalog updates

**Features:**
- Comprehensive error reporting
- Snapshot etag computation
- Structured logging with stage tracking
- Batch processing of JSON files

---

## 8. Memory API Enhancement

### Real Data Service
- **File**: `services/memory_api/src/memory_api/app.py`

**New Endpoints:**

**Catalog Endpoints:**
- `GET /api/schema/fields` - Field catalog with aliases
- `GET /api/schema/rels` - Available relationship types

**Enrichment Endpoints:**
- `GET /api/enrich/decision/{node_id}` - Normalized decision data
- `GET /api/enrich/event/{node_id}` - Normalized event data
- `GET /api/enrich/transition/{node_id}` - Normalized transition data

**Features:**
- Snapshot etag headers (`x-snapshot-etag`)
- Real ArangoDB integration
- Proper error handling (404 for missing items)
- Health checks with database connectivity

---

## 9. Contract Testing

### Orphan and Empty Array Handling
- **File**: `services/ingest/tests/test_contract_orphans.py`

**Test Coverage:**
- Empty array handling for relationships
- Orphaned reference tolerance
- Backlink derivation validation
- Transition attachment verification

---

## 10. Memory API Testing

### Service Validation
- **File**: `services/memory_api/tests/test_enrich_stubs.py`

**Test Coverage:**
- Catalog endpoint functionality
- Response structure validation
- Mock store integration

---

## 11. Updated Seed Script

### Enhanced Memory Seeding
- **File**: `scripts/seed_memory.sh`

**New Behavior:**
- Validates JSON against schemas
- Normalizes all data
- Populates ArangoDB with graph structure
- Publishes field and relation catalogs
- Sets snapshot etag for cache invalidation

---

## Key Improvements Summary

### Data Quality
- Strict JSON schema validation
- Comprehensive data normalization
- Automated text repair and cleanup
- Consistent timestamp handling

### Graph Integrity
- Bidirectional relationship enforcement
- Proper edge creation and management
- Orphan-tolerant referential integrity
- Deterministic ID generation

### API Enhancement
- Real database-backed endpoints
- Snapshot etag support for caching
- Structured error responses
- Field and relation discovery

### Operational Excellence
- Comprehensive logging and monitoring
- Contract-based testing
- Batch processing capabilities
- Zero-downtime field additions


- **Timestamp Format**: ISO-8601 UTC with 'Z' suffix required
- **Content Fields**: Must have at least one content field (rationale/description/reason/summary)

**Features:**
- Pre-normalization validation
- Comprehensive error reporting
- Snapshot etag computation
- File batch processing

### Validation Testing
- **File**: `services/ingest/tests/test_strict_id_timestamp.py`
- ID regex pattern testing
- Timestamp format validation
- Edge case handling

---

## 14. Gateway Service Implementation

### Complete Gateway App
- **File**: `services/gateway/src/gateway/app.py`

**Core Features:**
- **MinIO Integration**: Bucket management and lifecycle policies
- **Redis Connectivity**: Caching infrastructure readiness
- **Health Checks**: Comprehensive dependency probing
- **Artifact Management**: Bucket creation and retention policies

**Endpoints:**
- `GET /healthz` - Service health check
- `GET /readyz` - Dependency readiness check
- `POST /ops/minio/ensure-bucket` - Bucket management
- `POST /v2/ask` - Why decision templater (contract-compliant)

### Gateway Models
- **File**: `services/gateway/src/gateway/models.py`

**Response Models:**
- `WhyDecisionAnchor` - Decision anchor structure
- `WhyDecisionEvidence` - Evidence collection model
- `WhyDecisionAnswer` - Answer structure with validation
- `WhyDecisionResponse` - Complete response contract
- `CompletenessFlags` - Evidence completeness indicators

### Templater Logic
- **File**: `services/gateway/src/gateway/templater.py`

**Core Functions:**
- `build_allowed_ids()` - Evidence ID collection
- `deterministic_short_answer()` - Stable answer generation
- `validate_and_fix()` - Answer validation and repair

**Validation Rules:**
- `supporting_ids ⊆ allowed_ids` enforcement
- Anchor citation requirement
- Deterministic fallback responses

---

## 15. Enhanced Ingest Service

### Ingest FastAPI App
- **File**: `services/ingest/src/ingest/app.py`

**Features:**
- Health and readiness endpoints
- Memory API dependency checking
- Service integration validation

---

## 16. API Edge Passthrough

### V2 Ask Endpoint
- **Enhancement**: API Edge service now includes `/v2/ask` passthrough

**Implementation:**
```python
@app.post("/v2/ask")
async def v2_ask_passthrough(request: Request):
    # Routes requests to gateway service
    # Maintains request/response contract
    # Handles JSON parsing and error cases
```

**Features:**
- Request forwarding to gateway service
- JSON payload handling
- Error boundary management
- Response proxying

---

## 17. Gateway Testing

### Contract Compliance Testing
- **File**: `services/gateway/tests/test_templater_contract.py`

**Test Coverage:**
- Response contract validation
- Supporting IDs subset verification
- Anchor citation enforcement
- Evidence structure validation
- Intent classification verification

**Key Assertions:**
- `intent == "why_decision"`
- `supporting_ids ⊆ allowed_ids`
- Anchor always present in supporting_ids
- Response structure compliance

---

## Major System Integration Improvements

### Service Architecture
- **Complete Service Mesh**: All 4 Python services now fully implemented
- **Dependency Health Checks**: Cross-service readiness validation
- **Contract Compliance**: Standardized response formats across services

### Data Pipeline Enhancements
- **Pre-validation**: Strict artifact-level checks before normalization
- **Canonical Transformations**: Consistent ID and text processing
- **Deterministic Responses**: Stable, reproducible templater outputs

### Infrastructure Readiness
- **MinIO Integration**: Complete artifact storage with lifecycle management
- **Redis Connectivity**: Caching infrastructure preparation
- **Health Monitoring**: Comprehensive service health and dependency checks

### API Contract Implementation
- **Why Decision Flow**: Complete `/v2/ask` implementation from edge to gateway
- **Evidence Validation**: Strict supporting ID subset enforcement
- **Fallback Responses**: Deterministic templater when LLM unavailable

### Testing Coverage
- **Unit Tests**: Core utility functions and transformations
- **Contract Tests**: API response structure validation
- **Integration Tests**: Cross-service communication verification

This comprehensive update transforms the system from basic bootstrapping to a fully functional, contract-compliant service mesh with real data processing, validation, and deterministic response generation capabilities.