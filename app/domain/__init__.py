"""Pure domain layer: business rules with no infrastructure dependencies.

ARCHITECTURAL DECISION -- this package imports nothing from FastAPI, nothing
from SQLAlchemy, and nothing from `app.api`. That constraint is the whole
value proposition of the layer.

Business rules expressed as pure functions over plain Python types can be
tested exhaustively in microseconds with no database, no HTTP client and no
fixtures. Rules entangled with ORM models can only be tested by constructing
persistent objects, which is slow enough that people stop doing it thoroughly
-- and the rules that stop being tested thoroughly are precisely the ones that
cost money when they break.

This is the Dependency Inversion Principle at package scale: the layers that
change most often (transport, persistence) depend on the layer that changes
least (business rules), never the reverse.
"""
