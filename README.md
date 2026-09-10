# Lateral

A backend for a small takeaway platform. Customers browse restaurants and menus,
place orders, and track them through their lifecycle. Internal staff manage the
catalogue and drive orders from `pending` to `delivered`.

Built with FastAPI, PostgreSQL, SQLAlchemy and Alembic, packaged with Docker
Compose behind Nginx.

**Author:** Alghisi Alessandro Paolo — <alexalghisi@gmail.com>

---

## Table of contents

1. [Quick start](#quick-start)
2. [Configuration](#configuration)
3. [Using the API](#using-the-api)
4. [API reference](#api-reference)
5. [The order lifecycle](#the-order-lifecycle)
6. [Architecture](#architecture)
7. [Data model](#data-model)
8. [Security](#security)
9. [Testing](#testing)
10. [Database migrations](#database-migrations)
11. [Development workflow](#development-workflow)
12. [Project layout](#project-layout)
13. [Decisions worth explaining](#decisions-worth-explaining)

---

## Quick start

### Prerequisites

Docker with the Compose plugin is the only requirement. Nothing needs to be
installed on the host — no Python, no PostgreSQL. On macOS,
[Colima](https://github.com/abiosoft/colima) works as the container runtime:

```bash
brew install colima docker docker-compose
colima start
```

### Launch

```bash
git clone https://github.com/alexalghisi/Lateral.git
cd Lateral

# Create your environment file from the documented template
cp .env.example .env

# Generate a real signing key (do not ship the placeholder)
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"
# ...and paste the result over the SECRET_KEY line in .env

docker compose up --build -d
```

That single command builds the API image, starts PostgreSQL, waits for it to
report healthy, **applies all database migrations automatically**, starts
Uvicorn, and puts Nginx in front of it.

### Verify

```bash
curl http://localhost:8080/health/ready
# {"status":"ready"}
```

| URL | What it is |
| --- | --- |
| <http://localhost:8080> | Nginx — the public entrypoint, use this |
| <http://localhost:8080/docs> | Interactive Swagger UI |
| <http://localhost:8080/redoc> | ReDoc reference |
| <http://localhost:8080/openapi.json> | Machine-readable OpenAPI schema |
| <http://localhost:8000> | Uvicorn directly — bypasses the proxy, debugging only |
| `localhost:5432` | PostgreSQL |

The documentation endpoints are **automatically suppressed** when `APP_ENV` is
set to `production`.

### Everyday commands

```bash
docker compose logs -f api        # Follow application logs
docker compose ps                 # Health of every service
docker compose exec -T api pytest # Run the test suite
docker compose down               # Stop, keeping data
docker compose down -v            # Stop and destroy the database volume
```

---

## Configuration

Every setting is read from the environment — twelve-factor style. There are no
per-environment settings modules and no `if ENV == "production"` branches in
application code. The same image runs everywhere; only the injected environment
differs. `.env.example` is the tracked contract and documents each variable
inline; `.env` is git-ignored and never leaves your machine.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `local` | Environment label. `production` disables `/docs` and `/redoc`. |
| `LOG_LEVEL` | `info` | Verbosity for the application and Uvicorn. |
| `SECRET_KEY` | *(none)* | Token signing key. **No default — the app refuses to start without it.** |
| `JWT_ALGORITHM` | `HS256` | Signing algorithm. Pinned at verification time. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime. |
| `POSTGRES_USER` | `lateral` | Database user. |
| `POSTGRES_PASSWORD` | `lateral` | Database password. **Change outside local dev.** |
| `POSTGRES_DB` | `lateral` | Database name. |
| `POSTGRES_HOST` | `db` | The Compose service name; use `localhost` when running on the host. |
| `POSTGRES_PORT` | `5432` | Database port. |
| `NGINX_HOST_PORT` | `8080` | Host port for the proxy. |
| `API_HOST_PORT` | `8000` | Host port for Uvicorn (debugging). |
| `POSTGRES_HOST_PORT` | `5432` | Host port for PostgreSQL. |

`SECRET_KEY` deliberately has **no default value**. A default would be a
functioning key that works perfectly in development and silently ships to
production, where anyone who has read the repository can forge tokens for any
account. Failing to boot is the correct behaviour.

The connection URL is assembled from its parts rather than supplied whole, so
the password stays a discrete, individually injectable secret — which is how
secret managers hand out credentials.

---

## Using the API

A complete session, from empty database to a delivered order.

### 1. Register and log in

```bash
BASE=http://localhost:8080/api/v1

curl -X POST $BASE/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"a-sufficiently-long-password","full_name":"Ada Lovelace"}'
```

```bash
TOKEN=$(curl -s -X POST $BASE/auth/login \
  -d "username=ada@example.com&password=a-sufficiently-long-password" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

Login uses form encoding rather than JSON, because that is what the OAuth2
password flow specifies and what makes the "Authorize" button in Swagger UI work
without custom JavaScript.

Registration **always** creates a customer. The role is not a field on the
request schema, so it cannot be supplied — an attempt to send one is rejected
with 422 rather than quietly ignored.

### 2. Promote an administrator

There is no endpoint that grants the admin role, by design: a self-service
route to privilege escalation is a liability, and the first administrator has to
come from outside the application anyway. Promotion is an operator action:

```bash
docker compose exec -T db psql -U lateral -d lateral \
  -c "UPDATE users SET role='admin' WHERE email='ada@example.com';"
```

Re-issue the token afterwards, since the role is embedded in it.

### 3. Create a restaurant and a menu (admin)

```bash
curl -X POST $BASE/restaurants \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Trattoria Lateral","description":"Napoli style"}'

curl -X POST $BASE/restaurants/1/menu \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Margherita","description":"San Marzano, fior di latte","price_cents":1050}'
```

Prices are integers in **cents**, and the unit is in the field name. Floating
point cannot represent `10.50` exactly, and money that drifts by fractions of a
cent across arithmetic is the classic finance bug. `price_cents` also makes it
impossible to misread the number as euros at a glance.

### 4. Browse (no authentication needed)

```bash
curl "$BASE/restaurants?limit=20&offset=0"
curl $BASE/restaurants/1/menu
```

Browsing is public: requiring an account to look at a menu would cost customers
before the platform earns any. Unavailable items are hidden from the public
view but visible to staff via `?include_unavailable=true`, which is ignored
for anonymous callers rather than rejected.

### 5. Place an order

```bash
curl -X POST $BASE/orders \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"restaurant_id":1,"items":[{"menu_item_id":1,"quantity":2}]}'
```

```json
{
  "id": 1, "customer_id": 1, "restaurant_id": 1,
  "status": "pending", "total_cents": 2100,
  "items": [{
    "id": 1, "menu_item_id": 1, "item_name": "Margherita",
    "unit_price_cents": 1050, "quantity": 2, "subtotal_cents": 2100
  }]
}
```

Notice what the request did **not** contain: a price, a total, or a customer id.
The basket says what to buy; the server decides what it costs and who is buying,
from the menu and the token respectively.

### 6. Track it

```bash
curl $BASE/orders/1 -H "Authorization: Bearer $TOKEN"
curl "$BASE/orders?status=pending" -H "Authorization: Bearer $TOKEN"
```

A customer sees only their own orders. Staff see every order — the same routes,
with the service deciding what each audience may read.

### 7. Advance it (admin)

```bash
curl -X PATCH $BASE/orders/1/status \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"accepted"}'
```

Attempting to skip a step returns `409 Conflict` with an explanation of what
*is* possible:

```json
{"detail": "Cannot change order status from 'pending' to 'delivered'. Allowed transitions from 'pending': accepted, cancelled."}
```

---

## API reference

All endpoints are under `/api/v1`. Authentication is `Authorization: Bearer <token>`.

### Authentication

| Method | Path | Access | Description |
| --- | --- | --- | --- |
| `POST` | `/auth/register` | Public | Create a customer account. Returns 201. |
| `POST` | `/auth/login` | Public | Exchange credentials for an access token (form-encoded). |
| `GET` | `/auth/me` | Authenticated | The current user's profile. |

### Catalogue

| Method | Path | Access | Description |
| --- | --- | --- | --- |
| `GET` | `/restaurants` | Public | Paginated list of active restaurants. |
| `GET` | `/restaurants/{id}` | Public | A single restaurant. |
| `GET` | `/restaurants/{id}/menu` | Public | Available menu items. Staff may pass `?include_unavailable=true`. |
| `POST` | `/restaurants` | **Admin** | Create a restaurant. |
| `PATCH` | `/restaurants/{id}` | **Admin** | Partial update, including deactivation. |
| `POST` | `/restaurants/{id}/menu` | **Admin** | Add a menu item. |
| `PATCH` | `/menu-items/{id}` | **Admin** | Partial update, including availability. |
| `DELETE` | `/menu-items/{id}` | **Admin** | Remove a menu item. Returns 204. |

### Orders

| Method | Path | Access | Description |
| --- | --- | --- | --- |
| `POST` | `/orders` | Authenticated | Place an order. Returns 201. |
| `GET` | `/orders` | Authenticated | Own orders; **all** orders for admins. Supports `?status=`, `?limit=`, `?offset=`. |
| `GET` | `/orders/{id}` | Owner or admin | Track a single order. |
| `PATCH` | `/orders/{id}/status` | **Admin** | Advance the lifecycle. |

### Operations

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health/live` | Liveness. Touches nothing — answers "is the process up". |
| `GET` | `/health/ready` | Readiness. Executes `SELECT 1`; returns 503 if the database is unreachable. |

Health probes sit at the root, outside `/api/v1`. Versioning is a promise to API
consumers about payload stability; an orchestrator is not an API consumer, and
its probe URL should never change because the API reached v2.

### Status codes

| Code | Meaning here |
| --- | --- |
| `401` | Missing, malformed, expired or unverifiable token. |
| `403` | Authenticated, but the role is insufficient. |
| `404` | Does not exist — **or** exists and is none of your business. |
| `409` | Well-formed and authorised, but conflicts with current state. |
| `422` | The request itself is malformed, per the schema. |

The distinction between `409` and `422` is deliberate. `422` means the request
could never be valid; `409` means this exact request would have succeeded at a
different moment.

---

## The order lifecycle

```
        ┌─────────┐      ┌──────────┐      ┌──────────────────┐      ┌───────────┐
        │ pending │─────▶│ accepted │─────▶│ out_for_delivery │─────▶│ delivered │
        └────┬────┘      └────┬─────┘      └──────────────────┘      └───────────┘
             │                │                                        (terminal)
             │                │
             └────────┬───────┘
                      ▼
                ┌───────────┐
                │ cancelled │  (terminal)
                └───────────┘
```

The lifecycle lives in `app/domain/order_state.py` as a **declarative
transition table**, not as scattered `if` statements:

```python
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING:          frozenset({OrderStatus.ACCEPTED, OrderStatus.CANCELLED}),
    OrderStatus.ACCEPTED:         frozenset({OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED}),
    OrderStatus.OUT_FOR_DELIVERY: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED:        frozenset(),
    OrderStatus.CANCELLED:        frozenset(),
}
```

The rules are therefore *data*, and the whole policy is readable in ten lines
rather than reconstructed from conditionals spread across a service. Adding a
status means adding a row; the enforcement code does not change. Every entry
point consults this one table.

Four properties are enforced and tested:

- **No skipping.** `pending → delivered` is rejected. An order cannot be
  delivered by a courier who was never dispatched.
- **No going backwards.** History is not editable through the status field.
- **Terminal states are final.** Nothing follows `delivered` or `cancelled`.
  Unwinding a completed order is a refund — a different business process with
  its own record — not a status change.
- **No self-transitions.** Setting `accepted` on an already-accepted order is a
  `409`. Treating it as a harmless no-op would hide the case where two staff
  members acted at once; rejecting it surfaces the lost update.

`cancelled` is an addition to the brief. Without it, an order a restaurant
cannot fulfil has nowhere to go and sits in `pending` forever. Cancellation is
possible before dispatch only: once a courier is holding the food, the resolution
is a refund, not a status.

Transitions take a `SELECT ... FOR UPDATE` row lock. Advancing a status is a
read-then-write sequence, so without the lock two staff members acting
simultaneously would both read the old status, both find their transition legal,
and both write — one update silently lost, and an audit trail showing a step
that never happened.

---

## Architecture

### Layers

```
  HTTP  ──▶  Routers  ──▶  Services  ──▶  Repositories  ──▶  PostgreSQL
             (app/api)     (app/services)  (app/repositories)
                  │             │
                  └─── Schemas ─┘         Domain (app/domain)
                     (app/schemas)        pure rules, zero imports
```

Each layer has exactly one reason to change, and the dependencies point in one
direction only:

- **Routers** translate HTTP to calls and back. They contain no business logic;
  their longest statements are the dependency declarations that enforce roles.
- **Services** own the use cases and the transaction boundary. They are the only
  layer that calls `commit()`.
- **Repositories** own the queries. They know SQLAlchemy and nothing about HTTP.
- **Domain** holds rules that are true regardless of delivery mechanism — the
  state machine, roles, error taxonomy. It imports **nothing** from the rest of
  the application: not FastAPI, not SQLAlchemy, not Pydantic models.

The domain's total absence of imports is the architectural keystone. It means the
state machine can be tested without a database, a web server, or a single
fixture, and it means the business rules cannot rot into framework details.

### Transaction boundaries

Exactly one layer commits: the service. Repositories `flush()` — making rows
visible to subsequent queries within the transaction, and letting the database
assign identifiers — but never commit. Routers and dependencies never commit
either.

A repository that committed would make it impossible to compose two writes into
one atomic operation. Order placement is precisely that composition: validate
every line against the live menu, snapshot prices, insert the order and all its
lines, and commit **once**. A basket containing a sold-out item therefore leaves
nothing behind — not a partial order containing whatever happened to be in
stock, which would charge a customer for half of what they asked for and give
them no way to find out until it arrived.

### Dependency injection

FastAPI's `Depends` is used as the composition root, with `Annotated` aliases
that keep signatures readable:

```python
DbSession    = Annotated[Session, Depends(get_db)]
CurrentUser  = Annotated[User,    Depends(get_current_user)]
CurrentAdmin = Annotated[User,    Depends(require_role(UserRole.ADMIN))]
```

`CurrentAdmin` reads as a sentence and is impossible to forget silently — an
endpoint without it simply has no admin argument. Authorisation being part of the
signature rather than a line inside the body means the OpenAPI schema documents
it automatically, and a reviewer can audit an entire router's access rules by
reading only the parameter lists.

Because the database session arrives through a dependency, tests swap it for one
bound to a transaction that is always rolled back — no mocking, no monkey
patching, just a different provider.

### Errors

Domain errors carry meaning; the API layer maps meaning to status codes:

| Domain exception | HTTP |
| --- | --- |
| `NotFoundError` | 404 |
| `ConflictError` (incl. `InvalidOrderTransition`) | 409 |
| `PermissionDeniedError` | 403 |
| `InvalidTokenError` | 401 + `WWW-Authenticate: Bearer` |

Services raise business exceptions and never import `HTTPException`. Services
remain reusable outside HTTP, the mapping is defined in exactly one place, and no
endpoint can accidentally report a conflict as a 500.

---

## Data model

```
users                     restaurants
  id                        id
  email          (unique)   name
  hashed_password           description
  full_name                 is_active
  role  (enum)                 │
  is_active                    │ 1:N (cascade)
     │                         ▼
     │                      menu_items
     │ 1:N                    id
     │                        restaurant_id
     ▼                        name
  orders  ◀───── N:1 ─────    price_cents  (CHECK >= 0)
     id                       is_available
     customer_id  (RESTRICT)
     restaurant_id (RESTRICT)
     status  (enum, indexed)
     total_cents  (CHECK >= 0)
        │
        │ 1:N (cascade)
        ▼
  order_items
     id
     order_id       (CASCADE)
     menu_item_id   (RESTRICT)
     item_name          ◀── snapshot
     unit_price_cents   ◀── snapshot
     quantity  (CHECK > 0)
```

### Price and name snapshotting

`order_items` stores `item_name` and `unit_price_cents` as **copies**, not as a
join to `menu_items`. This is the difference between a receipt and a query.

Read the price live, and raising the price of a pizza tomorrow retroactively
changes what every past customer paid. Renaming a dish rewrites history. Deleting
it leaves orders referring to nothing. With the snapshot, an order is an
immutable record of what was actually agreed, which is also what accounting,
disputes and refunds require.

A test proves it end-to-end: place an order, change the menu price to `9999`,
re-fetch the order, and it still reports its original value.

### Deletion semantics

Deletions are chosen per relationship rather than applied uniformly:

- `orders → order_items`: **CASCADE**. Lines have no meaning without their order.
- `restaurants → menu_items`: **CASCADE**. A menu belongs to its restaurant.
- `orders → users`, `orders → restaurants`, `order_items → menu_items`:
  **RESTRICT**. Financial records must not vanish because a row was tidied away.
  The correct way to retire a restaurant is `is_active = false`, which is why the
  flag exists.

### Constraints in the database

`price_cents >= 0`, `total_cents >= 0` and `quantity > 0` are `CHECK`
constraints, and email uniqueness is a `UNIQUE` index. The application validates
these too, but application validation protects against *this* application. The
database is the last line of defence, and it also holds against a migration
script, a psql session, or a future service written by someone else.

All constraints are named through a metadata naming convention, so Alembic can
always generate a reversible `DROP` — anonymous constraints are the classic
reason a downgrade fails.

---

## Security

**Passwords** are hashed with **Argon2id** (`argon2-cffi`), the winner of the
Password Hashing Competition and the current OWASP first choice. It is memory-
hard, which is what makes GPU cracking expensive in a way that iteration-count
schemes are not. `argon2-cffi` is used directly rather than through `passlib`,
which has been unmaintained for years and is a poor dependency to carry in the
one place where being out of date is least acceptable.

**Tokens** are JWTs signed with HS256 via PyJWT. The verifier **pins the
algorithm** rather than trusting the token's own header — the defence against the
`alg: none` and RS256-to-HS256 confusion attacks, where an attacker tells the
verifier how to verify. `exp`, `iat` and `sub` are all *required* to be present,
so a token missing an expiry is rejected rather than treated as eternal.

**Authentication failures are indistinguishable.** Unknown email, wrong password
and deactivated account produce one identical response. Anything else is an
account enumeration oracle: a signup form already reveals which addresses are
registered, and a login endpoint should not confirm it. When no user exists, the
service still hashes a dummy value, so the response time does not reveal the
answer either.

**Roles are never client-supplied.** `POST /auth/register` has no `role` field,
so privilege cannot be requested. The role in a token is issued by the server and
verified by signature, and every privileged route declares `CurrentAdmin`.

**The user row is loaded on every request** rather than trusted from token
claims. This costs one indexed primary-key lookup and means deactivating an
account takes effect immediately, instead of when the last issued token happens
to expire.

**Secrets stay out of logs.** `SECRET_KEY` and `POSTGRES_PASSWORD` are
`SecretStr`, so they render as `**********` in tracebacks and log lines. This is
enforced by a test — one that caught a real defect during development, where
`database_url` had been written as a Pydantic `computed_field` and was therefore
included in `model_dump()`, printing the password in clear text and defeating the
entire point of `SecretStr`. It is now a plain `@property`, and the test that
found it still runs.

**The proxy** disables `server_tokens`, caps request bodies at 1 MB, and sets
`X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy`.

**The container** runs as a non-root user with no shell.

---

## Testing

```bash
docker compose exec -T api pytest                          # everything
docker compose exec -T api pytest --cov=app --cov-report=term-missing
docker compose exec -T api pytest tests/api/test_orders.py -v
```

**130 tests**, all passing.

| Suite | Focus |
| --- | --- |
| `tests/domain/test_order_state.py` | The state machine, exhaustively — every pair of statuses. |
| `tests/core/test_security.py` | Hashing, token issue and verification, tampering, expiry, secret redaction. |
| `tests/models/test_schema_integrity.py` | Constraints, cascades, uniqueness — against the real database. |
| `tests/api/test_auth.py` | Registration, login, enumeration resistance, privilege escalation. |
| `tests/api/test_restaurants.py` | Catalogue reads and admin-only writes. |
| `tests/api/test_orders.py` | Placement, pricing, atomicity, ownership, lifecycle. |
| `tests/test_health.py` | Liveness and readiness, including a simulated database outage. |

Tests run against **real PostgreSQL**, in a separate `lateral_test` database
created automatically on first run, with its schema built **by running the
migrations**. SQLite would be faster and would test a different database than the
one in production: no `CHECK` enforcement by default, different enum handling,
different transactional DDL. Worse, building the schema with `create_all()`
tests the models while production runs the migrations — so the one artefact that
can break a deploy would be the one artefact never exercised. Here, a broken
migration fails the suite before it fails a deployment.

Each test runs inside a transaction that is **always rolled back**, using
SQLAlchemy's `join_transaction_mode="create_savepoint"`. Service code commits
normally — those commits become savepoint releases — and the outer transaction is
discarded at teardown. Tests are therefore fully isolated and order-independent,
without truncating tables between them.

The suite is written **test-first**. Every feature branch in this repository
contains a commit where the tests exist and fail, followed by the implementation
that makes them pass.

The tests document reasoning, not just behaviour. `test_the_total_is_computed_by_the_server`
and `test_nothing_is_persisted_when_one_line_is_invalid` explain in their
docstrings what would go wrong commercially if the property did not hold.

---

## Database migrations

The schema is **only ever** changed by a migration. `Base.metadata.create_all()`
is never called, in any environment, including tests.

`create_all()` produces a schema with no history, no ordering and no way back. It
also cannot express the things migrations exist for: backfilling a column,
renaming without data loss, deploying without downtime. Allowing it anywhere
creates two sources of truth that drift silently.

Migrations run **automatically on container start**, in the entrypoint before
Uvicorn is exec'd, so an unmigrated database is impossible.

```bash
# Generate after changing a model — then READ the result, always
docker compose exec -T api alembic revision --autogenerate -m "add delivery notes"

docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic downgrade -1
docker compose exec -T api alembic current
docker compose exec -T api alembic history --verbose

# Fail if models and migrations have drifted apart — CI-ready
docker compose exec -T api alembic check
```

Autogenerate is a first draft, not an authority. The initial migration in this
repository was hand-audited and corrected: `drop_table` does not drop PostgreSQL
`ENUM` types, so the generated downgrade left them behind and the following
upgrade failed with "type already exists". Explicit drops were added and the
full `upgrade → downgrade → upgrade` round trip was verified.

`alembic.ini` contains **no database URL**. The URL is resolved from `Settings`
at runtime, so credentials never enter a tracked file and migrations use exactly
the same connection details as the application.

---

## Development workflow

The source tree is bind-mounted into the API container, so edits take effect on
save — the production image bakes the code instead.

```bash
docker compose exec -T api ruff check .          # Lint
docker compose exec -T api ruff check --fix .    # Lint and fix
docker compose exec -T api ruff format .         # Format
docker compose exec -T api mypy app              # Type-check
docker compose exec -T api pytest                # Test
docker compose exec -T api alembic check         # Migration drift
```

All five must be clean before a branch is merged. Ruff enforces a 100-character
line length and a rule set covering pyflakes, pycodestyle, import sorting,
bugbear, comprehensions, simplification and modern-Python upgrades. mypy runs in
strict-adjacent mode: every function is annotated, and no `Any` leaks in from
untyped calls.

### Branching

Work happens on descriptive feature branches, one per unit of behaviour, merged
through pull requests with `--no-ff` so the shape of each feature survives in
history:

```
feature/integration-test-harness
feature/authentication-endpoints
feature/restaurant-menu-management
feature/order-placement-and-tracking
docs/comprehensive-project-readme
```

Commit messages explain **why** a change is correct, not what the diff already
shows. Pull request descriptions carry the architectural reasoning, so a reviewer
gets the argument before the code.

---

## Project layout

```
.
├── app/
│   ├── api/
│   │   ├── deps.py              # Composition root: session, current user, role guards
│   │   ├── errors.py            # Domain exception → HTTP status mapping
│   │   ├── router.py            # /api/v1 aggregation
│   │   └── routes/              # health, auth, restaurants, menu_items, orders
│   ├── core/
│   │   ├── config.py            # Settings; SecretStr; cached
│   │   └── security.py          # Argon2id hashing, JWT issue/verify
│   ├── db/
│   │   ├── base.py              # DeclarativeBase + constraint naming convention
│   │   └── session.py           # Engine, session factory, request-scoped session
│   ├── domain/                  # Pure rules. Imports nothing.
│   │   ├── errors.py            # NotFound / Conflict / PermissionDenied
│   │   ├── order_state.py       # The state machine
│   │   └── roles.py
│   ├── models/                  # SQLAlchemy mappings
│   ├── repositories/            # Query objects. No HTTP, no business rules.
│   ├── schemas/                 # Pydantic request/response contracts
│   ├── services/                # Use cases. The only layer that commits.
│   └── main.py                  # create_app()
├── docker/
│   ├── api/{Dockerfile,entrypoint.sh}
│   └── nginx/default.conf
├── migrations/                  # Alembic
├── tests/                       # conftest, factories, and the suites
├── docker-compose.yml
├── pyproject.toml               # Ruff, mypy, pytest configuration
├── requirements.txt             # Pinned, each dependency justified inline
└── .env.example                 # The environment contract
```

---

## Decisions worth explaining

**Synchronous SQLAlchemy, not async.** The workload is ordinary CRUD against one
database, where the constraint is the connection pool, not the event loop. Async
SQLAlchemy adds a genuine cost — a smaller ecosystem, harder debugging, and the
ever-present risk that one accidentally blocking call stalls the whole loop.
FastAPI runs synchronous endpoints in a thread pool, which handles this load
comfortably. If profiling ever shows otherwise, the repository layer is the only
thing that would change.

**No Gunicorn.** The brief suggests Uvicorn *or* Gunicorn. Modern Uvicorn removed
`uvicorn.workers.UvicornWorker`, so the once-standard pairing no longer exists as
written. `uvicorn --workers N` provides the same multi-process model with one
fewer dependency and one fewer layer of configuration.

**Integer cents, never floats.** Binary floating point cannot represent `10.50`
exactly. Money that drifts by fractions of a cent through arithmetic is the
oldest bug in commercial software. The unit lives in the column name so it cannot
be misread.

**404 rather than 403 for another customer's order.** A 403 confirms the order
exists, which turns sequential identifiers into a way to measure the platform's
order volume. To a customer, someone else's order and a nonexistent one are the
same thing.

**One list endpoint for both audiences.** `GET /orders` serves customers and
staff, with the service deciding scope. Two endpoints would duplicate pagination,
filtering and serialisation to express a difference of one predicate — and would
be two places to forget the customer filter.

**No Makefile.** The project standard forbids tabs, and `make` requires literal
tab-indented recipes. Rather than carve out an exception, the commands are
documented here.

**Suppressed documentation in production.** `/docs` and `/redoc` disappear when
`APP_ENV=production`. A complete map of every endpoint and payload shape is
useful to a developer and equally useful to an attacker; the OpenAPI schema is
better distributed to the people who should have it.

---

## Author

**Alghisi Alessandro Paolo**
<alexalghisi@gmail.com>
<https://github.com/alexalghisi>

Built as a technical challenge submission — FastAPI, PostgreSQL, SQLAlchemy,
Alembic, Docker and Nginx, developed test-first.
