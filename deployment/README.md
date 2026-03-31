# SavVio — Model Deployment Pipeline
**MLOps Course: Model Deployment Phase**

**Team Members:** Murtaza Nipplewala, Niraj Mehta, Wen-Hsin Su, Pranathi Bombay, Rishabh Joshi, Sanjana Patnam

---

## Core Principle

> Deployment must ensure reproducibility, automation, and safe model serving, while preserving the rule:
> **Deterministic logic remains authoritative. ML/LLM layers cannot override it.**

---

The model is deployed exclusively on Google Cloud Platform using the following GCP services:
- **GCP Cloud Run**: Serverless containerized deployment for the API and frontend
- **MLflow**: Model serving, versioning, and model registry
- **GCP Cloud Storage**: Bucket for Model artifact storage and DVC remote storage
- **GCP Cloud Monitoring**: Performance and health monitoring
- **GCP Cloud Logging**: Centralized logging and log analysis
- **Github Actions**: CI/CD pipeline for automated builds and deployments
- **GCP Artifact Registry**: Container image storage and versioning
- **GCP Cloud SQL**: Managed PostgreSQL database with pgvector for production 
- **GCP Secret Manager**: Keep secrets and passwords

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Repository Structure](#3-repository-structure)
4. [Pipeline Execution Order](#4-pipeline-execution-order)
5. [Quick Reference: Tools by Phase](#5-quick-reference-tools-by-phase)
6. [Phase 1 — Infrastructure Setup (Terraform)](#phase-1--infrastructure-setup-terraform)
7. [Phase 2 — Resource Provisioning](#phase-2--resource-provisioning)
8. [Phase 3 — Backend: API & Inference Layer](#phase-3--backend-api--inference-layer)
9. [Phase 4 — Containerization](#phase-4--containerization)
10. [Phase 5 — Resource Deployment](#phase-5--resource-deployment)
11. [Phase 6 — CI/CD Automation](#phase-6--cicd-automation)
12. [Phase 7 — Monitoring & Logging](#phase-7--monitoring--logging)
13. [Phase 8 — Drift Detection](#phase-8--drift-detection)
14. [Phase 9 — Testing](#phase-9--testing)
15. [Phase 10 — Monitoring Dashboard](#phase-10--monitoring-dashboard)
16. [Phase 11 — Frontend (Streamlit UI)](#phase-11--frontend-streamlit-ui)
17. [Deliverable Checklist](#deliverable-checklist)

---

## 1. Project Overview

SavVio is an AI-driven financial advocacy tool that provides pre-purchase recommendations in the form of a **Green / Yellow / Red** signal, based on a user's real-time financial health and product quality signals.
This phase covers the **deployment** of the production-ready SavVio model onto Google Cloud Platform. It picks up directly from the Model Development phase: the approved, bias-validated XGBoost model has been pushed to GCP Artifact Registry with a versioned tag, rollback pointer, and full MLflow lineage. The goal here is to expose that model through a live, monitored, auto-scaling inference API — with CI/CD automation, drift detection, and a retraining trigger loop.

**The three-layer recommendation system is preserved end-to-end at inference:**

| Layer | Responsibility |
|---|---|
| Deterministic Engine | Authoritative Green/Yellow/Red classification via financial rule engine |
| ML Model | Confidence scoring and ranking support — cannot override engine output |
| LLM + Guardrails | Extracts product information from user prompt, generates natural language recommendation with NeMo safety enforcement |

---

## 2. Architecture

### Inference Request Flow

```
User Prompt (natural language input)
        ↓
┌─────────────────────────────────────┐
│          FastAPI /predict           │  ← Receives request, routes to LLM Wrapper for input parsing
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│          LLM Wrapper                │  ← Extracts product info from user prompt
│         NeMo Guardrails             │  ← Hallucination + safety enforcement
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│       Deterministic Engine          │  ← Hard financial rules
│     Green / Yellow / Red            │  ← Authoritative output
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│          ML Model Layer             │  ← Confidence + ranking
│    XGBoost (loaded from Registry)   │  ← Cannot override engine
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│        LLM Wrapper                  │  ← Natural language recommendation
│         NeMo Guardrails             │  ← Final safety enforcement
└─────────────────────────────────────┘
        ↓
  User-facing Recommendation (Streamlit UI)
```

### Deployment Infrastructure

```
GitHub Push / PR
        ↓
GitHub Actions (CI/CD)
        ↓
┌──────────────────────────────────────────┐
│             Docker Container             │
│   FastAPI + Deterministic Engine +       │
│   ML Model + LLM Wrapper                 │
└──────────────────────────────────────────┘
        ↓
GCP Artifact Registry  (image push)
        ↓
GCP Cloud Run  (live endpoint)
        ↓
┌────────────────────┐   ┌──────────────────┐
│   Cloud Logging    │   │   Evidently AI   │
│  (latency, reqs)   │   │ (drift detection) │
└────────────────────┘   └──────────────────┘
```

### Latency SLA

| Metric | Threshold |
|---|---|
| Target latency | < 2 seconds per request |
| p50 latency | Tracked via Cloud Logging |
| p95 latency | Tracked via Cloud Logging |
| p99 latency | Tracked via Cloud Logging |

---

## 3. Repository Structure

```
SavVio/
├── deployment/
│   ├── README.md                          # This file
│   ├── requirements.txt                   # Python dependencies for inference
│   ├── infrastructure/
│   │   └── terraform/
│   │       ├── main.tf                    # Cloud Run + Artifact Registry + IAM
│   │       ├── variables.tf               # Input variables
│   │       └── outputs.tf                 # Output values (endpoint URL, etc.)
│   ├── api/
│   │   ├── main.py                        # FastAPI app — /predict endpoint
│   │   ├── inference.py                   # Model loading + inference logic
│   │   └── schemas.py                     # Request / response Pydantic schemas
│   ├── deterministic_engine/
│   │   └── decision_logic.py              # Green/Yellow/Red rule engine (carried from model-dev)
│   ├── llm_wrapper/
│   │   └── llm_wrapper.py                 # LLM + NeMo Guardrails integration
│   ├── docker/
│   │   └── Dockerfile                     # Full inference stack containerization
│   ├── monitoring/
│   │   ├── drift_detector.py              # Evidently drift checks
│   │   ├── dashboard.py                   # Monitoring dashboard
│   │   └── alert_config.yaml             # Latency and drift alert thresholds
│   └── tests/
│       ├── test_api.py                    # Endpoint correctness + response validation
│       ├── test_inference.py              # Model loading + prediction tests
│       ├── test_decision_logic.py         # Deterministic engine rule tests
│       ├── test_drift_detector.py         # Drift detection threshold tests
│       └── test_llm_wrapper.py            # Guardrail enforcement tests
│
├── frontend/
│   └── app.py                             # Streamlit frontend UI
│
├── .github/
│   └── workflows/
│       └── deploy.yml                     # GitHub Actions deployment pipeline
│
├── infrastructure/
│   └── terraform/                         # (Shared infra configs if applicable)
│
└── model-development/                     # Upstream phase — model artifacts live here
    └── models/                            # Local staging (registry is source of truth)
```

---

## 4. Pipeline Execution Order

```
1.  Provision infrastructure using Terraform
         ↓
2.  Build inference API (FastAPI)
         ↓
3.  Containerize model + API using Docker
         ↓
4.  Push container image to Artifact Registry
         ↓
5.  Deploy container to Cloud Run
         ↓
6.  Expose /predict endpoint
         ↓
7.  Set up CI/CD pipeline (GitHub Actions)
         ↓
8.  Enable monitoring (latency, logs, prediction distribution)
         ↓
9.  Detect data/model drift
         ↓
10. Validate deployed system
         ↓
11. Deploy Streamlit frontend
```

---

## 5. Quick Reference: Tools by Phase

| Phase | Primary Tools | Alternatives | CI/CD Gate |
|---|---|---|---|
| Infra Setup | Terraform, GCP | Pulumi | Infra must provision successfully |
| Resource Provisioning | Terraform | — | Resources verified in GCP |
| API & Inference | FastAPI, NeMo | Flask | Endpoint response valid |
| Containerization | Docker | Podman | Build success |
| Resource Deployment | Cloud Run | Kubernetes (GKE) | Endpoint live check |
| CI/CD | GitHub Actions | Cloud Build, Jenkins | Full pipeline must pass |
| Monitoring & Logging | Cloud Logging | Prometheus, Grafana | Alerts configured |
| Drift Detection | Evidently | WhyLabs, Arize | Drift threshold checks |
| Testing | pytest | unittest | All tests pass |
| Monitoring Dashboard | Streamlit, Cloud Logging | Grafana | Dashboard live |
| Frontend | Streamlit | — | UI live and connected to API |

---

## Phase 1 — Infrastructure Setup (Terraform)

### Objective
Define and initialize all deployment infrastructure using Infrastructure as Code, ensuring reproducible and consistent environment provisioning.

### Tasks
- Write Terraform configuration (`deployment/infrastructure/terraform/main.tf`) for:
  - **Cloud Run** — serverless container hosting for the inference API
  - **Artifact Registry** — container image storage
  - **IAM roles** — least-privilege service account for Cloud Run to pull images and write logs
- Write `variables.tf` for input variables (project ID, region, image name, service account)
- Write `outputs.tf` to expose Artifact Registry URL and Cloud Run endpoint URL
- Initialize Terraform:
  ```bash
  cd deployment/infrastructure/terraform
  terraform init
  ```
- Validate configuration:
  ```bash
  terraform validate
  ```
- Review planned changes before applying:
  ```bash
  terraform plan
  ```

### Why This Matters
Manual GCP setup leads to environment inconsistencies across team members and runs. Terraform ensures the exact same infrastructure is reproduced on every deployment and can be version-controlled alongside the codebase.

### Tools

| Tool | Purpose | Alternative |
|---|---|---|
| Terraform | Infrastructure as Code | Pulumi |
| GCP | Cloud infrastructure provider | AWS |

---

## Phase 2 — Resource Provisioning

### Objective
Provision all required cloud resources by applying the Terraform configuration.

### Tasks
- Apply Terraform to create all resources:
  ```bash
  terraform apply
  ```
- Verify the following resources were created in GCP Console:
  - Container registry (Artifact Registry repository)
  - Cloud Run service (initially empty — image will be pushed in Phase 4)
  - IAM service account with correct roles
- Capture Artifact Registry URL and Cloud Run endpoint URL from Terraform outputs:
  ```bash
  terraform output
  ```
- Record both URLs in `deployment/config/deployment_config.yaml` for use in later phases

### Tools

| Tool | Purpose |
|---|---|
| Terraform | Resource provisioning |
| Cloud Run | Serverless container hosting |
| GCP Artifact Registry | Container image storage |

---

## Phase 3 — Backend: API & Inference Layer

### Objective
Expose the model through a production-grade REST API. The user provides a natural language prompt — the LLM extracts product information from the prompt, which then flows through the deterministic engine and ML model to produce a recommendation.

### Tasks
- Build FastAPI application in `deployment/api/main.py`
- Define Pydantic request schema in `deployment/api/schemas.py`:
  - **Request:** natural language user prompt (string)
  - **Response:** recommendation color (Green/Yellow/Red), confidence score, natural language explanation
- Implement `/predict` endpoint in `deployment/api/inference.py` with the following strict call order:
  1. Receive natural language user prompt
  2. Pass prompt to **LLM Wrapper** (`llm_wrapper.py`) → extract product signals and financial context from prompt
  3. Pass extracted signals through **Deterministic Engine** (`decision_logic.py`) → authoritative Green/Yellow/Red color output
  4. Pass extracted signals to **ML Model** → confidence score and ranking support only (cannot override engine color)
  5. Pass deterministic color + ML confidence back to **LLM Wrapper** → generate natural language explanation, enforced by NeMo Guardrails
- Enforce rule: LLM output must never contradict or override the deterministic engine's color classification
- Add `/health` endpoint for liveness checks and deployment verification
- Test `/predict` and `/health` endpoints locally before containerization

### Tools

| Tool | Purpose |
|---|---|
| FastAPI | API serving and request routing |
| Python | Inference logic and engine integration |
| Pydantic | Request/response schema validation |
| NeMo Guardrails | LLM safety enforcement at output boundary |

---

## Phase 4 — Containerization

### Objective
Package the full inference stack — FastAPI app, Deterministic Engine, ML model artifact, and LLM wrapper — into a single deployable Docker container.

### Tasks
- Write `deployment/docker/Dockerfile`:
  ```dockerfile
  FROM python:3.11-slim
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install -r requirements.txt
  COPY deployment/ ./deployment/
  CMD ["uvicorn", "deployment.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
- Build container locally:
  ```bash
  docker build -t savvio-deploy -f deployment/docker/Dockerfile .
  ```
- Run container locally and verify:
  ```bash
  docker run -p 8000:8000 savvio-deploy
  ```
- Test `/health` endpoint: `curl http://localhost:8000/health`
- Test `/predict` endpoint with a natural language prompt — confirm Green/Yellow/Red response is returned
- Confirm response correctness — returned color must match deterministic engine output for the extracted signals
- Confirm response latency is within acceptable SLA locally before pushing
- Ensure all environment variables are configurable via `.env` file

### Tools

| Tool | Purpose |
|---|---|
| Docker | Full inference stack containerization |
| uvicorn | ASGI server for FastAPI |

---

## Phase 5 — Resource Deployment

### Objective
Push the verified container image to Artifact Registry and deploy it to Cloud Run to expose the live `/predict` endpoint.

### Tasks
- Tag and push image to Artifact Registry:
  ```bash
  docker tag savvio-deploy gcr.io/<PROJECT_ID>/savvio-deploy:latest
  docker push gcr.io/<PROJECT_ID>/savvio-deploy:latest
  ```
- Deploy to Cloud Run:
  ```bash
  gcloud run deploy savvio-deploy \
    --image gcr.io/<PROJECT_ID>/savvio-deploy:latest \
    --platform managed \
    --region us-central1 \
    --allow-unauthenticated
  ```
- Verify deployment:
  - Confirm API is live: `curl https://<CLOUD_RUN_URL>/health`
  - Confirm `/predict` returns correct Green/Yellow/Red response with a test prompt
  - Confirm response latency is within acceptable SLA
- Record the live endpoint URL in `deployment/config/deployment_config.yaml`

---

## Phase 6 — CI/CD Automation

### Objective
Automate the full build → test → deploy pipeline on every code push, with gate checks that block bad deployments before they reach production.

### Pipeline Architecture

```
GitHub Push / PR on deployment/
        ↓
GitHub Actions (.github/workflows/deploy.yml) [Dockerized]
        ├── 1. Unit tests (pytest deployment/tests/)
        │       └── fail? → BLOCK + alert
        ├── 2. Docker build
        │       └── fail? → BLOCK + alert
        ├── 3. Endpoint response validation (local container smoke test)
        │       └── fail? → BLOCK + alert
        ├── 4. Push image to Artifact Registry
        ├── 5. Deploy to Cloud Run
        ├── 6. Live endpoint check (/health + /predict with test prompt)
        │       └── fail? → BLOCK + rollback + alert
        ├── 7. Latency check (response time within SLA threshold)
        │       └── breach? → alert
        └── 8. Slack/email notification on success or failure
```

### Tasks
- Configure `.github/workflows/deploy.yml` with the above gate sequence
- Automate: test execution → Docker build → image push → Cloud Run deploy → live validation
- Implement rollback trigger: if live endpoint check fails post-deploy, revert to previous stable image in Artifact Registry
- Add Slack/email failure alerts at each gate
- Test full end-to-end CI/CD pipeline by pushing a change and verifying all gates execute correctly
- Document pipeline YAML for reproducibility

### Tools

| Tool | Purpose |
|---|---|
| GitHub Actions | CI/CD orchestration |
| Docker | Build and containerization |
| Cloud Run | Deployment target |
| Slack / Email | Failure and success notifications |

---

## Phase 7 — Monitoring & Logging

### Objective
Track live system performance after deployment — capturing latency, request volume, prediction distribution, and system health metrics.

### Tasks
- Enable **Cloud Logging** for all `/predict` requests:
  - Log: request timestamp, input prompt (sanitized), predicted color, confidence score, latency per request
- Monitor the following metrics:
  - Latency per request (p50, p95, p99)
  - Request volume over time
  - Green/Yellow/Red prediction distribution over time
  - LLM hallucination flag rate
  - NeMo safety rail trigger volume
- Set alert thresholds in `deployment/monitoring/alert_config.yaml`:
  - Latency breach: response time > 2 seconds (SLA threshold) → alert
  - Prediction distribution shift: Green/Yellow/Red ratio changes significantly → alert
  - Hallucination spike: flag rate exceeds threshold → alert
  - Safety rail trigger volume increase → alert

### Tools

| Tool | Purpose |
|---|---|
| Cloud Logging | Request and prediction logs |
| GCP Cloud Monitoring | Infrastructure-level alerts and dashboards |

---

## Phase 8 — Drift Detection

### Objective
Detect data drift (shifts in input feature distributions) and model drift (shifts in output prediction distributions) to identify when the deployed model may no longer reflect real-world conditions.

### Tasks
- Implement drift detection in `deployment/monitoring/drift_detector.py` using Evidently
- Monitor input feature distributions against training baseline for:
  - Financial signals: `discretionary_income`, `debt_to_income_ratio`, `monthly_expense_burden_ratio`, `emergency_fund_months`, `savings_to_income_ratio`
  - Product signals: `price`, `average_rating`, `rating_number`, `rating_variance`
- Track output distribution shifts: changes in Green/Yellow/Red recommendation ratio over time
- Run drift detection on a rolling window (daily or weekly batch)
- Configure drift severity thresholds in `deployment/monitoring/alert_config.yaml`:
  - Green: distributions stable → no action
  - Yellow: minor shift detected → alert, monitor closely
  - Red: significant drift detected → alert and log
- Alert and log when any drift threshold is breached
- Verify drift detection triggers correctly by simulating distribution shifts

### Drift Severity Levels

| Level | Condition | Action |
|---|---|---|
| Green | Distributions stable | No action |
| Yellow | Minor shift detected | Alert, monitor closely |
| Red | Significant drift detected | Alert and log |

### Tools

| Tool | Purpose |
|---|---|
| Evidently | Data and model drift detection |
| GCP Cloud Monitoring | Infrastructure-level drift alerts |

---

## Phase 9 — Testing

### Objective
Validate the deployed system for correctness, latency, and reliability.

### Tasks
- **Endpoint correctness tests** (`tests/test_api.py`):
  - Test `/health` returns 200 OK
  - Test `/predict` with a natural language prompt describing a Green scenario — confirm Green response
  - Test `/predict` with a natural language prompt describing a Yellow scenario — confirm Yellow response
  - Test `/predict` with a natural language prompt describing a Red scenario — confirm Red response
  - Test response schema — confirm color, confidence score, and explanation fields present
- **Model loading and prediction tests** (`tests/test_inference.py`):
  - Confirm model loads from registry without errors
  - Confirm prediction output shape and confidence score range (0–1)
- **Deterministic engine tests** (`tests/test_decision_logic.py`):
  - Verify all hard-stop Red conditions
  - Verify all Yellow caution conditions
  - Verify confidence downgrade checks
  - Verify edge cases: missing fields → Yellow, conflicting rules → more conservative class
- **Guardrail tests** (`tests/test_llm_wrapper.py`):
  - Confirm NeMo rails block LLM outputs that contradict deterministic engine color
  - Confirm rails block hallucinated financial figures
  - Test adversarial prompts — confirm unsafe completions are blocked
- **Drift detector tests** (`tests/test_drift_detector.py`):
  - Confirm drift thresholds trigger correctly at each severity level
- Confirm p95 latency is within SLA on live endpoint
- Run full test suite:
  ```bash
  pytest deployment/tests/ -v
  ```

---

## Phase 10 — Monitoring Dashboard

### Objective
Build a live monitoring dashboard to visualize system performance, prediction distribution, and health metrics post-deployment.

### Tasks
- Build monitoring dashboard in `deployment/monitoring/dashboard.py`
- Connect dashboard to Cloud Logging to pull live request and prediction data
- Display the following metrics on the dashboard:
  - Latency per request (p50, p95, p99) over time
  - Request volume over time
  - Green/Yellow/Red prediction distribution over time
  - LLM hallucination flag rate
  - NeMo safety rail trigger volume
- Verify dashboard displays accurate data by cross-referencing with Cloud Logging entries
- Deploy dashboard to be accessible alongside the main application

### Tools

| Tool | Purpose |
|---|---|
| Cloud Logging | Data source for dashboard metrics |
| GCP Cloud Monitoring | Infrastructure-level alerts |

---

## Phase 11 — Frontend (Streamlit UI)

### Objective
Build a user-facing Streamlit interface that allows users to enter a natural language prompt and receive a Green/Yellow/Red recommendation with a natural language explanation.

### Tasks
- Build Streamlit app in `frontend/app.py`
- Implement user input form:
  - Text input field for natural language prompt (e.g. "I want to buy AirPods Pro for $249, I earn $4000/month and have $800 in savings")
- Connect frontend to the live `/predict` FastAPI endpoint on Cloud Run
- Display recommendation output:
  - Green/Yellow/Red signal with color-coded visual indicator
  - ML confidence score
  - Natural language explanation from LLM wrapper
- Handle API errors gracefully — display user-friendly error messages if endpoint is unavailable
- Handle edge cases:
  - Empty prompt → prompt user to enter input
  - API timeout → display retry message
- Style the UI to be clean and readable
- Test frontend locally:
  ```bash
  streamlit run frontend/app.py
  ```
- Verify frontend connects correctly to live Cloud Run endpoint
- Deploy frontend to Cloud Run or as a separate Streamlit Cloud instance

### Tools

| Tool | Purpose |
|---|---|
| Streamlit | Frontend UI framework |
| requests | API calls to FastAPI /predict endpoint |

---

## Deliverable Checklist

### Professor Requirements
- [ ] Cloud deployment specified: GCP — Cloud Run for serving, Artifact Registry for container storage
- [ ] Deployment service documented (Cloud Run — serverless containerized deployment)
- [ ] Automated deployment scripts included (Terraform + GitHub Actions + Docker)
- [ ] Scripts pull latest model from registry and deploy automatically
- [ ] CI/CD pipeline configured to trigger redeployment on new model versions (GitHub Actions)
- [ ] Step-by-step replication instructions provided
- [ ] Environment setup and dependency instructions included
- [ ] Deployment verification steps documented (`curl /health` + `/predict`)
- [ ] Monitoring for model decay and data shift implemented (Evidently + Cloud Logging)
- [ ] Drift detection implemented on input feature distributions and output prediction distribution
- [ ] Predefined thresholds for drift detection configured
- [ ] Notifications configured for deployment outcomes (Slack/email)
- [ ] Deployment scripts structured for minimal manual intervention
- [ ] Logging and monitoring functionality included post-deployment
- [ ] Video demonstration planned: fresh environment, full deploy walkthrough, endpoint verification

### SavVio-Specific
- [ ] Deterministic engine preserved and authoritative at inference — ML and LLM cannot override color output
- [ ] User prompt accepted as natural language input — LLM extracts product information from prompt
- [ ] Three-layer inference stack connected: LLM extraction → Deterministic Engine → ML Model → LLM explanation
- [ ] NeMo Guardrails integrated and tested with adversarial prompts
- [ ] Monitoring dashboard built and deployed
- [ ] Drift detection covers Green/Yellow/Red output distribution shifts
- [ ] Streamlit frontend live and connected to Cloud Run endpoint
- [ ] CI/CD gate sequence: unit tests → build → smoke test → push → deploy → live check → latency check
