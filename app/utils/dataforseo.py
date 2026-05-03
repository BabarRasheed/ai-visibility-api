"""
app/utils/dataforseo.py — DataForSEO API client.

DataForSEO provides real search volume and competitive difficulty data
via a REST API using HTTP Basic Auth with login/password credentials.

Endpoints used:
  - Keywords Data / Google Ads / Search Volume (POST)
    https://docs.dataforseo.com/v3/keywords_data/google_ads/search_volume/live/

Fallback strategy:
    If credentials are not set, or the API returns an error, this client
    raises DataForSEOError. The caller (VisibilityScoringAgent) catches this
    and falls back to LLM-estimated values — the pipeline never crashes.

Usage example:
    client = DataForSEOClient(login="user@example.com", password="abc123")
    volume, difficulty = client.get_keyword_data("best SEO content tool")
"""

import base64
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# DataForSEO API base URL
_BASE_URL = "https://api.dataforseo.com/v3"

# Request timeout in seconds
_TIMEOUT = 15


class DataForSEOError(Exception):
    """Raised when DataForSEO is unavailable or returns an error."""
    pass


class DataForSEOClient:
    """
    Thin wrapper around the DataForSEO Keywords Data API.

    Args:
        login:    DataForSEO account email.
        password: DataForSEO account password.
    """

    def __init__(self, login: str = "", password: str = ""):
        self.login = login
        self.password = password
        self._configured = bool(login and password)

    @property
    def _auth_header(self) -> dict:
        """Build HTTP Basic Auth header."""
        credentials = f"{self.login}:{self.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    def get_keyword_data(self, keyword: str, location_code: int = 2840) -> tuple[Optional[int], Optional[int]]:
        """
        Fetch search volume and competition index for a keyword.

        Args:
            keyword:       The keyword/query to look up.
            location_code: DataForSEO location code (2840 = United States).

        Returns:
            Tuple of (search_volume, difficulty) where both may be None if
            the keyword has no data.

        Raises:
            DataForSEOError: If not configured or API call fails.
        """
        if not self._configured:
            raise DataForSEOError(
                "DataForSEO credentials not configured (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD)"
            )

        payload = [
            {
                "keywords": [keyword],
                "location_code": location_code,
                "language_code": "en",
            }
        ]

        url = f"{_BASE_URL}/keywords_data/google_ads/search_volume/live"

        try:
            response = requests.post(
                url,
                json=payload,
                headers={**self._auth_header, "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise DataForSEOError(f"HTTP request to DataForSEO failed: {exc}") from exc

        if response.status_code != 200:
            raise DataForSEOError(
                f"DataForSEO returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise DataForSEOError(f"DataForSEO returned non-JSON response") from exc

        return self._extract_keyword_metrics(data, keyword)

    def _extract_keyword_metrics(
        self, data: dict, keyword: str
    ) -> tuple[Optional[int], Optional[int]]:
        """
        Parse the DataForSEO response and extract volume + difficulty.

        DataForSEO response structure (simplified):
        {
          "tasks": [{
            "result": [{
              "keyword": "...",
              "search_volume": 1200,
              "competition_index": 62,
              "competition": "HIGH"
            }]
          }]
        }
        """
        try:
            tasks = data.get("tasks", [])
            if not tasks:
                logger.warning("[DataForSEO] No tasks in response for keyword='%s'", keyword)
                return None, None

            task = tasks[0]
            status_code = task.get("status_code", 0)
            if status_code != 20000:
                raise DataForSEOError(
                    f"DataForSEO task error {status_code}: {task.get('status_message', '')}"
                )

            results = task.get("result", [])
            if not results:
                logger.info("[DataForSEO] No keyword data found for '%s'", keyword)
                return None, None

            item = results[0]
            search_volume = item.get("search_volume")
            competition_index = item.get("competition_index")  # 0–100

            logger.debug(
                "[DataForSEO] keyword='%s' volume=%s difficulty=%s",
                keyword,
                search_volume,
                competition_index,
            )

            return (
                int(search_volume) if search_volume is not None else None,
                int(competition_index) if competition_index is not None else None,
            )

        except DataForSEOError:
            raise
        except Exception as exc:
            logger.error("[DataForSEO] Failed to parse response: %s", exc)
            return None, None
