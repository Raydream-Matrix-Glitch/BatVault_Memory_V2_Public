services/gateway/src/gateway/app.py.

Replace Erroneous Decorator

Change route from "/ask" → "/v2/ask"

Remove @log_stage decorator

Make ask an async def

Inline log_stage(...) at the top of the function

Correct MinIO Helper

Use the client instance, not undefined mc

Fix validate_and_fix & Templater Wiring

Call validate_and_fix(answer, allowed_ids, anchor_id) properly (it returns a tuple)

Unpack into (answer, changed, errs)

Deduplicate Meta Keys

Stop flattening selector_meta into the top-level; namespace it under "selector_meta"

Re-compute allowed_ids

(Already handled by your call to _compute_allowed_ids(ev) immediately after truncate_evidence — no change required here.)