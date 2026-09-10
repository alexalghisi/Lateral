"""Alembic runtime environment.

ARCHITECTURAL DECISION -- migrations are the only mechanism that changes schema.

`Base.metadata.create_all()` is never called, in any environment, including
tests. It is seductive because it is one line, but a schema built by
`create_all` and a schema built by migrations drift apart silently: the
migration chain stops being exercised, and the first time anyone runs it end to
end is against production data. Tests run against a migrated database for the
same reason -- the migration chain is production code and deserves the same
coverage as everything else.

This module also resolves the database URL from the application's `Settings`
rather than from alembic.ini, so there is one source of truth for connection
details and no password in a tracked file.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# IMPORTANT -- `app.models` is imported for its SIDE EFFECT, not for a name.
# Alembic diffs `Base.metadata` against the live database, and a model class is
# only registered on that metadata once its module has been imported. A model
# that is never imported here is invisible to autogenerate, which will happily
# emit an empty migration and give no warning at all.
#
# Importing the package suffices: app/models/__init__.py imports every model
# module, so there is one place to keep current rather than two.
import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base

config = context.config

# Inject the resolved URL. `set_main_option` escapes nothing, so any '%' in a
# generated password would be read as ConfigParser interpolation -- doubling it
# keeps such passwords working.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it ("offline" mode).

    This is what makes change-controlled deployments possible: a DBA can review
    the exact SQL before it touches a production database.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Emit each revision inside its own transaction block so a failure
        # part-way through a chain does not leave a half-applied schema.
        transaction_per_migration=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Execute migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool: this process runs a handful of statements and exits. Pooling
        # would hold connections open for no benefit and can keep a migration
        # container alive past its work.
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # compare_type: detect column type changes (VARCHAR(50) -> VARCHAR(100)).
            # Off by default in Alembic, which means autogenerate silently
            # ignores them and the migration looks complete when it is not.
            compare_type=True,
            # compare_server_default: likewise for changes to DEFAULT clauses.
            compare_server_default=True,
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
