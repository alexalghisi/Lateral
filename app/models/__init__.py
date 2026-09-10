"""SQLAlchemy ORM models.

IMPORTANT -- every model module must be imported here.

Alembic autogenerate works by diffing `Base.metadata` against the live
database, and a model class only registers itself on that metadata when its
module is imported. A model that is never imported is invisible to Alembic,
which will cheerfully generate an empty migration and report success. The
table then does not exist in production and the failure surfaces as a runtime
error on the first query.

Importing them here, in the package `__init__`, means a single
`import app.models` in migrations/env.py guarantees the metadata is complete.
"""

from app.models.order import Order, OrderItem
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User, UserRole

# Re-exported explicitly so `from app.models import User` is the supported
# import path and linters do not flag the imports above as unused.
__all__ = [
    "MenuItem",
    "Order",
    "OrderItem",
    "Restaurant",
    "User",
    "UserRole",
]
