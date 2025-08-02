# Codebase Structure

## Services

### API Edge Service
```
services/api_edge/src/api_edge/
├── __init__.py
├── __main__.py
└── app.py
```

### Gateway Service
```
services/gateway/src/gateway/
├── __init__.py
├── __main__.py
├── app.py
├── evidence.py
├── load_shed.py
├── match_snippet.py
├── prompt_envelope.py
├── resolver/
│   ├── __init__.py
│   ├── embedding_model.py
│   ├── fallback_search.py
│   └── reranker.py
├── selector.py
└── templater.py
```

### Ingest Service
```
services/ingest/src/ingest/
├── __init__.py
├── __main__.py
├── app.py
├── cli.py
├── watcher.py
├── catalog/
│   └── field_catalog.py
├── pipeline/
│   ├── graph_upsert.py
│   ├── normalize.py
│   └── snippet_enricher.py
└── schemas/
    ├── __init__.py
    └── json_v2/
        └── __init__.py
```

### Memory API Service
```
services/memory_api/src/memory_api/
├── __init__.py
├── __main__.py
└── app.py
```

---

## Packages

### Core Configuration
```
packages/core_config/src/core_config/
├── __init__.py
├── constants.py
└── settings.py
```

### Core Logging
```
packages/core_logging/src/core_logging/
├── __init__.py
└── logger.py
```

### Core Models
```
packages/core_models/src/core_models/
├── __init__.py
├── models.py
└── responses.py
```

### Core Storage
```
packages/core_storage/src/core_storage/
├── __init__.py
├── arangodb.py
└── minio_utils.py
```

### Core Utils
```
packages/core_utils/src/core_utils/
├── __init__.py
├── async_timeout.py
├── fingerprints.py
├── health.py
├── ids.py
├── snapshot.py
└── uvicorn_entry.py
```

### Core Validator
```
packages/core_validator/src/core_validator/
├── __init__.py
└── validator.py
```

### Link Utils
```
packages/link_utils/src/link_utils/
├── __init__.py
└── derive_links.py
```

---