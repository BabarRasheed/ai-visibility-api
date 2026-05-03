# AI Visibility Intelligence API

A production-quality Flask REST API with a multi-agent AI pipeline for discovering and scoring AI visibility opportunities for businesses.

---

## 🚀 Quick Start (< 5 minutes)

### Option A — Docker Compose (Recommended)

```bash
git clone <repo-url>
cd ai_visibility_api

# 1. Copy env file and add your OpenAI key
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

# 2. Build and run
docker-compose up --build

# 3. Verify it's running
curl http://localhost:5000/api/v1/profiles
```

### Option B — Local Python Setup

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set OPENAI_API_KEY at minimum

# 4. Initialise the database
flask db init
flask db migrate -m "initial schema"
flask db upgrade

# 5. Run the API
flask run
# → http://localhost:5000
```

---

## ⚙️ Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | ✅ Yes | Your OpenAI API key |
| `OPENAI_MODEL` | No | Model to use (default: `gpt-4o`) |
| `DATABASE_URL` | No | SQLAlchemy DB URL (default: `sqlite:///dev.db`) |
| `SECRET_KEY` | No | Flask secret key |
| `DATAFORSEO_LOGIN` | No | DataForSEO account email (falls back to LLM estimates if absent) |
| `DATAFORSEO_PASSWORD` | No | DataForSEO account password |

---

## 🌐 API Endpoints

### Register a Profile
```
POST /api/v1/profiles
Content-Type: application/json

{
  "name": "Frase",
  "domain": "frase.io",
  "industry": "SEO Content Tools",
  "description": "AI-powered content brief tool",
  "competitors": ["surferseo.com", "marketmuse.com", "clearscope.io"]
}

→ 201 Created
{
  "profile_uuid": "abc-123",
  "name": "Frase",
  "domain": "frase.io",
  "status": "created",
  "created_at": "2025-01-15T10:00:00+00:00"
}
```

### Trigger the Pipeline
```
POST /api/v1/profiles/{profile_uuid}/run

→ 200 OK
{
  "run_uuid": "...",
  "status": "completed",
  "queries_discovered": 15,
  "queries_scored": 15,
  "tokens_used": 4821,
  "top_opportunities": [...],
  "recommendations": [...]
}
```

### Other Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/profiles/{id}` | Get profile + stats |
| `GET` | `/api/v1/profiles/{id}/queries` | List queries (filterable) |
| `GET` | `/api/v1/profiles/{id}/recommendations` | List recommendations |
| `POST` | `/api/v1/queries/{id}/recheck` | Re-score a single query |

### Query Filtering
```
GET /api/v1/profiles/{id}/queries?min_score=0.5&status=not_visible&page=1&per_page=20
```

---

## 🏗️ Architecture

### App Factory Pattern

```
run.py
  └─► create_app()          ← app/__init__.py
        ├─► config.py       ← env-based config classes
        ├─► extensions.py   ← db, migrate (no circular imports)
        ├─► models/         ← SQLAlchemy models
        ├─► api/            ← Flask Blueprints
        │     ├─ profiles.py
        │     └─ queries.py
        ├─► agents/         ← 3 independent AI agents
        │     ├─ base.py        (shared LLM client + JSON parsing)
        │     ├─ discovery.py   (Agent 1)
        │     ├─ scoring.py     (Agent 2)
        │     └─ recommendation.py (Agent 3)
        ├─► services/
        │     └─ pipeline.py   ← orchestrator
        └─► utils/
              ├─ scoring.py    ← opportunity score formula
              └─ dataforseo.py ← DataForSEO API client
```

### Multi-Agent Pipeline

```
POST /profiles/{id}/run
        │
        ▼
PipelineOrchestrator
        │
        ├─► Agent 1: QueryDiscoveryAgent
        │     Input:  business profile
        │     Output: 10–20 natural-language queries
        │     Model:  GPT-4o @ temperature=0.7 (diversity)
        │
        ├─► Agent 2: VisibilityScoringAgent (per query, failures isolated)
        │     Input:  query + domain
        │     Output: volume, difficulty, visibility, opportunity_score
        │     Data:   DataForSEO (real) + GPT-4o (visibility check)
        │     Model:  GPT-4o @ temperature=0.0 (deterministic)
        │
        └─► Agent 3: ContentRecommendationAgent
              Input:  top invisible queries
              Output: 3–5 content recommendations
              Model:  GPT-4o @ temperature=0.5 (balanced)
```

---

## 🤖 Agent Design Rationale

