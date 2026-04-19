# SavVio - Data Pipeline Phase
**Team Members:** Murtaza Nipplewala, Niraj Mehta, Wen-Hsin Su, Pranathi Bombay, Rishabh Joshi, Sanjana Patnam

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     SavVio Data Pipeline                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. SETUP                                                       │
│     └── Environment, Docker, Git, DVC init                      │
│                        ↓                                        │
│  2. DATA COLLECTION (Planning)                                  │
│     └── Identify sources, document requirements, privacy        │
│                        ↓                                        │
│  3. DATA INGESTION                                              │
│     ├── Load Financial data                                     │
│     ├── Load Product data                    [parallel]         │
│     └── Load Review data                     [parallel]         │
│                        ↓                                        │
│  4. VERSION RAW DATA (DVC Checkpoint #1)                        │
│     └── dvc add data/raw/                                       │
│                        ↓                                        │
│  5. SCHEMA & STATISTICS GENERATION                              │
│     └── Define expected structure, compute baseline stats       │
│                        ↓                                        │
│  6. RAW DATA VALIDATION                                         │
│     └── Validate raw data against schema                        │
│                        ↓                                        │
│  7. ANOMALY DETECTION & ALERTS                                  │
│     └── Detect outliers, missing values, trigger alerts         │
│                        ↓                                        │
│  8. DATA PREPROCESSING                                          │
│     └── Clean, transform, standardize                           │
│                        ↓                                        │
│  9. PROCESSED DATA VALIDATION                                   │
│     └── Validate preprocessing didn't break data                │
│                        ↓                                        │
│  10. VERSION PROCESSED DATA (DVC Checkpoint #2)                 │
│      └── dvc add data/processed/                                │
│                        ↓                                        │
│  11. FEATURE ENGINEERING                                        │
│      └── Create derived features (RUS, affordability, sentiment)│
│                        ↓                                        │
│  12. FEATURE VALIDATION                                         │
│      └── Validate feature calculations and ranges               │
│                        ↓                                        │
│  13. VERSION FEATURES (DVC Checkpoint #3)                       │
│      └── dvc add data/features/ (features.dvc)                 │
│                        ↓                                        │
│  14. LOAD TO DATABASE                                           │
│      ├── PostgreSQL (financial, product, review data)           │
│      └── pgvector (product embeddings for RAG)                  │
│                        ↓                                        │
│  15. BIAS DETECTION & MITIGATION                                │
│      └── Slice analysis on features, fairness checks            │
│                        ↓                                        │
│  16. PIPELINE ORCHESTRATION (Airflow DAG)                       │
│      └── Connect all tasks, set dependencies                    │
│                        ↓                                        │
│  17. TESTING                                                    │
│      └── Unit tests for each component                          │
│                        ↓                                        │
│  18. TRACKING, LOGGING & MONITORING                             │
│      └── Logging, metrics, dashboards                           │
│                        ↓                                        │
│  19. PIPELINE OPTIMIZATION                                      │
│      └── Gantt analysis, parallelization                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Reference: Tools by Phase

| Phase                | Primary Tools                 | Alternatives                            | Airflow Integration            |
| -------------------- | ----------------------------- | --------------------------------------- | ------------------------------ |
| Setup                | Docker, Git, DVC              | —                                       | —                              |
| Data Collection      | Documentation                 | —                                       | —                              |
| Ingestion            | Pandas, API clients           | Polars                                  | PythonOperator                 |
| Schema/Stats         | Great Expectations            | Pandera, ydata-profiling, custom Python | PythonOperator                 |
| Raw Validation       | Great Expectations            | Pandera, Pydantic, custom validators    | PythonOperator                 |
| Anomaly Detection    | Great Expectations, Evidently | Custom Python (IQR/z-score)             | PythonOperator + EmailOperator |
| Preprocessing        | Pandas, DuckDB                | Polars                                  | PythonOperator                 |
| Processed Validation | Great Expectations            | Pandera, custom validators              | PythonOperator                 |
| Features             | Pandas, NumPy                 | Polars                                  | PythonOperator                 |
| Feature Validation   | Great Expectations            | Pandera, custom validators              | PythonOperator                 |
| Versioning           | DVC, GCP Cloud Storage        | —                                       | BashOperator                   |
| Load to Database     | SQLAlchemy, psycopg2          | pandas.to_sql                           | PythonOperator                 |
| Embeddings           | Sentence-Transformers, OpenAI | LangChain embeddings                    | PythonOperator                 |
| Bias Detection       | Fairlearn                     | Custom Pandas slicing, AIF360           | PythonOperator                 |
| Orchestration        | Apache Airflow                | —                                       | Native                         |
| Testing              | pytest                        | unittest                                | —                              |
| Monitoring           | Python logging, Airflow UI    | GCP Cloud Logging, Grafana              | Native + EmailOperator         |
| Optimization         | Airflow Gantt                 | cProfile                                | Native                         |

---

## Table of Contents

1. [Phase 1: Project Setup & Environment Configuration](#phase-1-project-setup--environment-configuration)
2. [Phase 2: Data Collection & Planning](#phase-2-data-collection--planning)
3. [Phase 3: Data Ingestion](#phase-3-data-ingestion)
4. [Phase 4: Version Raw Data (DVC Checkpoint #1)](#phase-4-version-raw-data-dvc-checkpoint-1)
5. [Phase 5: Schema & Statistics Generation](#phase-5-schema--statistics-generation)
6. [Phase 6: Raw Data Validation](#phase-6-raw-data-validation)
7. [Phase 7: Anomaly Detection & Alerts](#phase-7-anomaly-detection--alerts)
8. [Phase 8: Data Preprocessing & Transformation](#phase-8-data-preprocessing--transformation)
9. [Phase 9: Processed Data Validation](#phase-9-processed-data-validation)
10. [Phase 10: Version Processed Data (DVC Checkpoint #2)](#phase-10-version-processed-data-dvc-checkpoint-2)
11. [Phase 11: Feature Engineering](#phase-11-feature-engineering)
12. [Phase 12: Feature Validation](#phase-12-feature-validation)
13. [Phase 13: Version Features (DVC Checkpoint #3)](#phase-13-version-features-dvc-checkpoint-3)
14. [Phase 14: Load to Database](#phase-14-load-to-database)
15. [Phase 15: Bias Detection & Mitigation](#phase-15-bias-detection--mitigation)
16. [Phase 16: Pipeline Orchestration (Airflow DAGs)](#phase-16-pipeline-orchestration-airflow-dags)
17. [Phase 17: Testing](#phase-17-testing)
18. [Phase 18: Tracking, Logging & Monitoring](#phase-18-tracking-logging--monitoring)
19. [Phase 19: Pipeline Optimization](#phase-19-pipeline-optimization)

---

## Phase 1: Project Setup & Environment Configuration

### Objective

Establish the foundational project structure, dependencies, and development environment before any data work begins.

### Steps

1. **Create folder structure** following the required format (actual implementation layout):

   ```
   SavVio/
   ├── data_pipeline/
   │   ├── README.md              # This file
   │   ├── SETUP_AND_RUN.md       # Setup and run instructions (reproducibility)
   │   ├── requirements.txt       # Python dependencies (or use repo root requirements.txt)
   │   ├── config/                # Configuration (Airflow, Token, GCP)
   │   ├── logs/                  # Pipeline execution logs
   │   ├── docker-compose.yaml    # Airflow + Postgres + Redis stack
   │   ├── Dockerfile             # Custom Airflow image (apache/airflow:3.1.7)
   │   ├── tests/                 # Unit tests (pytest)
   │   │   ├── conftest.py
   │   │   ├── test_requirements.txt
   │   │   ├── test_data_pipeline_airflow.py
   │   │   ├── test_incremental.py
   │   │   ├── ingestion/
   │   │   ├── preprocess/
   │   │   ├── features/
   │   │   ├── validation/
   │   │   ├── database/
   │   │   └── bias/
   │   └── dags/                  # Airflow DAG and pipeline code
   │       ├── data_pipeline_airflow.py   # Main DAG definition
   │       ├── data/
   │       │   ├── raw/           # Raw data (financial_data.csv, product_data.jsonl, review_data.jsonl)
   │       │   ├── processed/     # Preprocessed outputs (*_preprocessed.{csv|jsonl})
   │       │   ├── features/      # Feature-engineered outputs (*_featured.{csv|jsonl})
   │       │   ├── quarantine/    # Quarantined anomalies (auto-created at runtime)
   │       │   ├── raw.dvc        # DVC pointer for raw (versioned in Git)
   │       │   ├── processed.dvc  # DVC pointer for processed
   │       │   └── features.dvc   # DVC pointer for features
   │       └── src/
   │           ├── ingestion/     # Data acquisition (GCS, APIs)
   │           │   ├── __init__.py
   │           │   ├── api_loader.py
   │           │   ├── config.py
   │           │   ├── gcs_loader.py
   │           │   └── run_ingestion.py
   │           ├── preprocess/    # Cleaning and transformation
   │           │   ├── __init__.py
   │           │   ├── financial.py
   │           │   ├── product.py
   │           │   ├── review.py
   │           │   ├── run_preprocessing.py
   │           │   └── utils.py
   │           ├── features/      # Feature engineering
   │           │   ├── __init__.py
   │           │   ├── financial_features.py
   │           │   ├── product_review_features.py
   │           │   ├── run_features.py
   │           │   └── utils.py
   │           ├── validation/    # Schema, stats, anomaly checks (Great Expectations)
   │           │   ├── __init__.py
   │           │   ├── run_validation.py
   │           │   ├── validate/
   │           │   │   ├── __init__.py
   │           │   │   ├── raw_validator.py
   │           │   │   └── processed_validator.py
   │           │   └── anomaly/
   │           │       ├── __init__.py
   │           │       ├── anomaly_validator.py
   │           │       └── detectors.py
   │           ├── database/      # PostgreSQL and pgvector load
   │           │   ├── __init__.py
   │           │   ├── run_database.py
   │           │   ├── upload_to_db.py     # SQLAlchemy bulk loaders
   │           │   └── vector_embed.py     # SentenceTransformer + pgvector inserts
   │           └── bias/          # Bias detection (data slicing)
   │               ├── __init__.py
   │               ├── financial_bias.py
   │               ├── product_bias.py
   │               ├── review_bias.py
   │               ├── run_bias.py
   │               └── utils.py
   ```

   > **Note:** Database connection helpers (`db_connection.py`, `db_schema.py`) and the validation expectation suites (`validation_config.py`, `feature_validator.py`) live in the shared `savviocore/` package at the repo root, not under `data_pipeline/dags/src/`. Both packages are mounted into the Airflow image via `docker-compose.yaml`.

2. **Configure Docker environment for Airflow**
   - Use official Apache Airflow Docker Compose setup 
   - Follow SETUP_AND_RUN

3. **Initialize version control**
   - Git repository setup
   - Create `.gitignore` (exclude data files, logs, credentials, `__pycache__`)
   - Initialize DVC: `dvc init`
   - Configure DVC remote: `dvc remote add -d gcs gs://savvio-data-bucket/dvcstore`
   - Authenticate DVC against GCS via the standard `GOOGLE_APPLICATION_CREDENTIALS` env var (see Phase 4 for details). `.dvc/config` intentionally does **not** hard-code a `credentialpath`, so the repo is portable across machines and CI.

4. **Configure ENV and database connections**
   - Copy `.env.example` to `.env`
   - Follow SETUP_AND_RUN to configure ENV and database connections
   - Local (Development): Local PostgreSQL instance
   - Cloud (Production): GCP Cloud SQL for PostgreSQL


---

## Phase 2: Data Collection & Planning

### Objective

Identify, understand, and document the data sources needed for SavVio's purchase guardrail functionality. This phase is about planning and documentation before actual data ingestion.

### Steps

1. **Translate user needs into data needs**
   - Users: Consumers making purchase decisions
   - User Need: Make informed, responsible purchase decisions
   - System Need: Financial health data + Product information + Product reviews

2. **Document data privacy measures**
   - Masking/hashing user identifiers
   - Encryption for financial snapshots
   - Read-only access (no transactional capabilities)
   - Compliance with data privacy principles
   - Anonymized review data (no PII)

3. **Create Data Card documentation**

   **Financial Data (from Kaggle):**
   | Field | Description |
   |-------|-------------|
   | user_id | Unique user identifier |
   | age | Age of individual |
   | gender | Gender |
   | education_level | Highest education level |
   | employment_status | Employment type |
   | job_title | Job title or role |
   | monthly_income_usd | Approx. monthly income in USD |
   | monthly_expenses_usd | Approx. monthly expenses in USD |
   | savings_usd | Total savings |
   | has_loan | Whether individual has a loan |
   | loan_type | Type of loan |
   | loan_amount_usd | Loan principal amount |
   | loan_term_months | Duration of loan |
   | monthly_emi_usd | Monthly installment (EMI) |
   | loan_interest_rate_pct | Interest rate on loan (%) |
   | debt_to_income_ratio | Ratio of debt payments to income |
   | credit_score | Synthetic credit score |
   | savings_to_income_ratio | Ratio of savings to annual income |
   | region | Geographic region |
   | record_date | Record creation date |

   **Product Data (from Amazon Reviews'23):**
   | Field | Description |
   |-------|-------------|
   | main_category | Main category of the product |
   | title | Name of the product |
   | average_rating | Rating of the product |
   | rating_number | Number of ratings |
   | features | Bullet-point features |
   | description | Description of product |
   | price | Price in USD |
   | images | Product images |
   | videos | Product videos |
   | store | Store name |
   | categories | Hierarchical categories |
   | details | Product details |
   | parent_asin | Parent ID of product |
   | bought_together | Recommended bundles |

   **Review Data (from Amazon Reviews'23):**
   | Field | Description |
   |-------|-------------|
   | rating | Rating of the product (1-5) |
   | title | Title of the review |
   | text | Text body of review |
   | images | Images posted by user |
   | asin | ID of the product |
   | parent_asin | Parent ID of the product |
   | user_id | ID of reviewer |
   | timestamp | Time of review |
   | verified_purchase | User purchase verification |
   | helpful_vote | Helpful votes |




---

## Phase 3: Data Ingestion

### Objective

Download data from external sources and load it into the pipeline system in a consistent, reproducible manner.

### Steps

1. **Implement shared utilities** (`src/ingestion/config.py`, `src/ingestion/api_loader.py`)
   - Logging, config variables, API wrapper functions.

2. **Implement generic loaders** (`src/ingestion/gcs_loader.py`)
   - `download_from_gcs()` — Fetches files from cloud storage.
   - Saves to `data/raw/` path based on provided keys.

3. **Run ingestion pipeline** (`src/ingestion/run_ingestion.py`)
   - Connects to sources (GCS, APIs) sequentially or in parallel.
   - Fetches and stores raw data.

4. **Store raw data in original format**
   - Save to `data/raw/financial_data.csv`
   - Save to `data/raw/product_data.jsonl`
   - Save to `data/raw/review_data.jsonl`

5. **Log ingestion metadata**
   - Timestamp, record counts, columns, errors (logged via `src/utils.py` shared logger).
   - Last verified end-to-end run: **32,424 financial / 94,327 products / 2,128,605 reviews** loaded from GCS in ~20 s.

---

## Phase 4: Version Raw Data (DVC Checkpoint #1)

### Objective

Version control the raw ingested data to ensure reproducibility and enable rollback if needed. Data is versioned using DVC with separate `.dvc` pointer files for raw, processed, and features (history maintained in Git).

### Steps

1. **Authenticate to the GCS DVC remote** (one-time per machine)

   `.dvc/config` no longer hard-codes a `credentialpath` (that path was a stale, machine-specific absolute path). DVC reads Google credentials from the standard `GOOGLE_APPLICATION_CREDENTIALS` env var, so the config is portable across machines and CI.

   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/data_pipeline/config/savvio-gcp-key.json"
   ```

   Per-machine override (without polluting the shared config):
   ```bash
   dvc remote modify --local gcs credentialpath /abs/path/to/key.json
   ```

2. **Add raw data to DVC tracking** (run from `data_pipeline/dags/data`)

   ```bash
   cd data_pipeline/dags/data
   dvc add raw
   ```

   This creates `raw.dvc` (and updates `.gitignore` for the raw directory).

3. **Commit .dvc files to Git**

   ```bash
   git add raw.dvc
   git commit -m "Add raw data v1.0"
   ```

4. **Push data to remote storage**

   ```bash
   dvc push
   ```

5. **Pull data on a fresh checkout**

   ```bash
   dvc pull            # restores raw/processed/features into data_pipeline/dags/data/
   ```

6. **Tag the version**
   ```bash
   git tag -a "data-raw-v1.0" -m "Initial raw data ingestion"
   ```


### Why Version Here?

- Captures original data before any modifications
- Enables rollback if preprocessing introduces errors
- Provides "source of truth" for debugging

---

## Phase 5: Data Schema & Statistics Generation

### Objective

Define the expected structure (data schema) of your datasets and compute baseline statistics.

> **Note:** "Data Schema" = expected structure/rules for a dataset (columns, types, constraints).  
> "Database Schema" = tables, columns, relationships in a database (covered in Phase 14).

### Steps

1. **Define data schema (expected structure)**

   **Financial Data Schema:**
   | Column | Type | Nullable | Constraints |
   |--------|------|----------|-------------|
   | monthly_income | float | No | >= 0 |
   | rent | float | Yes | >= 0 |
   | recurring_bills | float | Yes | >= 0 |
   | savings_balance | float | Yes | >= 0 |
   | debt_obligations | float | Yes | >= 0 |

   **Product Data Schema:**
   | Column | Type | Nullable | Constraints |
   |--------|------|----------|-------------|
   | product_name | string | No | non-empty |
   | category | string | No | valid category list |
   | price | float | No | > 0 |
   | specifications | string | Yes | — |
   | description | string | Yes | — |

   **Review Data Schema:**
   | Column | Type | Nullable | Constraints |
   |--------|------|----------|-------------|
   | product_id | integer/string | No | foreign key valid |
   | reviewer_id | string | No | non-empty |
   | rating | float | No | 1-5 range |
   | text | string | Yes | — |
   | helpful_count | integer | Yes | >= 0 |
   | date | timestamp | Yes | valid date |

2. **Compute baseline statistics**
   - Numeric: min, max, mean, median, std, percentiles
   - Categorical: unique values, frequency distribution
   - All columns: null percentage, data type distribution

3. **Create expectation suites (Great Expectations)**
   - `expect_column_to_exist`
   - `expect_column_values_to_be_of_type`
   - `expect_column_values_to_not_be_null`
   - `expect_column_values_to_be_between`
   - `expect_column_values_to_be_in_set`


---

## Phase 6: Raw Data Validation

### Objective

Validate raw ingested data against the schema defined in Phase 5 to catch ingestion errors early.

### Steps

1. **Run validation checkpoint on raw data**
   - Load expectation suite
   - Execute against `data/raw/` files
   - Generate validation results

2. **Validation rules**

   **Financial Data:**
   - All expected columns exist
   - Data types are correct
   - Required fields not null
   - Values within expected ranges

   **Product Data:**
   - All expected columns exist
   - Price is positive
   - Product name not empty

   **Review Data:**
   - All expected columns exist
   - Rating between 1-5
   - Product ID references valid product
   - Reviewer ID not empty

3. **Handle validation failures**
   - Log errors with details
   - Quarantine invalid records to `data/quarantine/`
   - Generate HTML report (Great Expectations Data Docs)
   - Critical failures halt pipeline

4. **Store results**
   - `logs/validation/raw_validation_YYYYMMDD.json`


---

## Phase 7: Anomaly Detection & Alerts

### Objective

Detect data anomalies (outliers, suspicious patterns) and trigger alerts when issues are found.

### Steps

1. **Define anomaly rules**

   **Financial Data:**
   | Anomaly | Detection Method |
   |---------|------------------|
   | Income = 0 | Rule-based |
   | Expenses > 2x income | Rule-based |
   | Negative monetary values | Rule-based |
   | Extreme outliers | IQR or z-score |

   **Product Data:**
   | Anomaly | Detection Method |
   |---------|------------------|
   | Price <= 0 | Rule-based |
   | Price > $100,000 | Threshold |
   | Missing name with valid price | Rule-based |

   **Review Data:**
   | Anomaly | Detection Method |
   |---------|------------------|
   | Rating outside 1-5 | Rule-based |
   | Product ID doesn't exist | Referential check |
   | Duplicate reviews | Deduplication |
   | Extreme helpful counts | Outlier detection |

2. **Implement detection**
   - Statistical: IQR method, z-score
   - Rule-based: business logic checks

3. **Configure alerts**

   | Severity | Condition      | Action          |
   | -------- | -------------- | --------------- |
   | INFO     | Minor outliers | Log only        |
   | WARNING  | >5% issues     | Email alert     |
   | CRITICAL | Data corrupted | Email + halt    |

   - Implementation: `EmailOperator` per stage in `dags/data_pipeline_airflow.py`. SMTP credentials are read from `SMTP_USER` / `SMTP_PASSWORD` in `.env` and wired into the Airflow connection `smtp_default` automatically by the docker-compose env (`AIRFLOW_CONN_SMTP_DEFAULT`).
   - Tier-1 (raw) anomaly checks log INFO only and never halt the DAG; Tier-2 (featured, pre-DB) checks gate the DB load and trigger the email alert on WARNING/CRITICAL.

4. **Quarantine suspicious records**
   - Suspicious records are written to `data/quarantine/<dataset>_anomalies_<timestamp>.json` (one JSON-lines file per detection run) by `dags/src/validation/anomaly/anomaly_validator.py::_quarantine_records`.


---

## Phase 8: Data Preprocessing & Transformation

### Objective

Clean, transform, and standardize validated data into a consistent format ready for feature engineering.

### Steps

1. **Data cleaning**

   **Financial Data:**
   - Handle missing values (impute median or flag)
   - Standardize transaction categories
   - Remove duplicates

   **Product Data:**
   - Flatten nested JSONL objects if present
   - Remove duplicates
   - Standardize price format
   - Clean product names

   **Review Data:**
   - Flatten nested JSONL objects if present
   - Remove duplicate reviews
   - Clean text (trim whitespace, handle special chars)
   - Validate ratings are in 1-5 range
   - Handle missing helpful_count

2. **Data transformation**

   **Financial Data:**
   - Convert to monthly format (annual ÷ 12, weekly × 4.33)
   - Categorize expenses
   - Calculate total fixed expenses

   **Product Data:**
   - Standardize category names
   - Clean descriptions for embedding

   **Review Data:**
   - Normalize text for processing
   - Aggregate ratings by product

3. **Incremental Merging (Out-of-Core)**
   - To support continuous daily ingestion without blowing up Memory, new batched records are merged with the existing historical dataset using **DuckDB**.
   - Records with matching keys (`asin`, `user_id`) in both files are updated/replaced.
   - New records are appended. 
   - Operations performed entirely out-of-core utilizing `/tmp` disk spilling to stay within strict Docker RAM limits.

4. **Save processed data**
   - `data/processed/financial_preprocessed.csv`
   - `data/processed/product_preprocessed.jsonl`
   - `data/processed/review_preprocessed.jsonl`

   Last verified end-to-end run:
   - Financial: 32,424 rows (no records dropped)
   - Products: 94,327 rows; **47,601 missing prices imputed** (title-group median → category median → global median fallback)
   - Reviews: 2,128,605 → **2,105,948 rows** (22,657 duplicates removed by `(asin, user_id)`); `timestamp` and `images` dropped as non-useful for embeddings/sentiment


---

## Phase 9: Processed Data Validation

### Objective

Validate that preprocessing transformations didn't break the data or introduce errors.

### Steps

1. **Define processed data expectations**
   - All original records accounted for (minus quarantined)
   - No new null values introduced
   - Transformations applied correctly (e.g., monthly format)
   - No duplicate records

2. **Run validation checkpoint**
   - Validate `data/processed/` files
   - Compare record counts: raw vs processed
   - Verify transformation logic

3. **Specific checks for SavVio**
   - Financial values in monthly format
   - Expense categories standardized
   - Product prices positive and reasonable
   - Review ratings normalized and valid

4. **Handle failures**
   - Log transformation errors
   - Alert if significant data loss
   - Option to rollback to raw data


---

## Phase 10: Version Processed Data (DVC Checkpoint #2)

### Objective

Version control the processed data to track transformations.

### Steps

1. **Add processed data to DVC** (run from `data_pipeline/dags/data`)

   ```bash
   cd data_pipeline/dags/data
   dvc add processed
   ```

   This creates/updates `processed.dvc`.

2. **Commit and push**

   ```bash
   git add processed.dvc
   git commit -m "Add processed data v1.0"
   dvc push
   ```

3. **Tag the version**
   ```bash
   git tag -a "data-processed-v1.0" -m "Processed data"
   ```

### Why Version Here?

- Captures cleaned data before feature engineering
- If features have bugs, no need to re-preprocess
- Enables raw vs processed comparison

---

## Phase 11: Feature Engineering

### Objective

Create meaningful features within each data track — financial health features per user and quality features per product. Affordability metrics (which require both user and product data) are computed at inference time by the Deterministic Financial Logic Engine, not pre-computed in the pipeline.

### Steps

1. **Financial health features** (`financial_features.py`)

   Input: `data/processed/financial_preprocessed.csv`
   Output: `data/features/financial_featured.csv`

   | Feature                        | Formula                    | Purpose                           |
   | ------------------------------ | -------------------------- | --------------------------------- |
   | `discretionary_income`         | income - (expenses + emi)  | Available money after obligations |
   | `debt_to_income_ratio`         | emi / income               | Debt burden indicator             |
   | `savings_to_income_ratio`      | savings / income           | Savings health indicator          |
   | `monthly_expense_burden_ratio` | (expenses + emi) / income  | Spending pattern                  |
   | `emergency_fund_months`        | savings / (expenses + emi) | Safety buffer in months           |

2. **Product quality features** (`product_review_features.py`)

   Input: `data/processed/review_preprocessed.jsonl`, `data/processed/product_preprocessed.jsonl`
   Output: `data/features/product_featured.jsonl` (products enriched with `rating_variance`), `data/features/review_featured.jsonl` (pass-through copy)

   | Feature           | Formula                 | Purpose                 |
   | ----------------- | ----------------------- | ----------------------- |
   | `rating_variance` | std(rating) per product | Rating consensus signal |

   > **Note:** `average_rating` and `rating_number` (equivalent to `num_reviews`) already exist in the product metadata. The only feature requiring individual review data is `rating_variance`, which measures how polarized opinions are about a product.

3. **Handle edge cases**
   - Zero income: ratios set to NaN (XGBoost handles natively)
   - Division by zero: safe handling with NaN defaults
   - Single-review products: rating_variance defaults to 0.0

4. **Outputs**
   - `data/features/financial_featured.csv` — Financial profiles enriched with health metrics
   - `data/features/product_featured.jsonl` — Product-level features
   - `data/features/review_featured.jsonl` — Review-level features

---

## Phase 12: Feature Validation

### Objective

Validate that engineered features are correctly calculated and within expected ranges.

### Steps

1. **Define feature expectations**

   **Financial Features:**
   | Feature | Expected Range | Validation |
   |---------|---------------|------------|
   | `discretionary_income` | Can be negative | Check not NaN where income > 0 |
   | `debt_to_income_ratio` | 0 to ~2 | Flag if > 2; NaN only when income = 0 |
   | `savings_to_income_ratio` | 0 to ~1 | Flag if > 1; NaN only when income = 0 |
   | `monthly_expense_burden_ratio` | 0 to ~1 | Flag if > 1 |
   | `emergency_fund_months` | >= 0 | NaN only when obligations = 0 |

   **Product Quality Features:**
   | Feature | Expected Range | Validation |
   |---------|---------------|------------|
   | `rating_variance` | >= 0 | Check not negative; 0.0 for single-review products |

2. **Run validation**
   - No unexpected NaN or Inf values
   - Ratios within reasonable bounds
   - All expected features present
   - Product count in variance output matches product count in reviews

3. **Cross-validate calculations**
   - Sample records: manually verify formulas
   - Edge cases: zero income, single review products

4. **Handle failures**
   - Log calculation errors
   - Identify problematic source records


---

## Phase 13: Version Features (DVC Checkpoint #3)

### Objective

Version control the feature-engineered data for reproducibility.

### Steps

1. **Add features to DVC** (run from `data_pipeline/dags/data`)

   ```bash
   cd data_pipeline/dags/data
   dvc add features
   ```

   This creates/updates `features.dvc`.

2. **Commit and push**

   ```bash
   git add features.dvc
   git commit -m "Add features v1.0"
   dvc push
   ```

3. **Tag the version** (optional)
   ```bash
   git tag -a "data-features-v1.0" -m "Feature-engineered data"
   ```

### Why Version Here?

- Ties financial health features to model training versions
- Enables comparison of feature distributions across pipeline runs
- Experiment reproducibility

---

## Phase 14: Load to Database

### Objective

Load processed data, engineered features, and product embeddings into PostgreSQL (relational) and pgvector (vector) databases for the SavVio application.

### Environment Configuration

| Environment | Database                           | Connection      |
| ----------- | ---------------------------------- | --------------- |
| Development | Local PostgreSQL + pgvector        | localhost:5432  |
| Production  | GCP Cloud SQL + pgvector extension | Cloud SQL proxy |

### Steps

1. **Identify datasets to load**
   - `data/features/financial_featured.csv` — Financial profiles with health metrics
   - `data/processed/product_preprocessed.jsonl` — Product catalog
   - `data/features/product_featured.jsonl` — Rating variance per product (merged onto products during load)
   - `data/processed/review_preprocessed.jsonl` — Individual reviews

2. **Define database schema (tables)**

   - PostgreSQL Tables: Schema defined in `savviocore/database/db_schema.py` (shared with `model_pipeline`).
   - pgvector Tables: Created on-demand by `src/database/vector_embed.py::_ensure_embedding_tables` using the `vector` extension (auto-created via `ensure_pgvector(engine)`).

3. **Implement data loaders**

   **`src/database/upload_to_db.py`** (pandas → SQLAlchemy bulk load):
   - `load_financial(engine, path)` — Loads `data/features/financial_featured.csv` into `financial_profiles`.
   - `load_products(engine, path)` — Loads `data/features/product_featured.jsonl` (rating_variance already merged in Phase 11) into `products`.
   - `load_reviews(engine, path)` — Loads `data/features/review_featured.jsonl` into `reviews`.
   - `load_all(engine, data_dir)` — Convenience wrapper that runs all three loaders.

   **`src/database/vector_embed.py`** (embeddings → pgvector):
   - `load_model()` — Loads `sentence-transformers/all-MiniLM-L6-v2` (384-dim).
   - `embed_products(engine, path, model)` — Embeds product titles + descriptions, writes to `product_embeddings`.
   - `embed_reviews(engine, path, model)` — Embeds review text, writes to `review_embeddings`.

   **`savviocore/database/db_connection.py`** (shared connection helpers):
   - `get_engine()` — Reads `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` from env and returns a SQLAlchemy `Engine`.
   - `ensure_pgvector(engine)` — Idempotent `CREATE EXTENSION IF NOT EXISTS vector`.

4. **Environment-based configuration**

   No YAML config file is used. The active database is selected purely by `.env` values:

   | Variable      | Local Dev (Mac + Docker)   | Production (Cloud SQL)               |
   | ------------- | -------------------------- | ------------------------------------ |
   | `DB_HOST`     | `host.docker.internal`     | Cloud SQL private IP / `/cloudsql/…` |
   | `DB_PORT`     | `5432`                     | `5432`                               |
   | `DB_NAME`     | e.g. `savvio_dev`          | e.g. `savvio_prod`                   |
   | `DB_USER`     | local pg user              | Cloud SQL user (Secret Manager)      |
   | `DB_PASSWORD` | local pg password          | Cloud SQL password (Secret Manager)  |

   `docker-compose.yaml` overrides `DB_HOST=host.docker.internal` and `DB_PORT=5432` for the Airflow services so pipeline tasks can reach the host's Postgres on macOS regardless of the user's `.env`.

5. **Load data**
   - `setup_database_task` calls `create_tables(engine)` (idempotent — `CREATE TABLE IF NOT EXISTS`).
   - `load_financial_profiles` and `load_products` run in parallel.
   - `load_reviews` runs after products to satisfy the `parent_asin` FK.
   - `generate_and_load_embedding_task` (currently commented out in the DAG; available as a manual callable in `run_database.py`) embeds products and reviews and writes them to pgvector.



---

## Phase 15: Bias Detection & Mitigation

### Objective

Detect and mitigate data representation bias across financial profiles and product/review data. The goal is to ensure the pipeline produces balanced, representative data so downstream models and the decision engine don't systematically disadvantage any subgroup.

### Why Two Separate Tracks?

Each data track is analyzed independently because they represent fundamentally different populations (users vs. products) and carry different bias risks:

| Track          | Population         | Bias Risk                                                                                         |
| -------------- | ------------------ | ------------------------------------------------------------------------------------------------- |
| Financial      | User profiles      | Underrepresentation of financially vulnerable users — the people SavVio is designed to help most  |
| Product/Review | Products & reviews | Skewed category coverage, price range gaps, or unreliable quality signals for low-review products |

> **Note:** Decision outcome bias (e.g., does the Green/Yellow/Red recommendation system unfairly penalize low-income users?) is tested in Phase 3: Model Development, once the Deterministic Financial Logic Engine and affordability calculations exist. The data pipeline focuses on **data representation bias** only.

### Steps

1. **Financial data — Slice analysis**

   | Slice Dimension       | Groups                                    | What to Check                           |
   | --------------------- | ----------------------------------------- | --------------------------------------- |
   | Income bracket        | Low (<$3k), Medium ($3k-$7k), High (>$7k) | Sufficient low-income representation?   |
   | Debt-to-income ratio  | Low (<0.2), Medium (0.2-0.4), High (>0.4) | Balanced coverage across debt levels?   |
   | Expense burden        | Low (<0.5), Medium (0.5-0.8), High (>0.8) | Are high-burden users underrepresented? |
   | Emergency fund months | Critical (<1), Low (1-3), Healthy (3+)    | Enough financially stressed profiles?   |

   **Analysis:**
   - Count records per slice — flag slices with <10% of total records
   - Compare feature distributions across slices (mean, median, std of each financial metric)
   - Identify if any slice has significantly different feature distributions that could bias model training

   **Why this matters for SavVio:**
   If the financial data skews toward high-income, healthy profiles, the model won't learn effective decision boundaries for users who are financially vulnerable. These are the users most likely to benefit from a "Red Light" recommendation, and the system must work well for them.

2. **Product/review data — Slice analysis**

   | Slice Dimension               | Groups                                         | What to Check                                         |
   | ----------------------------- | ---------------------------------------------- | ----------------------------------------------------- |
   | Product category              | Electronics, Clothing, Home, etc.              | Any category with <5% of products?                    |
   | Price range                   | Budget (<$25), Mid ($25-$200), Premium (>$200) | Balanced price representation?                        |
   | Average rating                | Low (<3), Medium (3-4), High (>4)              | Are low-rated products underrepresented?              |
   | Review volume (rating_number) | Few (<10), Some (10-100), Many (>100)          | Do low-review products lack reliable quality signals? |
   | Rating variance               | Low (<0.5), Medium (0.5-1.0), High (>1.0)      | Are polarizing products represented?                  |

   **Analysis:**
   - Count products per slice — flag underrepresented groups
   - Check if `rating_variance` is meaningful for low-review products (variance from 2 reviews is unreliable)
   - Verify price distribution covers the range users are likely to query about

   **Why this matters for SavVio:**
   If the product data is dominated by one category (e.g., electronics), the RAG retrieval and quality signals will perform poorly for other categories. If budget products are underrepresented, the system may lack good alternatives to recommend when giving a "Yellow Light."

3. **Evaluate for bias**
   - Are any critical slices underrepresented (<10% of records)?
   - Would the data gaps cause the system to perform worse for specific user groups?
   - Are there product categories where quality signals (rating_variance, avg_rating) are unreliable due to low review counts?

4. **Implement mitigation if needed**

   **Financial data:**
   - Oversample underrepresented income brackets or debt levels
   - Generate synthetic profiles for underrepresented slices
   - Document which slices are underrepresented and the expected impact

   **Product/review data:**
   - Flag products with fewer than N reviews as having low-confidence quality signals
   - Ensure category distribution covers common purchase types
   - Document category gaps and their impact on recommendations

5. **Document analysis**
   - The following subsection summarizes slices used, bias found, and mitigation strategies (aligned with the team’s Bias Detection document). Raw datasets are not modified; mitigation is applied at **model training time only**.

---

### Bias Detection Report (Phase 15 Summary)

This section describes the **slices used**, **bias found**, and **mitigation strategies** from representation bias analysis across Financial (17 columns), Product (10 columns), and Review (8 columns) datasets. The objective is to identify underrepresented high-risk financial groups and high-uncertainty product/review slices that could skew downstream affordability and recommendation models.

#### Slices Used for Bias Detection

**Financial (domain-informed risk bands)**

| Dimension                                                        | Bands / Logic                                                   | Threshold (flagged if) |
| ---------------------------------------------------------------- | --------------------------------------------------------------- | ---------------------- |
| Discretionary income                                             | Negative (&lt;0), Tight (0–1000), Comfortable (&gt;1000)        | —                      |
| Debt-to-income (DTI)                                             | Safe (&lt;0.2), Warning (0.2–0.4), Risky (&gt;0.4)              | Warning &lt;10%        |
| Savings-to-income                                                | Fragile (&lt;0.25), Moderate (0.25–1.0), Strong (&gt;1.0)       | Fragile &lt;10%        |
| Monthly expense burden                                           | Comfortable (&lt;0.5), Tight (0.5–0.8), Overstretched (&gt;0.8) | —                      |
| Emergency fund months                                            | Quantile bands (Q1–Q4) + outliers                               | —                      |
| Income / expenses / loan amount / interest / term / credit score | Low, Medium, High (quantiles or domain bins)                    | High-risk band &lt;10% |
| Employment status                                                | Employed, Self-employed, Unemployed, Student                    | Category &lt;10%       |
| Region                                                           | Geographic categories                                           | —                      |
| user_id                                                          | Uniqueness check                                                | Uniqueness &lt;95%     |
| savings_balance                                                  | Near-zero, Low, Moderate, High                                  | Near-zero &lt;10%      |

**Product (uncertainty and coverage bands)**

| Dimension              | Bands / Logic                                          | Threshold (flagged if) |
| ---------------------- | ------------------------------------------------------ | ---------------------- |
| Price                  | Budget, Mid-range, Premium                             | —                      |
| Average rating         | Low, Medium, High                                      | —                      |
| rating_number          | Low / Medium / High confidence (review count)          | —                      |
| rating_variance        | Consensus, Mixed, Polarized, Single-review proxy (0.0) | —                      |
| Description / features | Length or count bands (0, 1–2, 3–5, 6+)                | —                      |
| details (Brand)        | Rare-brand detection                                   | Rare brands &lt;5%     |
| category               | Long-tail category coverage                            | Category &lt;5%        |
| product_id             | Uniqueness                                             | —                      |

**Review (signal and coverage bands)**

| Dimension                  | Bands / Logic                  | Threshold (flagged if)     |
| -------------------------- | ------------------------------ | -------------------------- |
| rating                     | Negative, Neutral, Positive    | Neutral or minority &lt;5% |
| verified_purchase          | True, False                    | Minority class &lt;5%      |
| helpful_vote               | None, Low, Medium, High        | High &lt;5%                |
| review_title / review_text | Short, Medium, Long, Empty     | —                          |
| user_id                    | Uniqueness                     | Uniqueness &lt;95%         |
| Reviews per product        | 1, 2–5, 6–20, 21+ (cold-start) | —                          |

---

#### Bias Found (Phase 15 Outputs)

**Financial (flagged)**

| Column / slice              | Finding                                                                 | Risk                                                                                    |
| --------------------------- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **savings_balance**         | Near-zero savings severely underrepresented (~0%)                       | Financial vulnerability under-captured; model may miss users most in need of Red Light. |
| **employment_status**       | Unemployed (9.93%) and Student (9.91%) slightly below 10%               | Financially vulnerable groups underrepresented.                                         |
| **debt_to_income_ratio**    | Warning band (0.2–0.4) = 3.48%                                          | Mid-risk users underrepresented; model may learn binary Safe vs Risky.                  |
| **savings_to_income_ratio** | Fragile (&lt;0.25) ~1.5%                                                | Long-term vulnerability underrepresented.                                               |
| **emergency_fund_months**   | Critical (&lt;1 month) and Fragile (1–3 months) highly underrepresented | Emergency-risk users rare; classifier may rarely predict Red in real distress cases.    |

**Review (flagged)**

| Column / slice        | Finding         | Risk                                   |
| --------------------- | --------------- | -------------------------------------- |
| **user_id**           | Uniqueness ~83% | Repeat reviewers may dominate signals. |
| **rating**            | Neutral = 4.89% | Middle sentiment underrepresented.     |
| **verified_purchase** | False ~4.16%    | Non-verified reviews underrepresented. |
| **helpful_vote**      | High = 1.5%     | High-signal reviews scarce.            |

**Product (flagged)**

| Column / slice              | Finding                                                                   | Risk                                                    |
| --------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------- |
| **category**                | Many long-tail categories &lt;5%                                          | Coverage skew toward popular categories; category bias. |
| **details (Brand)**         | Many rare brands &lt;5%                                                   | Model may overfit to dominant brands.                   |
| **Low-confidence products** | High share of single-review / low-count items (e.g. rating_variance == 0) | Cold-start risk; uncertainty at serving time.           |

---

#### Mitigation Strategies (Training-Time Only)

Mitigation is applied **only at model training time**. The raw dataset is not modified.

**Financial**

- **DTI Warning band:** Oversample Warning (0.2–0.4) during training; ensure moderate-debt users are sufficiently represented in the training split.
- **Savings-to-income (Fragile):** Oversample Fragile users; ensure minimum exposure of low-savings profiles; if needed, controlled synthetic low-savings profiles (clearly labeled).
- **Emergency fund (Critical / Fragile):** Oversample Critical and Fragile runway users; stress-test classifier on &lt;3 month runway users; optionally controlled synthetic emergency profiles. **Highest priority mitigation.**
- **Employment (Unemployed / Student):** Stratified sampling by employment_status so vulnerable groups are represented.

**Review**

- **Rating (Neutral):** Stratified sampling by sentiment class (Negative / Neutral / Positive).
- **verified_purchase (False):** Oversample non-verified reviews during training.
- **helpful_vote (High):** Weight high-helpfulness reviews more during training.
- **user_id (repeat reviewers):** User-level deduplication or per-user weighting to avoid repeat reviewers dominating.

**Product**

- **Category (long-tail):** Stratified sampling or category grouping to reduce bias toward popular categories.
- **Brand (rare):** Group rare brands or apply brand smoothing to avoid overfitting to major brands.
- **Low-confidence (single-review):** Flag as low-confidence at serving time; down-weight in recommendation logic; optionally require minimum review count.

**Cross-cutting**

- Stratified sampling across all flagged slices where applicable.
- Controlled oversampling of underrepresented high-risk slices (with caps to avoid distortion).
- Evaluation stress tests on vulnerable slices (low savings, DTI Warning, cold-start products).
- No synthetic data in raw pipeline unless model performance on vulnerable slices remains poor after oversampling.

---

#### Trade-offs and Design Rationale

| Choice                               | Rationale                                                                                                |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| **No raw data mutation**             | Preserves real-world distributions and avoids synthetic artifacts in source-of-truth datasets.           |
| **Mitigation at training time only** | Keeps the data pipeline deterministic and auditable; fairness controls are applied where models are fit. |
| **Oversampling with caps**           | Reduces underrepresentation while limiting overfitting to rare slices; validated via cross-validation.   |
| **Long-tail / category grouping**    | Improves fairness across categories but may increase variance; addressed with grouping and smoothing.    |

**Limitations and next steps**

- Incorporate fairness-aware sampling directly into model training pipelines (Phase 3).
- Add per-slice performance metrics (e.g. recall on vulnerable users).
- Consider post-training calibration and threshold tuning for high-risk (Red) decisions.

---



### Future: Decision Outcome Bias (In Model Development)

Once the Deterministic Financial Logic Engine is built, a separate bias analysis should test:

- Do Green/Yellow/Red recommendations distribute fairly across income brackets?
- Does the affordability score systematically penalize certain financial profiles?
- Are certain product categories more likely to receive Red Light recommendations regardless of user finances?

This analysis requires the full decision pipeline and belongs in the model development phase, not the data pipeline.

---

## Phase 16: Pipeline Orchestration (Airflow DAGs)

### Objective

Structure the entire pipeline using Apache Airflow DAGs with conditional branching to manage complex error-handling logic, parallel processing, and dependencies.

### Implementation Setup

1. **Architecture**
   - We run a containerized Airflow environment via `docker-compose.yaml` (including PostgreSQL and Redis backends).
   - Core DAG file: `dags/data_pipeline_airflow.py`

2. **Implemented DAG Structure**
   The DAG achieves maximum concurrency while respecting data dependencies and executing specific validation branches to catch failures. Each `check_*` branch routes to a stage-specific `EmailOperator` on failure (and on the final success). Here is the implemented structure:

   ```
   [ingest_financial] ───────┐
   [ingest_product]   ───────┼──> [check_ingestion] ──(failed?)──> [email_error_at_ingestion]
   [ingest_review]    ───────┘          │ (success)
                                        ▼
   [validate_raw_data, validate_raw_anomalies]
                                        │
                                        ▼
   [check_raw_validation] ──(failed?)──> [email_error_at_raw_validation]
                                        │ (success)
                                        ▼
   [preprocess_financial, preprocess_product, preprocess_review]   (parallel)
                                        │
                                        ▼
   [check_preprocessing] ──(failed?)──> [email_error_at_preprocessing]
                                        │
                                        ▼
   [validate_processed_data] ──> [check_processed_validation]
                                        │
                                        ▼ ──(failed?)──> [email_error_at_processed_validation]
                                        │ (success)
                                        ▼
   [feature_financial_data, feature_product_review_data]   (parallel)
                                        │
                                        ▼
   [check_feature_engineering] ──(failed?)──> [email_error_at_feature_engineering]
                                        │ (success)
                                        ▼
   [validate_featured_data] ──> [check_featured_validation]
                                        │
                                        ▼ ──(failed?)──> [email_error_at_featured_validation]
                                        │ (success)
                                        ▼
   [setup_database] ──> [load_financial_profiles, load_products] ──> [load_reviews]
                                        │
                                        ▼
   [bias_analysis_financial, bias_analysis_products, bias_analysis_reviews]   (parallel)
        │                                │
        │ (any failed, ONE_FAILED)       │ (all done)
        ▼                                ▼
   [email_error_at_bias_analysis]   [check_db_loading]
                                         │
                                         ├──(failed?)──> [email_error_at_DB_loading]
                                         │ (success)
                                         ▼
                              [email_pipeline_success]
                                         │
                                         ▼
                                  [pipeline_sentinel]
   ```

   Notes on the diagram:
   - Bias-stage failures (`bias_analysis_*`) trigger their own dedicated email alert via `TriggerRule.ONE_FAILED`, but they **do not block** the success email — `check_db_loading` only inspects `load_*` task states.
   - `pipeline_sentinel` runs with `TriggerRule.ALL_DONE` and fails the DAG run if `send_email_pipeline_success` did not succeed (i.e. the pipeline didn't fully complete).

3. **Task Implementation**
   - The DAG tasks primarily map directly to `src/` modules using `PythonOperator`.
   - Error detection is implemented via `BranchPythonOperator` blocks (e.g., `make_branch_check(...)`) that evaluate upstream task states and route to the per-stage email alert on failure or to the next stage on success. Branches use `TriggerRule.ALL_DONE` so they always run and can route to alerts even when upstream tasks fail.
   - Alerts are dispatched via `EmailOperator` (SMTP, configured via `SMTP_USER` / `SMTP_PASSWORD` in `.env`; the docker-compose env wires this into the Airflow connection `smtp_default` automatically through `AIRFLOW_CONN_SMTP_DEFAULT`).

---

## Phase 17: Testing

### Objective

Provide comprehensive coverage of all data modules and tasks before pipeline deployment using parameterized test files and robust service mocks.

### Layout

```
data_pipeline/tests/
├── conftest.py                          # Shared fixtures (sample dataframes, tmp paths)
├── test_requirements.txt                # Test-only deps (pytest, pytest-mock, etc.)
├── test_data_pipeline_airflow.py        # DAG import + structure + branching tests
├── test_incremental.py                  # DuckDB out-of-core merge regression tests
├── ingestion/                           # Tests for src/ingestion/* (gcs_loader, api_loader, run_ingestion)
├── preprocess/                          # Tests for src/preprocess/* (financial, product, review, utils)
├── features/                            # Tests for src/features/* (financial_features, product_review_features)
├── validation/                          # Tests for raw/processed/feature validators + anomaly detectors
├── database/                            # Tests for upload_to_db + vector_embed (mocked engine)
└── bias/                                # Tests for financial/product/review bias modules
```

### Implementation

1. **Testing Standards Applied**
   - **Mocking Extraneous Services**: Stubs are aggressively used to isolate module tests from active GCP, Postgres, or Airflow metadata connections (`_stub` patterns in `test_data_pipeline_airflow.py`, GCS client stubs in `tests/ingestion/`, mocked SQLAlchemy engines in `tests/database/`).
   - **Format Standard:** All test files include module docstrings, section dividers, and structural comments.
   - **Bias Detection Validation:** The bias detection modules (`financial_bias.py`, `product_bias.py`, `review_bias.py`, `run_bias.py`) are exercised end-to-end by running `python -m src.bias.run_bias` against the engineered datasets. The logged outputs and the "Bias Detection Report" section above form the test oracle for Phase 15.
   - **DAG Import Test**: `test_data_pipeline_airflow.py` parses `dags/data_pipeline_airflow.py` and asserts every expected task ID exists, including the `EmailOperator` alerts and the `pipeline_sentinel` (Slack tasks are intentionally not present since Slack was removed).

2. **Running the Tests**

   Tests are the only component **not** orchestrated via Docker Compose — they run against a local virtual environment.

   ```bash
   # From repo root
   python3 -m venv savvio_tests
   source savvio_tests/bin/activate
   pip install -e savviocore
   pip install -r data_pipeline/tests/test_requirements.txt

   # Run everything
   pytest data_pipeline/tests/ -v

   # Run a specific module
   pytest data_pipeline/tests/bias/ -v
   ```

---

## Phase 18: Tracking, Logging & Monitoring

### Objective

Ensure data pipeline execution observability.

### Implementation

1. **Module-wide Logging**
   - `src/utils.py` contains shared `logging` config that formats logs by `[timestamp] [level] [module_name]`.
   - Every individual script explicitly initializes its logger instance globally using `logger = logging.getLogger(__name__)`.
   - Airflow's task-level logs are persisted under `data_pipeline/logs/` and surfaced in the Airflow UI per task run.
2. **Airflow Alerts (Email)**
   - If any core task fails, branching conditions inside Airflow (`make_branch_check(...)`) dynamically evaluate the task context state and route to a stage-specific `EmailOperator` (e.g. `email_error_at_preprocessing`) that sends an HTML email to the on-call list.
   - Bias-stage failures use a separate `ONE_FAILED` alert (`email_error_at_bias_analysis`) that fires without blocking the success path.
   - On a clean run, `send_email_pipeline_success` confirms completion; the `pipeline_sentinel` task fails the DAG run if the success notification didn't fire.
3. **Connection Setup (one-time)**
   - **SMTP** — set `SMTP_USER` / `SMTP_PASSWORD` (Gmail App Password) in `.env`. The docker-compose env (`AIRFLOW_CONN_SMTP_DEFAULT`) wires these into the Airflow connection `smtp_default` automatically; no manual UI configuration is required.

---

## Phase 19: Pipeline Optimization

### Objective

Diagnose runtime bottlenecks and pipeline faults.

### Implementation

1. **DAG Race Condition Mitigation**
   - _Problem:_ A race condition dropped merged data due to overlapping run times in `preprocess_product` and `preprocess_review`.
   - _Solution:_ Airflow dependencies were explicitly enforced for these two segments `preprocess_product >> preprocess_review` to ensure product mapping succeeds before dependency resolution.

2. **DVC (Data Version Control) Checkpoints**
   - Intermediary DVC markers allow the pipeline data to be cached at raw, processed, and featured states without repeating slow upstream execution times. Data commits are persisted in GCS.

3. **Orchestration Concurrency**
   - Tasks like `ingest_financial`, `ingest_product`, and `ingest_review` execute totally asynchronously without blocking execution threads since they exist independently of one another.

---

## Appendix A: End-to-End Verification Run

The pipeline has been executed end-to-end both as an **orchestrated Airflow DAG** (via `docker compose up -d`) and as **standalone scripts** (each module's `__main__` block invoked directly inside the `airflow-worker` container with `python -m src.<phase>.run_<phase>`). The standalone runs produce identical outputs to the DAG and serve as the per-stage smoke test.

### Phase-by-Phase Results (most recent run)

| #   | Phase                          | Entry Point                                | Result                                                                                                                                                            |
| --- | ------------------------------ | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Ingestion                      | `python -m src.ingestion.run_ingestion`    | ✅ 32,424 financial / 94,327 products / 2,128,605 reviews loaded from GCS (~46 s)                                                                                  |
| 2a  | Raw validation                 | `validate_raw()` in `run_validation.py`    | ✅ 73/74 checks pass; 1 WARNING (`prod_nulls_price` = 50.46% — known data-quality issue) → ALERT, pipeline continues                                               |
| 2b  | Raw anomaly (Tier-1)           | `validate_raw_anomalies()`                 | ✅ 1/3 checks pass; 2 INFO failures → CONTINUE; 27 suspicious financial records quarantined to `data/quarantine/raw_financial_anomalies_<ts>.json`                 |
| 3   | Preprocessing                  | `python -m src.preprocess.run_preprocessing` | ✅ Financial: 32,424 rows. Products: 94,327 rows, **47,601 prices imputed**. Reviews: **2,105,948** (22,657 duplicates removed)                                  |
| 4   | Processed validation           | `validate_processed()`                     | ✅ 33/33 checks pass — CONTINUE                                                                                                                                    |
| 5   | Feature engineering            | `python -m src.features.run_features`      | ✅ 5 financial features derived (`liquid_savings`, `discretionary_income`, `debt_to_income_ratio`, `monthly_expense_burden_ratio`, `emergency_fund_months`); `rating_variance` computed for **94,319** products (47,177 had review data) |
| 6a  | Featured validation            | `validate_features()`                      | ✅ 21/22 checks pass; 1 WARNING → ALERT, pipeline continues                                                                                                        |
| 6b  | Tier-2 anomaly                 | `validate_anomalies()`                     | ✅ 5/8 checks pass; 3 expected outlier WARNINGS in `discretionary_income`, `debt_to_income_ratio`, `emergency_fund_months` (IQR×4) → ALERT, pipeline continues     |
| 7   | Bias detection                 | `python -m src.bias.run_bias`              | ✅ All three modules (financial / product / review) ran; representation risks surfaced and training-time mitigation strategies emitted (see Phase 15 report)       |
| 8   | DB load + embeddings           | `python -m src.database.run_database`      | ▶ Requires a reachable Postgres with `pgvector`. In Airflow, runs after featured validation; in standalone mode, requires `DB_*` env vars to point at a live DB. |

### Reproducing the Verification

**Inside the Airflow worker container** (recommended — all dependencies pre-installed):

```bash
cd data_pipeline
docker compose up -d
docker compose exec -T -w /opt/airflow/dags airflow-worker python -m src.ingestion.run_ingestion
docker compose exec -T -w /opt/airflow/dags airflow-worker python -m src.preprocess.run_preprocessing
docker compose exec -T -w /opt/airflow/dags airflow-worker python -m src.features.run_features
docker compose exec -T -w /opt/airflow/dags airflow-worker python -m src.bias.run_bias
docker compose exec -T -w /opt/airflow/dags airflow-worker python -c \
  "from src.validation.run_validation import validate_raw, validate_raw_anomalies, validate_processed, validate_features, validate_anomalies; \
   [print(f(), '\n---') for f in (validate_raw, validate_raw_anomalies, validate_processed, validate_features, validate_anomalies)]"
docker compose exec -T -w /opt/airflow/dags airflow-worker python -m src.database.run_database
```

**Or trigger the full DAG end-to-end** via the Airflow UI at `http://localhost:8080` (DAG: `Data_pipeline_airflow`).

### Known Data-Quality Behaviors (expected, not bugs)

| Stage              | Signal                                                | Pipeline Action |
| ------------------ | ----------------------------------------------------- | --------------- |
| Raw validation     | `price` 50.46% null in product data                   | WARNING → email alert, continue (imputed in Phase 8) |
| Raw anomaly        | 27 financial records with extreme outliers            | INFO → quarantine, continue                          |
| Featured validation | Outliers in `discretionary_income` / `DTI` / `emergency_fund_months` (IQR×4) | WARNING → email alert, continue                      |

These trigger the **Tier-1 alert path** (email only, no halt) by design — they reflect real-world data heterogeneity, not pipeline failures. Only **CRITICAL** validation failures or **task-level exceptions** halt the DAG.

---

## Appendix B: Notification & Alerting Summary

The pipeline uses **email-only** notifications (Slack was previously wired but removed; only `EmailOperator` paths remain in production).

| Trigger                                | Operator                                  | Trigger Rule        | Behavior                                                            |
| -------------------------------------- | ----------------------------------------- | ------------------- | ------------------------------------------------------------------- |
| Any ingestion task fails               | `email_error_at_ingestion`                | Branch (ALL_DONE)   | Halts downstream stages; sends red HTML email                       |
| Raw validation fails (CRITICAL)        | `email_error_at_raw_validation`           | Branch (ALL_DONE)   | Halts downstream stages                                             |
| Preprocessing task fails               | `email_error_at_preprocessing`            | Branch (ALL_DONE)   | Halts downstream stages                                             |
| Processed validation fails (CRITICAL)  | `email_error_at_processed_validation`     | Branch (ALL_DONE)   | Halts downstream stages                                             |
| Feature task fails                     | `email_error_at_feature_engineering`      | Branch (ALL_DONE)   | Halts downstream stages                                             |
| Featured validation fails (CRITICAL)   | `email_error_at_featured_validation`      | Branch (ALL_DONE)   | Halts downstream stages                                             |
| DB load task fails                     | `email_error_at_DB_loading`               | Branch (ALL_DONE)   | Pipeline marked failed; success email is **not** sent               |
| Any bias task fails                    | `email_error_at_bias_analysis`            | `ONE_FAILED`        | Sends alert in parallel; **does not block** the success email path  |
| All loads succeed                      | `email_pipeline_success`                  | Branch (ALL_DONE)   | Sends green HTML stage-status table                                 |
| Sentinel: success email did not fire   | `pipeline_sentinel`                       | `ALL_DONE`          | Raises `AirflowException` so the DAG run is marked **failed**       |

SMTP credentials (`SMTP_USER`, `SMTP_PASSWORD`) are read from `.env` and wired into the Airflow `smtp_default` connection automatically by `docker-compose.yaml` via `AIRFLOW_CONN_SMTP_DEFAULT` — no manual UI configuration is required.
