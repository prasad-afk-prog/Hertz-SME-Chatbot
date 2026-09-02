"""HFB service code (Track A + Track B), per POA/18 §3 and the ratified repo
layout in POA/15 §12.

- ``services.platform``       — shared FastAPI/Celery template (M15, foundational)
- ``services.event_pipeline`` — Track A: event & trigger pipeline (POA 01–07)
- ``services.conversation``   — Track B: conversation/config/reporting (POA 08–14)

The generator's Pydantic models (``generator.models``) remain the single source
of truth for the wire contract (design principle P3); services import them.
"""
