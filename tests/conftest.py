"""
tests/conftest.py — Pytest fixtures for the test suite.

Provides:
    app       — Flask test application (SQLite in-memory)
    client    — Flask test client
    db        — Database with tables created for each test
"""

import pytest
from app import create_app
from app.extensions import db as _db
from config import TestingConfig


@pytest.fixture(scope="session")
def app():
    """Create and configure a Flask app instance for the test session."""
    application = create_app(config_override=TestingConfig)
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope="function")
def client(app):
    """Flask test client — fresh for each test."""
    return app.test_client()


@pytest.fixture(scope="function")
def db(app):
    """
    Provide a clean database for each test.
    Tables are truncated between tests rather than dropped/recreated
    to keep tests fast.
    """
    with app.app_context():
        yield _db
        # Rollback any uncommitted transactions and clear data
        _db.session.rollback()
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture
def sample_profile_data():
    """Reusable valid profile request body."""
    return {
        "name": "Frase",
        "domain": "frase.io",
        "industry": "SEO Content Tools",
        "description": "AI-powered content briefs and SEO research",
        "competitors": ["surferseo.com", "marketmuse.com", "clearscope.io"],
    }
