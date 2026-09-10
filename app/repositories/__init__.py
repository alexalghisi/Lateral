"""Repositories: the only place that knows how data is queried and stored.

ARCHITECTURAL DECISION -- a repository layer between services and SQLAlchemy.

The layer exists to keep query construction out of business logic. Three
concrete payoffs:

1.  SERVICES STAY READABLE. `users.get_by_email(email)` states an intent;
    `session.execute(select(User).where(...)).scalar_one_or_none()` states a
    mechanism. Business rules are easier to review when they are not
    interleaved with query builders.

2.  QUERIES ARE REUSED, NOT REINVENTED. Without this layer, the same lookup
    gets rewritten in several services, and the copies drift -- one filters on
    `is_active`, another forgets to.

3.  THE ORM STAYS REPLACEABLE. Nothing above this layer imports SQLAlchemy, so
    swapping the persistence mechanism is a change confined to one package.
    That is Dependency Inversion applied to storage.

Repositories deliberately do NOT commit. They add, they query, they flush when
a generated key is needed -- but the transaction boundary belongs to the
service, which is the only layer that knows when a business operation is
complete. See app/db/session.py.
"""
