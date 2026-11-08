# apps/worker

The background worker shares the API's Python package (`pro_trading.worker`) so that models,
settings and the outbox code have a single source. Run it from `apps/api`:

```bash
uv run celery -A pro_trading.worker worker --beat --loglevel=info
```

Module 00 ships only the outbox dispatcher (no external handlers registered yet).
