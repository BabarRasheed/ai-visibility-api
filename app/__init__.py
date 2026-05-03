"""
app/__init__.py — Application factory.

create_app() is the single entry point for constructing the Flask app.
It wires together configuration, extensions, blueprints, and error handlers.
"""

import logging
import os
from flask import Flask, jsonify

from .extensions import db, migrate
from config import get_config


def create_app(config_override=None):
    """
    Application factory.

    Args:
        config_override: Optional config class or object. If None, the
                         FLASK_ENV environment variable selects the config.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #
    cfg = config_override or get_config()
    app.config.from_object(cfg)

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    _configure_logging(app)

    # ------------------------------------------------------------------ #
    # Extensions
    # ------------------------------------------------------------------ #
    db.init_app(app)
    migrate.init_app(app, db)

    # ------------------------------------------------------------------ #
    # Import models so Flask-Migrate can detect them
    # ------------------------------------------------------------------ #
    from app.models import profile, pipeline, query, recommendation  # noqa: F401

    # ------------------------------------------------------------------ #
    # Blueprints
    # ------------------------------------------------------------------ #
    from app.api.profiles import profiles_bp
    from app.api.queries import queries_bp

    app.register_blueprint(profiles_bp, url_prefix="/api/v1")
    app.register_blueprint(queries_bp, url_prefix="/api/v1")

    # ------------------------------------------------------------------ #
    # Global error handlers
    # ------------------------------------------------------------------ #
    _register_error_handlers(app)

    app.logger.info(
        "AI Visibility API started",
        extra={"env": os.environ.get("FLASK_ENV", "development")},
    )
    return app


def _configure_logging(app: Flask):
    """Set up structured logging with a consistent format."""
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    app.logger.setLevel(level)


def _register_error_handlers(app: Flask):
    """Register JSON error responses for common HTTP errors."""

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad Request", "message": str(e)}), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not Found", "message": str(e)}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method Not Allowed", "message": str(e)}), 405

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred"}), 500
