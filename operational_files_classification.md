# Operational Files Classification

## 1. Dependency & Packaging
Files that define what gets installed and how services/packages are built:

- `requirements/dev.txt`
- `requirements/runtime.txt`
- `services/api_edge/pyproject.toml`
- `services/gateway/pyproject.toml`
- `services/ingest/pyproject.toml`
- `services/memory_api/pyproject.toml`
- `packages/core_config/pyproject.toml`
- `packages/core_logging/pyproject.toml`
- `packages/core_models/pyproject.toml`
- `packages/core_storage/pyproject.toml`
- `packages/core_utils/pyproject.toml`
- `packages/link_utils/pyproject.toml`

## 2. Containerization & Orchestration
Build and compose definitions for runtime environments:

- `ops/docker/Dockerfile.python`
- `docker-compose.yml`

## 3. CI/CD & Test Runner Configuration
Infrastructure that drives test execution and CI validation:

- `.github/workflows/ci.yml`
- `pytest.ini`

## 4. Environment & Telemetry Configuration
Runtime configuration and observability bootstrap:

- `.env`
- `ops/otel/otel-collector-config.yaml`

## 5. Operational Scripts
Bootstrap, install, seeding, smoke tests, and service entrypoints:

- `ops/bootstrap_arango.sh`
- `scripts/dev_install.sh`
- `scripts/seed_memory.sh`
- `scripts/smoke.sh`
- `services/api_edge/entrypoint.sh`
- `services/gateway/entrypoint.sh`
- `services/ingest/entrypoint.sh`
- `services/memory_api/entrypoint.sh`

## Complete Alphabetical List (26 files)

1. `.env`
2. `.github/workflows/ci.yml`
3. `docker-compose.yml`
4. `ops/bootstrap_arango.sh`
5. `ops/docker/Dockerfile.python`
6. `ops/otel/otel-collector-config.yaml`
7. `packages/core_config/pyproject.toml`
8. `packages/core_logging/pyproject.toml`
9. `packages/core_models/pyproject.toml`
10. `packages/core_storage/pyproject.toml`
11. `packages/core_utils/pyproject.toml`
12. `packages/link_utils/pyproject.toml`
13. `pytest.ini`
14. `requirements/dev.txt`
15. `requirements/runtime.txt`
16. `scripts/dev_install.sh`
17. `scripts/seed_memory.sh`
18. `scripts/smoke.sh`
19. `services/api_edge/entrypoint.sh`
20. `services/api_edge/pyproject.toml`
21. `services/gateway/entrypoint.sh`
22. `services/gateway/pyproject.toml`
23. `services/ingest/entrypoint.sh`
24. `services/ingest/pyproject.toml`
25. `services/memory_api/entrypoint.sh`
26. `services/memory_api/pyproject.toml`