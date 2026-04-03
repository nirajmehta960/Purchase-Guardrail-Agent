# Plan 2: Deployment API — Simplify to Single Artifact

## Context
After Plan 1, the MLflow artifact is self-contained: `model.predict(raw_df)` returns `{"label": "GREEN", "confidence": 0.87}`. The API no longer needs to load separate artifacts, run preprocessing, assemble feature rows, or decode labels.

## Depends on
Plan 1 (model pipeline wrapper) being implemented and a model retrained with the new wrapper.

---

## Step 1: Simplify `model_loader.py`

**File:** `deployment/api/model_loader.py`

Remove:
- `_load_label_encoder()` — encoder is inside the model
- `_load_feature_pipeline()` — pipeline is inside the model
- `_compute_category_stats()` — product features computed before being passed in
- `_predict_proba_for_pyfunc()` — wrapper returns confidence directly
- `predict()` method — wrapper does this
- `label_encoder`, `feature_pipeline`, `category_stats`, `max_rating_number` properties

Keep:
- `_load_model()` — loads the single pyfunc artifact
- `_connect_db()` — still need DB for user profiles, product lookup, reviews
- `_init_llm_provider()` — still need LLM for intent parsing + explanations
- `check_db_connection()`, `get_llm_provider_name()` — health checks

`ModelManager` becomes:
```python
class ModelManager:
    def __init__(self):
        self.model = None          # mlflow pyfunc (self-contained)
        self.db_engine = None      # SQLAlchemy engine
        self.llm_provider = None   # LLM for intent/explanation
        self._loaded = False

    def load(self):
        self._load_model(APIConfig.MODEL_ARTIFACT_DIR)
        self._connect_db(APIConfig.DB_ENV)
        self._init_llm_provider()
        self._loaded = True

    def predict(self, raw_features_df):
        """Returns (label, confidence) from the wrapped model."""
        result = self.model.predict(raw_features_df)
        return result["label"].iloc[0], float(result["confidence"].iloc[0])
```

Remove from `config.py`:
- `LABEL_ENCODER_PATH`
- `FEATURE_PIPELINE_PATH`

---

## Step 2: Simplify `inference.py`

**File:** `deployment/api/inference.py`

Remove:
- `_build_ml_feature_row()` — no longer manually assembling 40 fields
- `_compute_financial_features()` — the raw DB values + product price go directly to the model
- `_load_product_data()` — product/review data still needed but simplified
- `FinancialResult` dataclass — no longer needed for pipeline orchestration
- `_FEATURE_DEFAULTS` dict

The `_score_ml_model()` function becomes simple:
```python
def _score_ml_model(user_profile, product_price, product_data, manager):
    raw_row = {
        # User fields directly from DB profile
        **{k: user_profile.get(k, 0) for k in USER_FIELDS},
        # Product fields from DB row (or 0 if hypothetical)
        "product_price": product_price,
        **_product_fields(product_data),
        # Review fields from DB (or 0 if no reviews)
        **_review_fields(product_data),
    }
    result_df = manager.predict(pd.DataFrame([raw_row]))
    return result_df  # label + confidence
```

No affordability computation, no feature pipeline transform, no label decoding — the model handles all of it.

**Note:** The model still expects affordability features (affordability_score, price_to_income_ratio, etc.) as input columns. Two options:
- **(A)** Compute them in the wrapper's `predict()` method (model_pipeline change)
- **(B)** Keep `compute_affordability()` call in inference.py and pass the results as raw input columns

**Recommendation:** Option B for now — affordability depends on (user_profile + product_price) which is request-specific. The wrapper can't compute it without those inputs. Keep the call in inference.py, pass the 6 affordability values as columns in the raw DataFrame.

Same applies to product/review computed features — the wrapper expects them pre-computed. Keep `compute_product_features()` and `compute_review_features()` calls in inference, pass results as input columns. These are shared library calls (not re-implementations).

**Revised simplification:** What actually gets removed is:
- `_build_ml_feature_row()` — still needed to assemble the dict, but simpler (no None guards, no type coercion for MLflow)
- Feature pipeline `.transform()` call — wrapper does this
- Label encoder `.inverse_transform()` — wrapper does this
- `_predict_proba_for_pyfunc()` — wrapper returns confidence
- MLflow schema type fixups (credit_score int64 cast, downgraded int64 cast) — wrapper handles preprocessing

---

## Step 3: Simplify `schemas.py`

**File:** `deployment/api/schemas.py`

- Remove `ml_unavailable_reason` options related to `no_pipeline` — single artifact means model is either loaded or not
- Keep `FinancialFeaturesView` — still useful for the technical debug panel

---

## Step 4: Update health endpoint

**File:** `deployment/api/main.py`

Simplify health check — just `model is not None`, DB connected, LLM ready. No separate pipeline/encoder checks.

---

## Step 5: Strict startup (from your hardening plan)

**File:** `deployment/api/config.py` + `main.py` + `model_loader.py`

- Add `MODEL_STARTUP_MODE = os.getenv("MODEL_STARTUP_MODE", "strict")`
- In `model_loader._load_model()`: if MLmodel not found, raise instead of returning None
- In lifespan: if strict mode, let the exception propagate (fail startup)
- `is_loaded` only set True when model actually loaded

---

## Files Modified

| # | File | Change |
|---|------|--------|
| 1 | `deployment/api/config.py` | Remove `LABEL_ENCODER_PATH`, `FEATURE_PIPELINE_PATH`; add `MODEL_STARTUP_MODE` |
| 2 | `deployment/api/model_loader.py` | Strip to: load model, connect DB, init LLM. Remove encoder/pipeline/stats loading |
| 3 | `deployment/api/inference.py` | Remove feature pipeline transform, label decoding, MLflow type fixups. Keep feature computation calls + row assembly |
| 4 | `deployment/api/schemas.py` | Minor: simplify `ml_unavailable_reason` options |
| 5 | `deployment/api/main.py` | Strict startup enforcement, simplified health |
| 6 | `deployment/api/dependencies.py` | No change needed |

## Verification
1. Start API with retrained model artifact → startup succeeds in strict mode
2. Start API with missing artifact → startup fails with clear error
3. `/health` reports model loaded
4. `/predict` returns label + confidence from the single model call
5. Run test suite — no regressions
