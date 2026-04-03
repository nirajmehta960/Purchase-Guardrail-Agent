# Code Structure Review: `deployment/api/` & `deployment/frontend/`

## Context
This is a **review-only** assessment of coding structure and SOLID principles. The code is functional — these are recommendations for maintainability, testability, and clean architecture.

---

## Backend (`deployment/api/`) — Score: 6/10

### Critical Issues

#### 1. `ModelManager` is a God Object (SRP violation)
**File:** `api/model_loader.py`

**What's happening:**
`ModelManager` is a single class that owns 8 unrelated responsibilities:
- ML model loading (MLflow pyfunc + XGBoost fallback) — lines 113-161
- Label encoder loading (joblib) — lines 181-207
- Feature pipeline loading (sklearn) — lines 163-179
- Database initialization (SQLAlchemy via savviocore) — lines 209-223
- LLM provider initialization — lines 225-234
- Category stats pre-computation (pandas aggregation) — lines 236-257
- ML prediction + probability extraction — lines 318-366
- Health checks (DB ping, LLM name) — lines 372-388

It is then exposed as a module-level singleton on line 395:
```python
model_manager = ModelManager()
```

**Why this is critical:**

1. **Untestable in isolation.** To unit-test the `predict()` method, you must also have a valid `db_engine`, `llm_provider`, `category_stats`, and `label_encoder` — none of which prediction actually needs at call time. There's no way to construct a `ModelManager` with *just* a model and encoder for a focused test.

2. **One failure domain for everything.** If `_init_llm_provider()` raises, it's caught and swallowed with a warning (line 232), but `_loaded` is still set to `True` (line 110). The `is_loaded` property reports success even when critical sub-resources failed. Callers have no granular way to know *what* loaded.

3. **Change amplification.** Adding a new resource (e.g., a vector DB for embeddings) means modifying `ModelManager.__init__`, `load()`, and potentially `predict()` — touching a class that already does too much. This violates the Open/Closed Principle: the class must be modified rather than extended.

4. **Singleton makes parallel testing impossible.** Since `model_manager` is module-level global state, two tests cannot run with different configurations simultaneously. Any test that imports `model_loader` gets the same singleton instance.

**How the recommendation fixes this:**

Split by responsibility into classes that each own one resource:

```python
class ModelArtifactLoader:
    """Loads and holds the ML model artifact only."""
    def load(self, artifact_dir: str) -> None: ...
    def predict(self, features) -> tuple[str, float | None]: ...

class FeatureTransformer:
    """Loads and applies the sklearn preprocessing pipeline."""
    def load(self, path: str) -> None: ...
    def transform(self, df: pd.DataFrame) -> pd.DataFrame: ...

class DatabaseConnection:
    """Manages the SQLAlchemy engine lifecycle."""
    def connect(self, env: str) -> None: ...
    def check(self) -> bool: ...

class LLMProvider:
    """Already exists in llm/llm_provider.py — just use it directly."""
```

Then wire them via constructor injection:
```python
class InferenceResources:
    def __init__(self, model: ModelArtifactLoader, features: FeatureTransformer,
                 db: DatabaseConnection, llm: LLMProvider): ...
```

Now each piece can be tested independently, mocked individually, and fails with clear granularity. Adding a new resource means adding a new class, not editing an existing one.

---

#### 2. `run_inference()` is a 450+ line mega-function (SRP violation)
**File:** `api/inference.py`, lines 221-676

**What's happening:**
A single function `run_inference()` handles the *entire* inference pipeline sequentially:
- Step 1 (lines 258-279): Load user financial profile from DB
- Step 2 (lines 283-359): Parse intent via LLM *or* resolve product by ID
- Step 3 (lines 363-419): Compute 6 affordability features + guard None values
- Step 4 (lines 423-431): Run Deterministic Engine (Layer 1)
- Step 5 (lines 435-494): Load product/reviews, compute features, run Downgrade Engine (Layer 2)
- Step 6 (lines 498-598): Assemble 40+ field feature row, preprocess, run ML model
- Step 7 (lines 602-648): Build LLM context, generate response, run guardrails

It also contains 10+ late imports on lines 234-248 that only execute at call time.

**Why this is critical:**

1. **Cannot test any step independently.** To test just the ML scoring logic (Step 6), you must set up a valid user profile, product match, affordability result, engine decision, product features, and review features — all the preceding steps. There's no way to call "just the ML part" because it's all local variables inside one function scope.

2. **Debugging is needle-in-haystack.** When inference fails in production, the stack trace points to a line somewhere in a 450-line function. The developer must mentally trace which *stage* failed and what local state led there. With decomposed functions, the stack trace immediately shows which stage broke.

