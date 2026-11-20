from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from agenthub.db import database_url
from agenthub.models import Base

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

engine = create_engine(database_url())
with engine.connect() as conn:
    context.configure(connection=conn, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
