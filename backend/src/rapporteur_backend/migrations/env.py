"""Alembic получает готовое соединение от команды приложения."""

from alembic import context

from rapporteur_backend.tables import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Миграции запускаются командой rapporteur-backend migrate")
context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
