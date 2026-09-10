"""Pydantic schemas: the API's request and response contracts.

ARCHITECTURAL DECISION -- schemas are separate types from ORM models, and
separate again for reading and writing.

Returning ORM objects directly is the shortcut, and it fails in two directions
at once:

*   OUTBOUND, it leaks. Every column is serialised, including
    `hashed_password`. Adding an internal column later silently publishes it.
*   INBOUND, it over-accepts. If the same type is used to parse a request, a
    client can set any field it names -- including `role`, which turns public
    registration into a way to mint administrators.

Separate schemas make the API surface an explicit, reviewable decision rather
than an accident of the database layout. They also decouple the two: the
storage schema can be refactored without breaking published clients, and the
API contract can evolve without a migration.
"""
