"""Aggregation of every versioned API route.

ARCHITECTURAL DECISION -- the version prefix is applied in ONE place.

Each route module declares only its own prefix (`/auth`, `/restaurants`); the
`/api/v1` segment is added here. Repeating the full prefix in every router
would guarantee that one of them eventually disagrees with the rest, and
introducing a v2 would mean editing every module rather than mounting a second
aggregate router.

Versioning the URL path, rather than negotiating on a header, is chosen because
it is visible in logs, trivially cacheable, and testable with a browser.
"""

from fastapi import APIRouter

from app.api.routes import auth, menu_items, restaurants

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(restaurants.router)
api_router.include_router(menu_items.router)
