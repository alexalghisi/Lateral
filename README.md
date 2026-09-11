# Lateral

A backend for a small takeaway platform. Customers browse restaurants and menus,
place orders, and track them through their lifecycle. Internal staff manage the
catalogue and drive orders from `pending` to `delivered`.

<p align="center">
  <img src="docs/assets/how-it-works.gif" width="960" alt="A customer browses Trattoria Lateral, orders two Margheritas, and tracks the order from pending to delivered. The matching API calls appear beside the phone." />
</p>

Built with FastAPI, PostgreSQL, SQLAlchemy and Alembic, packaged with Docker
Compose behind Nginx.

**Author:** Alghisi Alessandro Paolo — <alexalghisi@gmail.com>

---

## How it works

A customer's request hits **Nginx** (the only public entrypoint), which forwards
to **Uvicorn** running the **FastAPI** app. Each layer has one job:

| Layer | Responsibility |
| --- | --- |
| **Router** (`app/api/routes/`) | Read input, call a service, return the result. Auth is declared in the signature. |
| **Schema** (`app/schemas/`) | The request/response contract. Omitting price, total and identity makes abuse *unrepresentable*. |
| **Service** (`app/services/`) | Owns the use case and the transaction — the **only** layer that commits. |
| **Repository** (`app/repositories/`) | Owns the SQL. No HTTP, no business rules. |
| **Domain** (`app/domain/`) | Pure rules (state machine, roles, errors). Imports nothing, so it's testable in isolation. |
| **PostgreSQL** | Source of truth: `CHECK` constraints, unique index and `RESTRICT` keys hold even if every layer above is bypassed. |

Three ideas run through the design:

- **Client states intent, server decides consequence** — price, total and
  identity come from the menu and the token, not the request body.
- **One layer commits** — repositories `flush`, only services `commit`, once per
  use case, so order placement is all-or-nothing.
- **Rules are pure data** — the lifecycle is a transition table in an import-free
  domain, provable without a database or web server.

Two mechanisms a reviewer will look for:

**Authorisation is a type.** Role guards ride in the signature, so an endpoint
lacking one simply has no admin argument — and OpenAPI documents access for free:

```python
DbSession    = Annotated[Session, Depends(get_db)]
CurrentUser  = Annotated[User,    Depends(get_current_user)]
CurrentAdmin = Annotated[User,    Depends(require_role(UserRole.ADMIN))]
```

**Errors carry meaning; the edge maps it to a status.** Services raise domain
exceptions and never import `HTTPException`, so the mapping lives in one place:
`NotFoundError` → 404, `ConflictError` → 409, `PermissionDeniedError` → 403,
`InvalidTokenError` → 401.

---

## Quick start

