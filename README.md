# SavVio

An AI-driven financial advocacy tool designed to bridge the gap between e-commerce and personal finance. SavVio serves as a "Financial Fiduciary" that evaluates whether a user should make a purchase based on their real-time financial health and the product's actual utility.

## Project Overview

SavVio is a comprehensive MLOps project that integrates real-time product data with sensitive financial streams to provide responsible, conversational shopping guidance. Unlike traditional shopping assistants that focus on maximizing conversion, SavVio evaluates purchases based on:

- **Financial Health**: User's income, expenses, savings, and debt obligations.
- **Product Utility**: Analysis of product specifications and real-world usefulness.
- **Affordability Metrics**: Calculated discretionary budget and residual utility scores.

The system provides Green/Yellow/Red light recommendations before users complete their purchase.

## Live Deployment (Production)

| Component | Status | Service | URL |
|-----------|--------|---------|-----|
| Frontend UI | Live | React / Vite / Tailwind | [https://savvio-ai-ebw2ryzjkq-ue.a.run.app](https://savvio-ai-ebw2ryzjkq-ue.a.run.app) |
| Inference API | Live | FastAPI / uvicorn | [https://savvio-backend-api-ebw2ryzjkq-ue.a.run.app](https://savvio-backend-api-ebw2ryzjkq-ue.a.run.app) |
| Model Registry | Live | MLflow / GCS | [https://savvio-ai-mlflow-ebw2ryzjkq-ue.a.run.app](https://savvio-ai-mlflow-ebw2ryzjkq-ue.a.run.app) |
| Data Pipelines | Active | Airflow / Postgres | [http://34.26.252.164:8080](http://34.26.252.164:8080/) |
| Observability | Healthy | Grafana Cloud | [SavVio Monitoring Dashboard](https://nirajmehta2410.grafana.net/d/savvio-monitoring-v1/savvio-e28094-api-and-inference-monitoring) |

---

## Technical Implementation

### 1. Data Pipeline Orchestration
The data pipeline is managed by Apache Airflow running on GCE. It handles:
- Automated ingestion of financial and product datasets.
- Data cleaning and normalization using Spark and Pandas.
- Feature engineering for financial ratios and sentiment analysis of reviews.
- Data versioning with DVC and storage in Google Cloud SQL and GCS.

### 2. Model Development & Training
The platform uses a sophisticated ML pipeline:
- **XGBoost Classifier**: Trained on historic financial outcomes to predict purchase viability.
- **Optuna Optimization**: Automated hyperparameter tuning to ensure peak model performance.
- **Bias Detection**: Integrated metrics to detect and mitigate bias across demographic slices.
- **Experiment Tracking**: Full lineage and performance logging via MLflow.

### 3. Inference & Decision Logic
The system employs a three-layer decision engine to ensure fiduciary responsibility:
- **Deterministic Layer**: Authoritative financial rules that enforce strict budget and emergency fund guardrails.
- **ML Layer**: Probabilistic scoring that identifies risk patterns in consumer behavior.
- **Generative Layer**: Google Gemini-powered conversational explanations, validated by NVIDIA NeMo Guardrails.

### 4. Monitoring & Observability
Continuous monitoring ensures the system remains reliable and accurate:
- **System Metrics**: Real-time tracking of API latency and throughput via Prometheus and Grafana.
- **Model Drift**: Drift detection using Evidently AI to identify performance decay.
- **Data Quality**: Schema and anomaly monitoring via Great Expectations.

## Project Structure

```
SavVio/
├── data_pipeline/              # Data ingestion and preprocessing
├── model_pipeline/             # ML model training and evaluation
├── deployment_pipeline/                 # Inference API, Frontend, and Monitoring
│   ├── api/                    # FastAPI service
│   ├── frontend/               # React web application
│   ├── monitoring/             # Prometheus and Grafana configuration
├── infrastructure/             # Terraform and GCP configuration
└── .github/workflows/          # CI/CD pipeline definitions
```

## Technology Stack

| Category | Tools Used |
|----------|-----------|
| Cloud Platform | Google Cloud Platform (GCP) |
| Infrastructure | Terraform, Docker |
| Backend | FastAPI (Python) |
| Frontend | React, Vite, Tailwind CSS |
| Orchestration | Apache Airflow |
| ML Frameworks | XGBoost, Scikit-learn, Optuna |
| Observability | Prometheus, Grafana, Evidently AI |
| Database | PostgreSQL (Cloud SQL) |
| Versioning | Git, DVC |

## Local Setup

### 1. Clone the Repository
```bash
git clone https://github.com/nirajmehta960/SavVio.git
cd SavVio
```

### 2. Run Backend API
```bash
cd deployment_pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh api
```

### 3. Run Frontend
```bash
cd deployment_pipeline/frontend
npm install
npm run dev
```

---
**Note**: This is an academic project. This README reflects the technical work completed and will be updated with architectural diagrams in the future.
