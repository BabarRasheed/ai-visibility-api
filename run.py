"""
run.py — Application entry point.

Usage:
    flask run          (development)
    python run.py      (direct)
    gunicorn run:app   (production)
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=app.config.get("DEBUG", False))
