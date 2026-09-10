"""Services: business operations, and the transaction boundary.

ARCHITECTURAL DECISION -- services own commits; routers and repositories do not.

A service method corresponds to one complete business operation, which is
exactly the unit that must be atomic. Placing the commit anywhere else breaks
that correspondence:

*   COMMITTING IN THE ROUTER (or in the `get_db` dependency) makes the HTTP
    request the transaction boundary. A request performing two writes would
    have them committed independently, so a failure between them leaves the
    database in a state the domain considers impossible.
*   COMMITTING IN THE REPOSITORY makes each individual write atomic and the
    operation as a whole not atomic -- the worst of both, because it looks
    safe.

Services also translate between the outside world and the domain: they accept
validated input, orchestrate repositories, enforce business rules, and raise
domain errors. They never import FastAPI, so the same operations are callable
from a worker, a CLI or a scheduled job.
"""
