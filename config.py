"""
config.py — Application configuration classes.

Uses environment variables via python-dotenv. Three configs:
  - DevelopmentConfig  (default)
  - TestingConfig      (used by pytest)
  - ProductionConfig   (for deployment)
"""

import os
from dotenv import load_dotenv

load_dotenv()


class BaseConfig:
    """Shared settings for all environments."""

    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # AI provider
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o")

    # DataForSEO credentials (optional)
    DATAFORSEO_LOGIN: str = os.environ.get("DATAFORSEO_LOGIN", "")
    DATAFORSEO_PASSWORD: str = os.environ.get("DATAFORSEO_PASSWORD", "")

    # Pipeline behaviour
    MAX_QUERIES_PER_RUN: int = int(os.environ.get("MAX_QUERIES_PER_RUN", 20))
    TOP_OPPORTUNITIES: int = int(os.environ.get("TOP_OPPORTUNITIES", 3))


class DevelopmentConfig(BaseConfig):
    DEBUG: bool = True
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL", "sqlite:///dev.db"
    )


class TestingConfig(BaseConfig):
    TESTING: bool = True
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    # Disable CSRF protection in tests
    WTF_CSRF_ENABLED: bool = False


class ProductionConfig(BaseConfig):
    DEBUG: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.environ.get("DATABASE_URL", "")

    @classmethod
    def validate(cls):
        """Raise on missing critical production settings."""
        if not cls.SQLALCHEMY_DATABASE_URI:
            raise ValueError("DATABASE_URL must be set in production")
        if not cls.SECRET_KEY or cls.SECRET_KEY == "dev-secret-key-change-me":
            raise ValueError("SECRET_KEY must be a strong secret in production")


_config_map = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(env: str | None = None):
    """Return the config class for the given environment name."""
    env = env or os.environ.get("FLASK_ENV", "development")
    return _config_map.get(env, DevelopmentConfig)
