"""Cross-cutting concerns: configuration, security primitives, shared errors.

This package must never import from `app.api`, `app.services` or `app.db`.
Keeping the dependency arrows pointing inwards is what stops a layered design
from decaying into a cycle.
"""