3. **Late imports hide the dependency graph.** Lines 234-248 import 10+ modules (`DecisionEngine`, `DowngradeEngine`, `compute_affordability`, `compute_product_features`, `compute_review_features`, `generate_response`, `check_response`, `resolve_product`, `parse_user_input`, response templates). These imports happen every call, are invisible to static analysis tools, and mean `import deployment.api.inference` succeeds even if these dependencies are broken — the error only surfaces at runtime during a request.

4. **Feature assembly is brittle.** Lines 515-564 manually construct a 40+ field dict (`raw_row`) mapping column names to values. If the training schema adds or renames a column, this dict must be updated by hand — there's no schema validation or shared constant defining the expected columns.

**How the recommendation fixes this:**

Decompose into pipeline stages, each a standalone function:

```python
def _load_user_context(user_id: str, db_engine) -> UserContext: ...
def _resolve_product(request: PredictRequest, llm, db_engine) -> ResolvedProduct: ...
def _compute_features(user: UserContext, product: ResolvedProduct, ...) -> FeatureSet: ...
def _run_deterministic_engine(features: FeatureSet) -> L1Decision: ...
def _run_downgrade_engine(l1: L1Decision, product_feats, review_feats) -> L2Decision: ...
def _score_ml_model(features: FeatureSet, manager) -> MLScore: ...
def _generate_explanation(context: ..., llm) -> str: ...

def run_inference(request, manager) -> PredictResponse:
    user = _load_user_context(request.user_id, manager.db_engine)
    product = _resolve_product(request, manager.llm_provider, manager.db_engine)
    features = _compute_features(user, product, ...)
    l1 = _run_deterministic_engine(features)
    l2 = _run_downgrade_engine(l1, ...)
    ml = _score_ml_model(features, manager)
    explanation = _generate_explanation(...)
    return PredictResponse(...)
```

Each stage is independently testable with clear inputs/outputs. The orchestrator (`run_inference`) becomes a readable 20-line pipeline. Late imports move to module level where static analysis catches missing dependencies at import time.

---

#### 3. Endpoints tightly coupled to singleton (DIP violation)
**File:** `api/main.py`

**What's happening:**
Every endpoint directly imports and uses the global `model_manager` singleton:
- Line 30: `from deployment.api.model_loader import model_manager`
- Line 61: `model_manager.load()` in lifespan
- Line 152: `if not model_manager.db_engine` (user profile endpoint)
- Line 187: `if not model_manager.db_engine` (products endpoint)
- Line 244: `run_inference(request, model_manager)` (predict endpoint)
- Line 277: `run_inference(request, model_manager)` (evaluate endpoint)

Additionally, `inference` and `products_catalog` are imported inside endpoint bodies (lines 150, 185, 241, 268) rather than at module level.

**Why this is critical:**

1. **Violates Dependency Inversion Principle.** High-level policy (endpoint business logic) directly depends on a low-level detail (the specific `ModelManager` singleton instance). The endpoint can never work with a different resource configuration without modifying the import.

2. **Repeated boilerplate / shotgun surgery.** The pattern `if not model_manager.db_engine: raise HTTPException(503)` appears 3 times (lines 152, 187, 258). If the error message or status code needs to change, or a new precondition is added, every endpoint must be updated independently. This is a textbook case of duplicated guard logic that should live in one place.

3. **Late imports obscure the API surface.** `from deployment.api.inference import run_inference` on line 241 (inside the endpoint function) means you can't see at a glance what modules this API depends on. It also means the import runs on every request rather than once at startup, adding latency and making import errors appear as runtime 500s rather than startup failures.

4. **Testing requires monkeypatching.** To test the `/predict` endpoint with a mock model, you must patch `deployment.api.model_loader.model_manager` at the module level. FastAPI's built-in `Depends()` pattern exists specifically to avoid this — it lets you override dependencies per-test via `app.dependency_overrides`.

**How the recommendation fixes this:**

Use FastAPI's dependency injection:

```python
# dependencies.py
def get_db_engine():
    if not model_manager.db_engine:
        raise HTTPException(503, "Database unavailable.")
    return model_manager.db_engine

def get_inference_service():
    return InferenceService(model_manager)

# main.py
@app.post("/predict")
async def predict(request: PredictRequest, service: InferenceService = Depends(get_inference_service)):
    return service.run(request)
```

The DB-availability check lives in one place (`get_db_engine`). Endpoints declare what they *need* rather than reaching into global state. Tests override dependencies cleanly:

```python
app.dependency_overrides[get_inference_service] = lambda: mock_service
```

No monkeypatching, no late imports, no repeated null checks.

### Medium Issues

