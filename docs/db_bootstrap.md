# Database bootstrap (ArangoSearch & Vector)

This repo bootstraps search & vector capabilities automatically.

## What gets created
- Analyzer: `text_en` (locale=en, lowercase, stemming)
- View: `nodes_search` with links on fields: `rationale | description | reason | summary`
- (Optional) Vector index on `nodes.embedding` if you enable it at the DB

## Compose
A one-shot `bootstrap` service runs at `docker-compose up`:

```yaml
bootstrap:
  image: arangodb/arangodb:3.12
  depends_on:
    arangodb:
      condition: service_started
  volumes:
    - ./:/app
  working_dir: /app
  entrypoint: ["/bin/bash", "-lc", "chmod +x ops/bootstrap_arango.sh && ./ops/bootstrap_arango.sh"]
  restart: "no"
