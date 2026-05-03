"""
app/extensions.py — Shared Flask extensions.

Centralising extension instances here prevents circular imports when
models, services, and the app factory all need the same db/migrate.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# Initialised without an app — bound later via init_app() in create_app()
db = SQLAlchemy()
migrate = Migrate()