| Issue | File | Detail |
|-------|------|--------|
| `PredictResponse` has 27 fields mixing concerns | `api/schemas.py` | Split into `RecommendationResult`, `EvaluationContext`, `DebugInfo` |
| Magic rule names hardcoded as strings | `api/inference.py` | Use an `Enum` for rule identifiers |
| Magic threshold numbers (0.34, 0.67, etc.) | `api/quality_signals.py` | Extract to named constants or config |
| SQL queries use raw f-strings with `text()` | `api/products_catalog.py` | Migrate to SQLAlchemy Core constructs |
| No input validation on data from DB queries | `api/inference.py` | Add dataclass/Pydantic validation for `user_profile`, `product_row` |
| `sys.path.insert()` manipulation | `api/model_loader.py` | Fragile; prefer proper package installation |
| Error swallowing — exceptions caught and logged as warnings | `api/model_loader.py` | `is_loaded` returns True even if sub-resources failed |

### What's Done Well
- Comprehensive logging at every pipeline step
- Graceful degradation (API doesn't crash if model/DB/LLM unavailable)
- Edge case handling (zero income, missing credit score, hypothetical products)
- Structured error responses with Pydantic models
- CORS and request logging middleware properly configured

---

## Frontend (`deployment/frontend/`) — Score: 4.5/10

### Critical Issues

#### 1. `AiChat.tsx` is a 1,002-line monolith (SRP violation)
**File:** `frontend/src/components/AiChat.tsx`
- Contains: message state (9 `useState` calls), catalog search logic, 15+ nested helper components/functions, response parsing, signal mapping, all UI rendering
- Nested components (`SeverityDot`, `TechnicalFeatureRow`, `TechnicalDetailsPanel`, etc.) defined inside the file — cannot be unit tested independently
- **Recommendation:** Split into `MessageList`, `CatalogSearch`, `MessageInput`, `AssistantMessage`, `TechnicalDetailsPanel` as separate files.

#### 2. TypeScript strict mode is disabled
**File:** `frontend/tsconfig.app.json`
- `strict: false`, `noImplicitAny: false`, `strictNullChecks: false`, `noUnusedLocals: false`
- Provides almost no compile-time safety — null/undefined errors only caught at runtime
- **Recommendation:** Enable `strict: true`. Fix resulting errors incrementally.

#### 3. Duplicated formatting logic (DRY violation)
**Files:** `frontend/src/components/AiChat.tsx` + `frontend/src/components/FinancialDashboard.tsx`
- `fmtMoney()`, `fmtPct()`, and severity threshold logic duplicated across both files
- **Recommendation:** Extract to `src/lib/formatters.ts` and `src/lib/thresholds.ts`.

#### 4. Minimal test coverage
- Only 2 tests: 1 placeholder (`expect(true).toBe(true)`) and 1 smoke test (page title check)
- No unit tests for business logic, no component tests, no API mock strategy

### Medium Issues

| Issue | File | Detail |
|-------|------|--------|
| `UserContext` mixes 3 concerns (session, profile data, loading state) | `frontend/src/context/UserContext.tsx` | Split into `SessionContext` + React Query hook for profile |
| Magic numbers for severity thresholds (0.28, 0.36, 0.5, etc.) | AiChat.tsx, FinancialDashboard.tsx | Centralize in `src/config/thresholds.ts` |
| Business logic (financial calculations) in UI components | `frontend/src/components/FinancialDashboard.tsx` | Extract to `src/lib/financial-calculations.ts` |
| Unused dependencies bloating bundle | `frontend/package.json` | `zod`, `react-hook-form`, many unused shadcn components |
| Components directly import API functions (DIP violation) | AiChat.tsx, UserContext.tsx | Use dependency injection or custom hooks wrapping React Query |
| Debounce implementation has potential memory leak | AiChat.tsx | Use a dedicated `useDebounce` hook |
| No error boundaries | App.tsx | Add `<ErrorBoundary>` wrapper |

### What's Done Well
- Clean API service layer with typed interfaces (`src/services/api.ts`)
- Good use of React Query provider at app root
- Proper `useIsMobile` hook with cleanup
- Responsive layout with framer-motion animations
- shadcn/ui component library is a solid choice

---

## Summary: Top 5 Recommendations by Impact

| # | Area | Action | Effort |
|---|------|--------|--------|
| 1 | Frontend | Split `AiChat.tsx` into 4-5 focused components | Medium |
| 2 | Backend | Decompose `run_inference()` into pipeline stages | Medium |
| 3 | Backend | Split `ModelManager` into focused classes + add DI | High |
| 4 | Frontend | Enable TypeScript `strict: true` | Medium |
| 5 | Both | Extract duplicated constants/formatters/thresholds | Low |

---

*This is a review document — no code changes are proposed for execution.*
