# SavVio Data Pipeline Implementation Guide

## MLOps Course: Data Pipeline Phase

**Project:** SavVio - AI-Driven Financial Advocacy Tool  
**Team Members:** Murtaza Nipplewala, Niraj Mehta, Wen-Hsin Su, Pranathi Bombay, Rishabh Joshi, Sanjana Patnam

---

## Pipeline Execution Order

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
│     ├── Download & Load Financial data (Kaggle)                 │
│     └── Download & Load Product data (Kaggle)  [parallel]       │
│                        ↓                                        │
│  4. VERSION RAW DATA (DVC Checkpoint #1)                        │
│     └── dvc add data/raw/                                       │
│                        ↓                                        │
│  5. SCHEMA & STATISTICS GENERATION                              │
│     └── Generate expectations, compute baseline stats           │
│                        ↓                                        │
│  6. DATA VALIDATION                                             │
│     └── Validate against schema, check constraints              │
│                        ↓                                        │
│  7. ANOMALY DETECTION & ALERTS                                  │
│     └── Detect outliers, missing values, trigger alerts         │
│                        ↓                                        │
│  8. DATA PREPROCESSING                                          │
│     └── Clean, transform, standardize                           │
│                        ↓                                        │
│  9. VERSION PROCESSED DATA (DVC Checkpoint #2)                  │
│     └── dvc add data/processed/                                 │
│                        ↓                                        │
│  10. FEATURE ENGINEERING                                        │
│      └── Create derived features (RUS, affordability, etc.)     │
│                        ↓                                        │
│  11. VERSION FEATURES (DVC Checkpoint #3)                       │
│      └── dvc add data/validated/                                │
│                        ↓                                        │
│  12. BIAS DETECTION & MITIGATION                                │
│      └── Slice analysis, fairness checks                        │
│                        ↓                                        │
│  13. PIPELINE ORCHESTRATION (Airflow DAG)                       │
│      └── Connect all tasks, set dependencies                    │
│                        ↓                                        │
│  14. TESTING                                                    │
│      └── Unit tests for each component                          │
│                        ↓                                        │
│  15. TRACKING, LOGGING & MONITORING                             │
│      └── Logging, metrics, dashboards                           │
│                        ↓                                        │
│  16. PIPELINE OPTIMIZATION                                      │
│      └── Gantt analysis, parallelization                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Reference: Tools by Phase

| Phase | Primary Tools | Airflow Integration |
|-------|---------------|---------------------|
| Setup | Docker, Git, DVC, Python venv | — |
| Data Collection | Documentation, Kaggle exploration | — |
| Ingestion | Pandas, Kaggle API | PythonOperator |
| Schema/Stats | Great Expectations, Pandas Profiling | PythonOperator |
| Validation | Great Expectations, Pandera | PythonOperator |
| Anomaly Detection | Great Expectations, Evidently AI | PythonOperator + EmailOperator |
| Preprocessing | Pandas, NumPy | PythonOperator |
| Features | Pandas, Scikit-learn | PythonOperator |
| Versioning | DVC, GCP Cloud Storage | BashOperator |
| Bias Detection | Fairlearn, Pandas slicing | PythonOperator |
| Orchestration | Apache Airflow | Native |
| Testing | pytest, pytest-cov | — |
| Monitoring | Python logging, Airflow UI | Native |
| Optimization | Airflow Gantt, cProfile | Native |

---

## Table of Contents

1. [Phase 1: Project Setup & Environment Configuration](#phase-1-project-setup--environment-configuration)
2. [Phase 2: Data Collection & Planning](#phase-2-data-collection--planning)
3. [Phase 3: Data Ingestion](#phase-3-data-ingestion)
4. [Phase 4: Version Raw Data (DVC Checkpoint #1)](#phase-4-version-raw-data-dvc-checkpoint-1)
5. [Phase 5: Schema & Statistics Generation](#phase-5-schema--statistics-generation)
6. [Phase 6: Data Validation](#phase-6-data-validation)
7. [Phase 7: Anomaly Detection & Alerts](#phase-7-anomaly-detection--alerts)
8. [Phase 8: Data Preprocessing & Transformation](#phase-8-data-preprocessing--transformation)
9. [Phase 9: Version Processed Data (DVC Checkpoint #2)](#phase-9-version-processed-data-dvc-checkpoint-2)
10. [Phase 10: Feature Engineering](#phase-10-feature-engineering)
11. [Phase 11: Version Features (DVC Checkpoint #3)](#phase-11-version-features-dvc-checkpoint-3)
12. [Phase 12: Bias Detection & Mitigation](#phase-12-bias-detection--mitigation)
13. [Phase 13: Pipeline Orchestration (Airflow DAGs)](#phase-13-pipeline-orchestration-airflow-dags)
14. [Phase 14: Testing](#phase-14-testing)
15. [Phase 15: Tracking, Logging & Monitoring](#phase-15-tracking-logging--monitoring)
16. [Phase 16: Pipeline Optimization](#phase-16-pipeline-optimization)

---

## Phase 1: Project Setup & Environment Configuration

### Objective
Establish the foundational project structure, dependencies, and development environment before any data work begins.

### Steps

1. **Create folder structure** following the required format:
   ```
   SavVio/
   ├── data-pipeline/
   │   ├── dags/              # Airflow DAG definitions
   │   ├── data/
   │   │   ├── raw/           # Raw financial & product data
   │   │   ├── processed/     # Cleaned data
   │   │   └── validated/     # Data ready for model use
   │   ├── scripts/
   │   │   ├── ingest/        # Data ingestion modules
   │   │   ├── validate/      # Validation modules
   │   │   ├── preprocess/    # Preprocessing modules
   │   │   └── features/      # Feature engineering modules
   │   ├── tests/             # Unit tests
   │   ├── logs/              # Pipeline execution logs
   │   ├── config/            # Configuration files
   │   ├── dvc.yaml           # DVC pipeline definition
   │   └── README.md          # Pipeline documentation
   ```

2. **Set up Python environment**
   - Create `requirements.txt` or `environment.yml`
   - Include: pandas, apache-airflow, dvc, great-expectations, pytest, evidently, fairlearn

3. **Configure Docker environment for Airflow**
   - Use official Apache Airflow Docker Compose setup
   - Customize for SavVio (mount data directories, install dependencies)
   - Ensure all team members can replicate the environment

4. **Initialize version control**
   - Git repository setup
   - Create `.gitignore` (exclude data files, logs, credentials, `__pycache__`)
   - Initialize DVC: `dvc init`
   - Configure DVC remote: `dvc remote add -d gcs gs://savvio-data-bucket`

### Tools/Services

| Tool | Purpose |
|------|---------|
| Docker + Docker Compose | Containerized Airflow environment |
| Git | Code version control |
| DVC | Data version control initialization |
| Python 3.12+ | Runtime environment |

---

## Phase 2: Data Collection & Planning

### Objective
Identify, understand, and document the data sources needed for SavVio's purchase guardrail functionality. This phase is about planning and documentation before actual data ingestion.

### Steps

1. **Translate user needs into data needs**
   - Users: Consumers making purchase decisions
   - User Need: Make informed, responsible purchase decisions
   - System Need: Financial health data + Product information

2. **Document data sources for SavVio**

   **Financial Data:**
   | Attribute | Details |
   |-----------|---------|
   | Source | Personal Finance ML Dataset (Kaggle) |
   | URL | https://www.kaggle.com/datasets/... |
   | Format | CSV |
   | Size | ~X records (to be confirmed) |
   | Update Frequency | Static (for academic project) |

   **Product Data:**
   | Attribute | Details |
   |-----------|---------|
   | Source | Amazon Products Dataset (Kaggle) |
   | URL | https://www.kaggle.com/datasets/... |
   | Format | CSV |
   | Size | ~X records (to be confirmed) |
   | Update Frequency | Static (for academic project) |

3. **Document expected data fields**

   **Financial Data Fields:**
   | Field | Type | Description | Required |
   |-------|------|-------------|----------|
   | monthly_income | float | User's monthly income | Yes |
   | rent | float | Monthly rent payment | No |
   | recurring_bills | float | Subscriptions, utilities, etc. | No |
   | savings_balance | float | Current savings amount | No |
   | debt_obligations | float | Monthly debt payments | No |

   **Product Data Fields:**
   | Field | Type | Description | Required |
   |-------|------|-------------|----------|
   | product_name | string | Name of the product | Yes |
   | category | string | Product category | Yes |
   | price | float | Product price | Yes |
   | specifications | string | Product details/specs | No |

4. **Assess data quality considerations**
   - Check if sources are refreshed/updated
   - Verify data consistency (values, units, data types)
   - Identify sensitive fields requiring protection
   - Note any known data quality issues

5. **Document data privacy measures**
   - Masking/hashing user identifiers
   - Encryption for financial snapshots
   - Read-only access (no transactional capabilities)
   - Compliance with data privacy principles

6. **Create Data Card documentation**
   
   | Attribute | Description |
   |-----------|-------------|
   | Data Type | Medium-sized datasets (thousands of records) |
   | Financial Data Fields | Monthly income, rent, recurring bills, savings, debt |
   | Product Data Fields | Product name, category, price, specifications |
   | Estimated Size | Small to medium (hundreds to thousands of records) |
   | Update Frequency | Periodic or event-driven (simulated) |
   | Sensitive Fields | Income, savings, expenses (masked and encrypted) |

### Outputs
- `docs/data_sources.md` — Documented data sources and fields
- `docs/data_card.md` — Formal data card
- `docs/privacy_measures.md` — Privacy and security documentation

### SavVio-Specific Considerations
- Financial data simulates Plaid-style bank account summaries
- Product data represents e-commerce product listings
- All data is synthetic or publicly available — no real PII
- Data supports answering: "Can the user afford this?" and "Is this purchase reasonable?"

---

## Phase 3: Data Ingestion

### Objective
Download data from external sources and load it into the pipeline system in a consistent, reproducible manner.

### Steps

1. **Create modular ingestion package structure**
   ```
      scripts/
   ├── ingest/
   │   ├── __init__.py
   │   ├── gcs_loader.py         # Handles ALL GCS bucket operations
   │   ├── api_loader.py         # Handles ALL API endpoint operations  
   │   ├── config.py             # Environment-based config
   │   └── utils.py              # Shared: logging, validation
   └── run_ingestion.py          # Reads env config, routes to GCS or API
   ```

2. **Implement shared utilities** (`ingest/utils.py`)
   - Logging configuration
   - Error handling helpers
   - File path management
   - Kaggle API wrapper functions
   - Data validation helpers

3. **Implement financial data ingestion** (`ingest/financial.py`)
   - `download_financial_data()` — Fetches from Kaggle
   - `load_financial_data()` — Reads CSV into DataFrame
   - `save_raw_financial_data()` — Saves to `data/raw/`

4. **Implement product data ingestion** (`ingest/product.py`)
   - `download_product_data()` — Fetches from Kaggle
   - `load_product_data()` — Reads CSV into DataFrame
   - `save_raw_product_data()` — Saves to `data/raw/`

5. **Create main entry point** (`run_ingestion.py`)
   - Orchestrates both ingestion modules
   - Handles command-line arguments
   - Provides single entry point for Airflow

6. **Implement data privacy measures during ingestion**
   - Mask/hash any user identifiers immediately upon load
   - Log ingestion metadata without exposing sensitive values
   - Ensure read-only access patterns

7. **Store raw data in original format**
   - Save to `data/raw/financial.csv`
   - Save to `data/raw/products.csv`
   - Maintain original CSV format for reproducibility

8. **Log ingestion metadata**
   - Timestamp of ingestion
   - Number of records loaded
   - Source file checksums
   - Any download errors encountered

### Module Structure Example

**ingest/utils.py:**
- `setup_logging()` — Configure logging for ingestion
- `get_kaggle_client()` — Initialize Kaggle API
- `validate_download()` — Verify file integrity
- `log_ingestion_metadata()` — Record ingestion details

**ingest/financial.py:**
- `download_financial_data(output_path)` — Download from Kaggle
- `load_financial_data(file_path)` — Load CSV to DataFrame
- `save_raw_financial_data(df, output_path)` — Save raw data

**ingest/product.py:**
- `download_product_data(output_path)` — Download from Kaggle
- `load_product_data(file_path)` — Load CSV to DataFrame
- `save_raw_product_data(df, output_path)` — Save raw data

### Tools/Services

| Tool | Purpose |
|------|---------|
| Kaggle API | Programmatic dataset download |
| Pandas | Data loading (`pd.read_csv()`) |
| Python logging | Ingestion logging |
| Presidio (optional) | PII detection during ingestion |

### Airflow Integration
```python
ingest_financial = PythonOperator(
    task_id='ingest_financial_data',
    python_callable=financial.load_financial_data,
    op_kwargs={'file_path': '/path/to/data/raw/'},
    dag=dag
)

ingest_products = PythonOperator(
    task_id='ingest_product_data',
    python_callable=product.load_product_data,
    op_kwargs={'file_path': '/path/to/data/raw/'},
    dag=dag
)

# These can run in parallel
[ingest_financial, ingest_products] >> next_task
```

### SavVio-Specific Considerations
- Both datasets can be ingested in parallel (no dependencies between them)
- Financial data contains sensitive fields — apply privacy measures immediately
- Keep data in CSV format throughout pipeline
- Log record counts for monitoring data completeness

---

## Phase 4: Version Raw Data (DVC Checkpoint #1)

### Objective
Version control the raw ingested data to ensure reproducibility and enable rollback if needed.

### Steps

1. **Add raw data to DVC tracking**
   ```bash
   dvc add data/raw/financial.csv
   dvc add data/raw/products.csv
   ```

2. **Commit .dvc files to Git**
   ```bash
   git add data/raw/*.dvc data/raw/.gitignore
   git commit -m "Add raw data v1.0"
   ```

3. **Push data to remote storage**
   ```bash
   dvc push
   ```

4. **Tag the version**
   ```bash
   git tag -a "data-raw-v1.0" -m "Initial raw data ingestion"
   ```

### Tools/Services

| Tool | Purpose |
|------|---------|
| DVC | Data versioning |
| GCP Cloud Storage | Remote storage backend |
| Git | Version tagging |

### Airflow Integration
```python
version_raw_data = BashOperator(
    task_id='version_raw_data',
    bash_command='cd /opt/airflow/data-pipeline && dvc add data/raw/ && dvc push',
    dag=dag
)
```

### Why Version Here?
- Captures original data before any modifications
- Enables rollback if preprocessing introduces errors
- Provides "source of truth" for debugging

---

## Phase 5: Schema & Statistics Generation

### Objective
Automatically generate data schema and compute baseline statistics to establish expectations for data quality validation.

### Steps

1. **Generate data schema**
   
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

2. **Compute baseline statistics**
   - For numeric columns: min, max, mean, median, std, percentiles
   - For categorical columns: unique values, frequency distribution
   - For all columns: null percentage, data type distribution

3. **Create expectation suites (Great Expectations)**
   - `expect_column_to_exist`
   - `expect_column_values_to_be_of_type`
   - `expect_column_values_to_not_be_null` (for required fields)
   - `expect_column_values_to_be_between` (for numeric ranges)
   - `expect_column_values_to_be_in_set` (for categories)

4. **Store schema and statistics artifacts**
   - Save to `config/schemas/financial_schema.json`
   - Save to `config/schemas/product_schema.json`
   - Save statistics to `config/statistics/`

### Tools/Services

| Tool | Purpose |
|------|---------|
| Great Expectations | Schema & expectation suites |
| Pandas Profiling / ydata-profiling | Automated statistics reports |
| Pandera | Schema definition (alternative) |

### SavVio-Specific Considerations
- Financial schema must enforce non-negative values for all monetary fields
- Product price must be strictly positive (> 0)
- Statistics baseline will be used for drift detection later

---

## Phase 6: Data Validation

### Objective
Validate incoming data against established schema and expectations to ensure data quality before processing.

### Steps

1. **Run validation checkpoint**
   - Load expectation suite from Phase 5
   - Execute validation against raw data
   - Generate validation results

2. **Implement validation rules for SavVio**

   **Financial Data:**
   - `monthly_income` must be non-negative
   - `rent` cannot exceed `monthly_income` significantly
   - All monetary fields must be numeric
   - Required fields cannot be null

   **Product Data:**
   - `price` must be positive
   - `product_name` cannot be empty
   - `category` must be from predefined list

3. **Handle validation failures**
   - Log validation errors with row-level details
   - Quarantine invalid records to `data/quarantine/`
   - Generate validation report (HTML via Great Expectations Data Docs)
   - Decide: halt pipeline (critical failure) or continue with valid records

4. **Store validation results**
   - Save to `logs/validation/`
   - Include timestamp, pass/fail counts, specific failures

### Tools/Services

| Tool | Purpose |
|------|---------|
| Great Expectations | Run validation checkpoints |
| Pandera | DataFrame validation (alternative) |
| Pydantic | Row-level validation (alternative) |

### SavVio-Specific Considerations
- Financial data validation is critical — incorrect data impacts recommendations
- Consider soft failure for product data, hard failure for financial data
- Validation must catch: missing income, invalid categorization, corrupted data

---

## Phase 7: Anomaly Detection & Alerts

### Objective
Detect data anomalies (outliers, suspicious patterns) and trigger alerts when issues are found.

### Steps

1. **Define anomaly detection rules**

   **Financial Data Anomalies:**
   - Income = 0 (suspicious for adult user)
   - Expenses > 2x income (likely data error)
   - Negative values in any monetary field
   - Savings > 10x monthly income (potential data error)

   **Product Data Anomalies:**
   - Price = 0 or negative
   - Price > $100,000 (outlier, verify)
   - Missing product name with valid price

2. **Implement detection mechanisms**
   - Statistical: IQR method, z-score for outliers
   - Rule-based: business logic checks
   - Pattern-based: unexpected value combinations

3. **Configure alert system**
   
   | Severity | Condition | Action |
   |----------|-----------|--------|
   | INFO | Minor outliers detected | Log only |
   | WARNING | >5% records have issues | Email alert |
   | CRITICAL | Financial data missing/corrupted | Email + Slack, halt pipeline |

4. **Create anomaly handling workflow**
   - Log all detected anomalies with details
   - Quarantine suspicious records
   - Generate anomaly report
   - Trigger appropriate alerts

### Tools/Services

| Tool | Purpose |
|------|---------|
| Great Expectations | Anomaly detection via expectations |
| Evidently AI | Statistical anomaly detection |
| Airflow EmailOperator | Email alerts |
| Airflow SlackWebhookOperator | Slack alerts |

---

## Phase 8: Data Preprocessing & Transformation

### Objective
Clean, transform, and standardize validated data into a consistent format ready for feature engineering.

### Steps

1. **Create modular preprocessing package**
   ```
   scripts/
   ├── preprocess/
   │   ├── __init__.py
   │   ├── financial.py      # Financial preprocessing
   │   ├── product.py        # Product preprocessing
   │   └── utils.py          # Shared utilities
   └── run_preprocessing.py  # Main entry point
   ```

2. **Data cleaning**

   **Financial Data:**
   - Handle missing values (impute median for optional fields, flag for required)
   - Standardize transaction categories (e.g., "Netflix" → "subscription")
   - Remove exact duplicate records
   
   **Product Data:**
   - Remove duplicate products (by name + price)
   - Standardize price format (ensure float, 2 decimal places)
   - Clean product names (trim whitespace, normalize case)

3. **Data transformation**

   **Financial Data:**
   - Convert all values to monthly format:
     - Annual income ÷ 12
     - Weekly expenses × 4.33
   - Categorize expenses into: rent, subscriptions, loans, utilities, other
   - Calculate total fixed expenses
   
   **Product Data:**
   - Standardize category names (lowercase, consistent naming)
   - Extract key specs from description (if applicable)

4. **Save processed data in CSV format**
   - Output to `data/processed/financial_processed.csv`
   - Output to `data/processed/products_processed.csv`

### Tools/Services

| Tool | Purpose |
|------|---------|
| Pandas | Data manipulation |
| NumPy | Numerical operations |
| Python re (regex) | Text cleaning |

### SavVio-Specific Considerations
- Monthly format is critical for consistent affordability calculations
- Recurring expense identification accuracy target: 95%+ (key metric)
- Preprocessing logic must be deterministic for reproducibility

---

## Phase 9: Version Processed Data (DVC Checkpoint #2)

### Objective
Version control the processed data to track transformations and enable comparison with raw data.

### Steps

1. **Add processed data to DVC**
   ```bash
   dvc add data/processed/financial_processed.csv
   dvc add data/processed/products_processed.csv
   ```

2. **Commit and push**
   ```bash
   git add data/processed/*.dvc
   git commit -m "Add processed data v1.0"
   dvc push
   ```

3. **Tag the version**
   ```bash
   git tag -a "data-processed-v1.0" -m "Processed data after cleaning and transformation"
   ```

### Tools/Services

| Tool | Purpose |
|------|---------|
| DVC | Data versioning |
| GCP Cloud Storage | Remote storage |

### Why Version Here?
- Captures cleaned data before feature engineering
- If feature engineering has bugs, don't need to re-preprocess
- Enables comparison: raw vs processed

---

## Phase 10: Feature Engineering

### Objective
Create meaningful features from processed data that maximize predictive signal for SavVio's decision engine.

### Steps

1. **Create modular feature engineering package**
   ```
   scripts/
   ├── features/
   │   ├── __init__.py
   │   ├── financial_features.py   # Financial health features
   │   ├── affordability_features.py  # Purchase affordability features
   │   └── utils.py                # Shared utilities
   └── run_feature_engineering.py  # Main entry point
   ```

2. **Define financial health features**

   | Feature | Formula | Purpose |
   |---------|---------|---------|
   | `discretionary_income` | income - total_fixed_expenses | Available spending money |
   | `debt_to_income_ratio` | debt_payments / income | Financial burden indicator |
   | `savings_rate` | savings / income | Financial health indicator |
   | `expense_burden_ratio` | total_expenses / income | Spending pattern |
   | `emergency_fund_months` | savings / monthly_expenses | Safety buffer |

3. **Define affordability features**

   | Feature | Formula | Purpose |
   |---------|---------|---------|
   | `price_to_income_ratio` | product_price / monthly_income | Relative cost |
   | `affordability_score` | discretionary_income - product_price | Can afford? |
   | `residual_utility_score` | (savings - product_price) / monthly_expenses | Impact on emergency fund |

4. **Handle edge cases**
   - Zero income: flag as invalid, don't compute ratios
   - Negative values: handle gracefully, log warnings
   - Division by zero: use safe division with defaults

5. **Validate feature outputs**
   - Check for NaN/Inf values
   - Verify ranges make sense
   - Ensure no data leakage

6. **Save feature-engineered data in CSV format**
   - Output to `data/validated/features.csv`
   - Store feature metadata in `config/feature_definitions.json`

### Tools/Services

| Tool | Purpose |
|------|---------|
| Pandas | Feature creation |
| NumPy | Numerical operations |
| Scikit-learn | Encoding (if needed) |

### SavVio-Specific Considerations
- **Residual Utility Score (RUS)** is the key metric for purchase recommendations
- Features must support Green/Yellow/Red decision logic
- Features should enable "break-even point" calculations

---

## Phase 11: Version Features (DVC Checkpoint #3)

### Objective
Version control the final feature set to ensure reproducibility of model training and recommendations.

### Steps

1. **Add features to DVC**
   ```bash
   dvc add data/validated/features.csv
   ```

2. **Commit and push**
   ```bash
   git add data/validated/*.dvc
   git commit -m "Add engineered features v1.0"
   dvc push
   ```

3. **Tag the version**
   ```bash
   git tag -a "data-features-v1.0" -m "Feature-engineered data ready for modeling"
   ```

4. **Update dvc.yaml pipeline definition**
   - Define complete pipeline stages
   - Specify dependencies between stages
   - Enable `dvc repro` for full pipeline reproduction

### Tools/Services

| Tool | Purpose |
|------|---------|
| DVC | Data versioning |
| GCP Cloud Storage | Remote storage |

### Why Version Here?
- Final model-ready data
- Ties directly to model versions
- Enables experiment reproducibility

---

## Phase 12: Bias Detection & Mitigation

### Objective
Detect and mitigate bias in the dataset through data slicing and analysis across different subgroups.

### Steps

1. **Identify slicing features relevant to SavVio**

   | Slice Dimension | Groups | Why It Matters |
   |-----------------|--------|----------------|
   | Income bracket | Low (<$3k), Medium ($3k-$7k), High (>$7k) | Ensure fair recommendations across income levels |
   | Debt-to-income | Low (<0.2), Medium (0.2-0.4), High (>0.4) | Don't penalize those in debt unfairly |
   | Expense burden | Low (<0.5), Medium (0.5-0.8), High (>0.8) | Fair treatment regardless of spending patterns |
   | Product category | Electronics, Clothing, Home, etc. | No category bias in recommendations |

2. **Perform slice analysis**
   - Count records per slice
   - Identify underrepresented groups
   - Compare feature distributions across slices

3. **Evaluate for potential bias**
   - Check if certain income groups are underrepresented
   - Verify recommendations wouldn't disproportionately affect any group
   - Test: Would low-income users get "Red Light" for reasonable purchases?

4. **Implement mitigation if needed**
   - Re-sample underrepresented groups
   - Adjust feature engineering to be fair
   - Document trade-offs

5. **Document bias analysis**
   - Create `docs/bias_analysis_report.md`
   - Include: slices analyzed, biases found, mitigation steps, remaining limitations

### Tools/Services

| Tool | Purpose |
|------|---------|
| Fairlearn | Bias metrics and mitigation |
| Pandas groupby | Manual slice analysis |
| SliceFinder | Automated slice discovery |

### SavVio-Specific Considerations
- Critical: SavVio must act in ALL users' best financial interest
- Document that the system is designed to help, not discriminate
- Ethical consideration: transparent explanations for all recommendations

---

## Phase 13: Pipeline Orchestration (Airflow DAGs)

### Objective
Structure the entire data pipeline using Airflow DAGs with logical task connections and proper error handling.

### Steps

1. **Design DAG structure**

   ```
   [ingest_financial] ─┬─→ [version_raw] → [generate_schema] → [validate_data]
   [ingest_products]  ─┘                           ↓
                                          [detect_anomalies]
                                                   ↓
                                          [preprocess_data]
                                                   ↓
                                         [version_processed]
                                                   ↓
                                         [engineer_features]
                                                   ↓
                                         [version_features]
                                                   ↓
                                          [detect_bias]
                                                   ↓
                                            [complete]
   ```

2. **Create DAG definition**
   - Location: `dags/savvio_data_pipeline.py`
   - Set default_args: owner, retries (2), retry_delay (5 min)
   - Schedule: `@daily` or `None` for manual triggers

3. **Define tasks using operators**
   - `PythonOperator` for data processing scripts
   - `BashOperator` for DVC commands
   - `EmailOperator` for alerts

4. **Set task dependencies**
   - Parallel: `ingest_financial` and `ingest_products`
   - Sequential: validation → preprocessing → features
   - Alerts: trigger on failure

5. **Implement error handling**
   - Retries for transient failures
   - `on_failure_callback` for alerts
   - `trigger_rule='all_success'` for dependent tasks

### Tools/Services

| Tool | Purpose |
|------|---------|
| Apache Airflow | DAG orchestration |
| PythonOperator | Run Python functions |
| BashOperator | Run shell commands (DVC) |
| EmailOperator | Send alerts |

---

## Phase 14: Testing

### Objective
Write comprehensive unit tests for each pipeline component to ensure robustness.

### Steps

1. **Create test structure**
   ```
   tests/
   ├── test_ingestion.py
   ├── test_validation.py
   ├── test_preprocessing.py
   ├── test_feature_engineering.py
   ├── test_anomaly_detection.py
   ├── test_bias_detection.py
   └── conftest.py
   ```

2. **Test categories**

   | Component | Test Cases |
   |-----------|------------|
   | Ingestion | File not found, corrupted file, empty file, successful load |
   | Validation | Invalid types, out-of-range values, null required fields |
   | Preprocessing | Missing value handling, transformation correctness |
   | Features | Edge cases (zero income), calculation accuracy |
   | Anomaly | Known outliers detected, alerts triggered |

3. **Run tests in CI/CD**
   - Configure pytest in GitHub Actions
   - Require tests to pass before merge
   - Generate coverage report

### Tools/Services

| Tool | Purpose |
|------|---------|
| pytest | Test framework |
| pytest-cov | Coverage reporting |
| unittest.mock | Mocking |
| GitHub Actions | CI/CD |

---

## Phase 15: Tracking, Logging & Monitoring

### Objective
Implement comprehensive logging and monitoring to track pipeline progress and support debugging.

### Steps

1. **Implement Python logging**
   - Configure in each script
   - Use levels: DEBUG, INFO, WARNING, ERROR
   - Format: `[timestamp] [level] [module] message`

2. **Track pipeline metrics**

   | Metric | Description |
   |--------|-------------|
   | Records ingested | Count per data source |
   | Validation pass rate | % records passing validation |
   | Anomalies detected | Count and type |
   | Processing time | Duration per task |

3. **Create monitoring approach**
   - Use Airflow UI for task monitoring
   - Store logs in `logs/` directory
   - Consider GCP Cloud Logging for production

4. **Set up alerts**
   - Task failures → Email
   - High anomaly rate → Warning
   - Financial data issues → Critical alert

### Tools/Services

| Tool | Purpose |
|------|---------|
| Python logging | Application logs |
| Airflow logging | Task logs |
| GCP Cloud Logging | Centralized logs (production) |
| Grafana (optional) | Dashboards |

---

## Phase 16: Pipeline Optimization

### Objective
Identify and resolve bottlenecks to optimize pipeline performance.

### Steps

1. **Analyze with Airflow Gantt chart**
   - Access via Airflow UI → DAG → Gantt
   - Identify longest-running tasks
   - Find tasks blocking parallelization

2. **Common bottlenecks for SavVio**

   | Bottleneck | Solution |
   |------------|----------|
   | Data download | Cache locally, retry logic |
   | Large file validation | Chunked processing |
   | DVC push | Run async, compress data |

3. **Implement optimizations**
   - Parallelize independent tasks
   - Use chunked processing for large datasets
   - Optimize pandas operations (vectorization)

4. **Measure improvements**
   - Compare before/after task durations
   - Document performance gains

### Tools/Services

| Tool | Purpose |
|------|---------|
| Airflow Gantt Chart | Visual analysis |
| cProfile | Code profiling |
| Pandas optimization | Vectorization |