Docker with the Compose plugin is the only requirement — no Python or PostgreSQL
on the host. On macOS, [Colima](https://github.com/abiosoft/colima) works as the
runtime (`brew install colima docker docker-compose && colima start`).

```bash
git clone https://github.com/alexalghisi/Lateral.git
cd Lateral

cp .env.example .env

# Generate a real signing key (the app refuses to start without one)
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"
# ...and paste the result over the SECRET_KEY line in .env

docker compose up --build -d
```

That one command builds the image, starts PostgreSQL, waits for it to report
healthy, **applies all migrations automatically**, starts Uvicorn and puts Nginx
in front of it.

```bash
curl http://localhost:8080/health/ready   # {"status":"ready"}
```

| URL | What it is |
| --- | --- |
| <http://localhost:8080> | Nginx — the public entrypoint, use this |
| <http://localhost:8080/docs> | Interactive Swagger UI (suppressed when `APP_ENV=production`) |
| <http://localhost:8080/redoc> | ReDoc reference |
| <http://localhost:8080/openapi.json> | Machine-readable OpenAPI schema |
| `localhost:5432` | PostgreSQL |

Everyday commands:

```bash
docker compose logs -f api        # Follow application logs
docker compose ps                 # Health of every service
docker compose exec -T api pytest # Run the test suite
docker compose down               # Stop, keeping data
docker compose down -v            # Stop and destroy the database volume
```

---

## Configuration

Every setting is read from the environment (twelve-factor). The same image runs
everywhere; only the injected environment differs. `.env.example` is the tracked
contract; `.env` is git-ignored.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `local` | Environment label. `production` disables `/docs` and `/redoc`. |
| `LOG_LEVEL` | `info` | Verbosity for the app and Uvicorn. |
| `SECRET_KEY` | *(none)* | Token signing key. **No default — the app refuses to start without it.** |
| `JWT_ALGORITHM` | `HS256` | Signing algorithm, pinned at verification time. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime. |
| `POSTGRES_USER` | `lateral` | Database user. |
| `POSTGRES_PASSWORD` | `lateral` | Database password. **Change outside local dev.** |
| `POSTGRES_DB` | `lateral` | Database name. |
| `POSTGRES_HOST` | `db` | Compose service name; use `localhost` on the host. |
| `POSTGRES_PORT` | `5432` | Database port. |
| `NGINX_HOST_PORT` | `8080` | Host port for the proxy. |
| `API_HOST_PORT` | `8000` | Host port for Uvicorn (debugging). |
| `POSTGRES_HOST_PORT` | `5432` | Host port for PostgreSQL. |

`SECRET_KEY` has no default on purpose: a default would ship to production, where
anyone who read the repo could forge tokens. Failing to boot is correct.

---

## Using the API

A full session, from empty database to a delivered order. `BASE=http://localhost:8080/api/v1`.

```bash
# 1. Register (always a customer) and log in (form-encoded, per OAuth2)
curl -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"a-sufficiently-long-password","full_name":"Ada Lovelace"}'

TOKEN=$(curl -s -X POST $BASE/auth/login \
  -d "username=ada@example.com&password=a-sufficiently-long-password" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Promote an admin (operator action — no self-service route by design)
docker compose exec -T db psql -U lateral -d lateral \
  -c "UPDATE users SET role='admin' WHERE email='ada@example.com';"
# ...then re-issue the token, since the role is embedded in it.

# 3. Create a restaurant and a menu item (admin). Prices are integers in cents.
curl -X POST $BASE/restaurants -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Trattoria Lateral","description":"Napoli style"}'
curl -X POST $BASE/restaurants/1/menu -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Margherita","description":"San Marzano, fior di latte","price_cents":1050}'

# 4. Browse (public)
curl "$BASE/restaurants?limit=20&offset=0"
curl $BASE/restaurants/1/menu

# 5. Place an order — no price, no total, no customer id in the request
curl -X POST $BASE/orders -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"restaurant_id":1,"items":[{"menu_item_id":1,"quantity":2}]}'

# 6. Track it (a customer sees only their own; admins see all)
curl $BASE/orders/1 -H "Authorization: Bearer $TOKEN"

# 7. Advance it (admin). Skipping a step returns 409 with the allowed transitions.
curl -X PATCH $BASE/orders/1/status -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"accepted"}'
```

The order response carries the server-computed `total_cents` and the snapshotted
item names — the basket says *what* to buy; the server decides what it costs and
who is buying.

---

## API reference

All endpoints are under `/api/v1`. Authentication is `Authorization: Bearer <token>`.

| Method | Path | Access | Description |
| --- | --- | --- | --- |
| `POST` | `/auth/register` | Public | Create a customer account. Returns 201. |
| `POST` | `/auth/login` | Public | Exchange credentials for a token (form-encoded). |
| `GET` | `/auth/me` | Authenticated | The current user's profile. |
| `GET` | `/restaurants` | Public | Paginated list of active restaurants. |
| `GET` | `/restaurants/{id}` | Public | A single restaurant. |
| `GET` | `/restaurants/{id}/menu` | Public | Available items. Staff may pass `?include_unavailable=true`. |
| `POST` | `/restaurants` | **Admin** | Create a restaurant. |
| `PATCH` | `/restaurants/{id}` | **Admin** | Partial update, including deactivation. |
| `POST` | `/restaurants/{id}/menu` | **Admin** | Add a menu item. |
| `PATCH` | `/menu-items/{id}` | **Admin** | Partial update, including availability. |
| `DELETE` | `/menu-items/{id}` | **Admin** | Remove a menu item. Returns 204. |
| `POST` | `/orders` | Authenticated | Place an order. Returns 201. |
| `GET` | `/orders` | Authenticated | Own orders; **all** for admins. Supports `?status=`, `?limit=`, `?offset=`. |
| `GET` | `/orders/{id}` | Owner or admin | Track a single order. |
| `PATCH` | `/orders/{id}/status` | **Admin** | Advance the lifecycle. |
| `GET` | `/health/live` | Public | Liveness — answers "is the process up". |
| `GET` | `/health/ready` | Public | Readiness — runs `SELECT 1`; 503 if the DB is unreachable. |

Health probes sit at the root, outside `/api/v1`: an orchestrator is not an API
consumer, so its probe URL should never change when the API reaches v2.

**Status codes:** `401` bad/missing token · `403` authenticated but wrong role ·
`404` doesn't exist *or* is none of your business · `409` conflicts with current
state · `422` malformed per the schema. (`422` could never be valid; `409` would
have succeeded at a different moment.)

---

## The order lifecycle

```
        ┌─────────┐      ┌──────────┐      ┌──────────────────┐      ┌───────────┐
        │ pending │─────▶│ accepted │─────▶│ out_for_delivery │─────▶│ delivered │
        └────┬────┘      └────┬─────┘      └──────────────────┘      └───────────┘
             │                │                                        (terminal)
             └────────┬───────┘
                      ▼
                ┌───────────┐
                │ cancelled │  (terminal)
                └───────────┘
```

The lifecycle lives in `app/domain/order_state.py` as a **declarative transition
table**, not scattered `if` statements. Adding a status means adding a row:

```python
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING:          frozenset({OrderStatus.ACCEPTED, OrderStatus.CANCELLED}),
    OrderStatus.ACCEPTED:         frozenset({OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED}),
    OrderStatus.OUT_FOR_DELIVERY: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED:        frozenset(),
    OrderStatus.CANCELLED:        frozenset(),
}
```

Four properties are enforced and tested: **no skipping** (`pending → delivered`
is rejected), **no going backwards**, **terminal states are final**, and **no
self-transitions** (re-setting a status is a `409`, which surfaces a lost update
rather than hiding it). `cancelled` is possible before dispatch only; afterwards
the resolution is a refund, not a status change.

Transitions take a `SELECT ... FOR UPDATE` row lock, so two staff acting at once
can't both read the old status and both write — one update silently lost.

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

**Price and name are snapshotted** onto `order_items` as copies, not a live join
— the difference between a receipt and a query. Raising a price tomorrow must not
retroactively change what past customers paid. A test proves it: place an order,
change the menu price, re-fetch — the order still reports its original value.

**Deletions are chosen per relationship.** `orders → order_items` and
`restaurants → menu_items` **CASCADE** (parts have no meaning without their
parent). Financial links (`orders → users/restaurants`, `order_items →
menu_items`) **RESTRICT** — retire a restaurant with `is_active = false`, not a
delete. `CHECK` constraints and the unique email index are the last line of
defence, holding against psql sessions and future code, not just this app.

---

## Security

- **Passwords** hashed with **Argon2id** (`argon2-cffi` directly, not the
  unmaintained `passlib`) — memory-hard, the current OWASP first choice.
- **Tokens** are HS256 JWTs (PyJWT). The verifier **pins the algorithm** rather
  than trusting the token header (defence against `alg: none` and RS256→HS256
  confusion). `exp`, `iat` and `sub` are all required.
- **Auth failures are indistinguishable.** Unknown email, wrong password and
  deactivated account return one identical response; a dummy hash keeps timing
  constant — no account-enumeration oracle.
- **Roles are never client-supplied.** `register` has no `role` field; the role
  is server-issued, signed, and verified on every privileged route.
- **The user row is loaded on every request**, so deactivation takes effect
  immediately instead of when the last token expires.
- **Secrets stay out of logs** via `SecretStr` (enforced by a test that caught a
  real leak when `database_url` was a `computed_field`).
- **The proxy** disables `server_tokens`, caps bodies at 1 MB, and sets
  `X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy`. The
  **container** runs as a non-root user with no shell.

---

## Testing

```bash
docker compose exec -T api pytest                          # everything (130 tests)
docker compose exec -T api pytest --cov=app --cov-report=term-missing
docker compose exec -T api pytest tests/api/test_orders.py -v
```

Tests run against **real PostgreSQL** (a separate `lateral_test` database, built
**by running the migrations** — so a broken migration fails the suite before it
fails a deploy). Each test runs inside a transaction that is always rolled back
(`join_transaction_mode="create_savepoint"`), so tests are isolated and
order-independent without truncating tables.

The suite is written **test-first**, and docstrings explain the commercial
reasoning (e.g. `test_the_total_is_computed_by_the_server`,
`test_nothing_is_persisted_when_one_line_is_invalid`).

| Suite | Focus |
| --- | --- |
| `tests/domain/test_order_state.py` | The state machine, every pair of statuses. |
| `tests/core/test_security.py` | Hashing, token issue/verify, tampering, expiry, redaction. |
| `tests/models/test_schema_integrity.py` | Constraints, cascades, uniqueness against the real DB. |
| `tests/api/test_auth.py` | Registration, login, enumeration resistance, privilege escalation. |
| `tests/api/test_restaurants.py` | Catalogue reads and admin-only writes. |
| `tests/api/test_orders.py` | Placement, pricing, atomicity, ownership, lifecycle. |
| `tests/test_health.py` | Liveness and readiness, incl. a simulated DB outage. |

---

## Database migrations

The schema is **only ever** changed by a migration; `create_all()` is never
called, in any environment. Migrations run **automatically on container start**,
before Uvicorn, so an unmigrated database is impossible.

```bash
docker compose exec -T api alembic revision --autogenerate -m "add delivery notes"  # then READ it
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic downgrade -1
docker compose exec -T api alembic check      # fail if models and migrations drifted — CI-ready
```

Autogenerate is a first draft: the initial migration was hand-audited because
`drop_table` leaves PostgreSQL `ENUM` types behind, breaking the next upgrade.
`alembic.ini` holds **no database URL** — it's resolved from `Settings` at
runtime, so credentials never enter a tracked file.

---

## Development workflow

The source tree is bind-mounted into the API container, so edits take effect on
save. All five checks must be clean before a branch merges:

```bash
docker compose exec -T api ruff check .    # Lint (100-char lines, broad rule set)
docker compose exec -T api ruff format .   # Format
docker compose exec -T api mypy app        # Type-check (strict-adjacent, no stray Any)
docker compose exec -T api pytest          # Test
docker compose exec -T api alembic check   # Migration drift
```

Work happens on feature branches merged via PRs with `--no-ff`. Commit messages
explain **why** a change is correct; PR descriptions carry the architectural
argument.

---

## Project layout

```
app/
  api/          # deps (session, current user, role guards), errors (domain→HTTP),
                # router, routes/ (health, auth, restaurants, menu_items, orders)
  core/         # config (Settings, SecretStr), security (Argon2id, JWT)
  db/           # DeclarativeBase + naming convention, engine/session
  domain/       # Pure rules: errors, order_state, roles. Imports nothing.
  models/       # SQLAlchemy mappings
  repositories/ # Query objects. No HTTP, no business rules.
  schemas/      # Pydantic request/response contracts
  services/     # Use cases. The only layer that commits.
  main.py       # create_app()
docker/         # api/{Dockerfile,entrypoint.sh}, nginx/default.conf
migrations/     # Alembic
tests/          # conftest, factories, and the suites
docker-compose.yml · pyproject.toml · requirements.txt · .env.example
```

---

## Decisions worth explaining

- **Synchronous SQLAlchemy, not async** — the workload is ordinary CRUD bounded
  by the connection pool, not the event loop. FastAPI runs sync endpoints in a
  thread pool; only the repository layer would change if profiling disagreed.
- **Integer cents, never floats** — binary floats can't represent `10.50`; the
  unit lives in the column name so it can't be misread.
- **404 rather than 403 for another customer's order** — a 403 would confirm the
  order exists, turning sequential IDs into a way to measure order volume.

---

## Author

**Alghisi Alessandro Paolo** — <alexalghisi@gmail.com> ·
<https://github.com/alexalghisi>

Built as a technical challenge submission — FastAPI, PostgreSQL, SQLAlchemy,
Alembic, Docker and Nginx, developed test-first.
