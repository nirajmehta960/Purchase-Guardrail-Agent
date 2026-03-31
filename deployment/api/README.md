# SavVio Phase 3: Backend API & Inference Layer

This directory contains the production-ready FastAPI backend for SavVio. It acts as the thin orchestration layer that chains together the deterministic rules engine, the ML model predictions, and the LLM response mechanisms.

By the time the request finishes, the user gets an authoritative Green/Yellow/Red recommendation, along with a cohesive explanation of *why* they were recommended that option.

---

## 1. Prerequisites

You must use the `model_pipeline` Python virtual environment for all API dependencies. It shares the same database schemas, configurations, and core features. 

Ensure you have your environment set to DEV in `.env` (it defaults to `dev` if absent) so you hit the right database instance. You also need local mock/model dependencies.

If you don't already have the dependencies installed:
```bash
# From project root
source model_pipeline/.venv/bin/activate
pip install -r model_pipeline/model-requirements.txt
pip install -r deployment/requirements.txt
```

---

## 2. API Architecture

The FastAPI application follows a clean request flow:

1. **`main.py`** intercepts `/predict` and validates inputs (Pydantic models in `schemas.py`).
2. **`inference.py`** is the orchestrator: 
   - Uses pgvector (`sentence-transformers`) to resolve the user's natural language to a Database `product_id`.
   - Fetches the user's financial profile from the database (`user_id`).
   - Runs **Layer 1** (Deterministic Affordability Engine).
   - Runs **Layer 2** (Downgrade Engine based on Product/Review ML Features).
   - Generates confidence scores via XGBoost (`ModelManager`).
   - Requests a response generation block from the LLM based on specific inputs.
   - Triggers LLM Guardrails to ensure output safety.
3. **`model_loader.py`** manages singletons (the Database Engine, the XGBoost MLflow artifact, the Label Encoder, and Precomputed category statistics) to stay loaded in memory for fast performance.

---

## 3. Running the API Locally

Run the Uvicorn webserver local process.

```bash
# Start from the project root!
uvicorn deployment.api.main:app --host 0.0.0.0 --port 8081 --reload
```

The server initializes everything and will typically output:
```log
[INFO] Loading model manager resources...
[INFO] Loaded model via mlflow.pyfunc from: .../model_pipeline/models/artifacts
[INFO] Database connected (env=dev)
[INFO] LLM provider initialized: mock
[INFO] Model manager fully loaded.
```

If it fails to connect to the DB or load the model, it will still start but operate in a gracefully degraded state.

---

## 4. Testing Endpoints

Once the Uvicorn server is running locally on port `8081`, you can test the three primary REST endpoints from another terminal.

*(Note: We use `U01157` as a sample User ID for local tests. You can query your local `financial_profiles` table for others).*

### Check API Health
Validates that connections to the Model, DB, and LLM are fully loaded and operational.
```bash
curl -s http://localhost:8081/health
```

### Direct Product Evaluation
If you already know the specific `product_id`, skip Natural Language parsing and evaluate directly:
```bash
curl -s "http://localhost:8081/user/U01157/evaluate?product_id=B07NPZ8YB1" 
```

### Natural Language Search & Evaluation
Pass raw natural language inputs. The engine will parse the intent, find the nearest `product_id` in pgvector, and evaluate:
```bash
curl -s -X POST http://localhost:8081/predict \
  -H "Content-Type: application/json" \
  -d '{
      "user_query": "Can I sensibly afford to buy a gas cooktop today?", 
      "user_id": "U01157"
  }'
```

### Example Output Response
```json
{
    "recommendation": "YELLOW",
    "confidence": 0.85,
    "explanation": "I'd suggest thinking carefully before buying 22″x20″ Built in Gas Cooktop 4 Burners Stainless Steel Stove NG/L-P-G Gas Hob Cooktop Kitchen Built-In Cook Stove Easy to Clean (22″x20″) at $167.49. Your emergency fund could use some building up before taking on additional expenses. While you can technically afford this, it would be worth considering whether this is a need or a want right now.",
    "product_name": "22″x20″ Built in Gas Cooktop 4 Burners Stainless Steel...",
    "product_price": 167.49,
    "triggered_rules": [
        "yellow:low_resilience"
    ],
    "was_downgraded": false,
    "guardrail_passed": true
}
```

---

## 5. Running the Unit Tests

We have complete test coverage over the core deterministic engine (`test_decision_logic.py`), the request orchestration logic (`test_inference.py`), and the FastAPI definitions (`test_api.py`).

**Run the suite (38 total tests ensures total coverage across rule logic & configurations):**

```bash
# Run tests from the project root
python -m pytest deployment/tests/ -v --tb=short
```

---

## Next Steps for the Team
Now that Phase 3 has verified the orchestration correctly combines deterministic outputs with our generative templates, the next step (Phase 4) is locking down the deployment in a Docker Container, deploying to Google Cloud Run, and pointing it directly to the Cloud SQL artifacts.