### Why GPT-4o for all agents?
- **JSON mode** (`response_format={"type": "json_object"}`) guarantees parseable output
- **Low failure rate** on structured output vs. smaller models
- **Temperature tuned per agent**:
  - Agent 1: 0.7 → creative, diverse queries
  - Agent 2: 0.0 → deterministic yes/no visibility decision
  - Agent 3: 0.5 → unique but coherent content titles

### Prompt Engineering Strategy

Each agent has:
1. **System prompt** with exact JSON schema defined inline
2. **Few-shot examples** (Agent 1) to anchor output style
3. **Variable user prompt** filled at runtime
4. **JSON extraction with fallback**: `json.loads → regex extraction → retry`

The schema is defined IN the prompt, not assumed. This is critical — vague prompts produce inconsistent JSON that crashes pipelines.

### Failure Isolation

Agent 2 processes each query independently in a try/except loop. If one query's LLM call fails (rate limit, timeout, malformed JSON after retry), we log the error and continue with the remaining queries. The pipeline only hard-fails if Agent 1 returns zero queries.

---

## 📊 Opportunity Score Formula

```
opportunity_score = 0.35 × norm_volume + 0.30 × ease + 0.35 × visibility_gap

Where:
  norm_volume    = min(search_volume, 10000) / 10000
  ease           = 1 - (difficulty / 100)
  visibility_gap = 1.0 if NOT visible, 0.0 if visible
```

### Weight Rationale

| Component | Weight | Reasoning |
|-----------|--------|-----------|
| `norm_volume` | 0.35 | High-volume queries have large upside; but volume alone doesn't mean opportunity |
| `ease` | 0.30 | Feasibility check — a high-volume invisible query with difficulty=95 may not be worth it |
| `visibility_gap` | 0.35 | Primary driver — if you're already there, there's no gap to close |

### Score Interpretation

| Score | Meaning |
|-------|---------|
| ≥ 0.80 | 🔥 Excellent — prioritise immediately |
| 0.60–0.79 | ✅ Good opportunity |
| 0.40–0.59 | ⚠️ Moderate — consider if resources allow |
| < 0.40 | 🔵 Low priority |

---

## 🗄️ Database Schema

### BusinessProfile
| Field | Type | Notes |
|-------|------|-------|
| uuid | String PK | UUID v4 |
| name | String | Business name |
| domain | String UNIQUE | Target domain |
| industry | String | Industry category |
| description | Text | Optional |
| competitors | JSON | Array of domains |
| status | String | created / running / completed / failed |

### PipelineRun
Tracks each execution. Multiple runs per profile allowed (historical tracking).

### DiscoveredQuery
One record per query. Scoring fields are nullable — filled in by Agent 2.

### ContentRecommendation
FK to both profile and query. `target_keywords` stored as JSON array.

**Schema decision**: We link recommendations to both profile AND query so we can:
- Show all profile-level recommendations efficiently
- Trace each recommendation back to its source query

---

## 🧪 Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run only agent tests
pytest tests/test_agents.py -v

# Run only API tests
pytest tests/test_api.py -v

# With coverage
pytest tests/ --cov=app --cov-report=term-missing
```

Tests use:
- `unittest.mock.patch` for all LLM calls (no API key needed)
- In-memory SQLite for DB tests
- No external network calls

---

## ⚖️ Tradeoffs & Decisions

### Synchronous Pipeline
The pipeline runs synchronously (may take 15–60s). **Tradeoff**: simpler to implement and debug; a production system would use Celery + Redis with a status polling endpoint. The assessment explicitly states sync is acceptable.

### SQLite Default
SQLite is used by default for zero-config setup. The `DATABASE_URL` env var accepts any SQLAlchemy-compatible URL (PostgreSQL recommended for production).

### DataForSEO Fallback
If DataForSEO credentials aren't configured, search volume returns 0 and difficulty falls back to the LLM's estimate. This means opportunity scores are approximate but the pipeline never crashes. **Tradeoff**: less accurate scoring without real data.

### UUID as String
UUIDs are stored as VARCHAR(36) strings for SQLite compatibility. PostgreSQL supports a native UUID type, but using strings works on both DBs without migration changes.

---

## 🛠️ AI Tools Used

This project was built using:
- **Claude (Anthropic)** — architecture planning, prompt engineering, code review
- **Copilot** — boilerplate generation

The architectural decisions, prompt design, schema choices, and scoring formula were authored and reasoned through independently.

---

## 📦 Dependencies

| Package | Purpose |
|---------|---------|
| Flask | Web framework |
| Flask-SQLAlchemy | ORM |
| Flask-Migrate | DB migrations |
| openai | GPT-4o API client |
| requests | DataForSEO HTTP client |
| python-dotenv | Env var management |
| gunicorn | Production WSGI server |
| pytest + pytest-flask | Testing |
| pydantic | Data validation |
