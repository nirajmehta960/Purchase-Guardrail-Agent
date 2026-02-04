# Data Pipeline

This directory contains the data ingestion, preprocessing, validation, and versioning pipeline for the Purchase Guardrail Agent.

## Structure

SavVio/
   ├── data-pipeline/
   │   ├── dags/              # Airflow DAG definitions
   │   ├── data/
   │   │   ├── raw/           # Raw financial & product data
   │   │   ├── processed/     # Cleaned data
   │   │   └── validated/     # Data ready for model use
   │   ├── scripts/           # Data processing scripts
   │   ├── tests/             # Unit tests
   │   ├── logs/              # Pipeline execution logs
   │   ├── config/            # Configuration files
   │   ├── dvc.yaml           # DVC pipeline definition
   │   └── README.md          # Pipeline documentation

## Key Components

### Data Acquisition
- Fetch financial data from mock Plaid-style API
- Load product data from static files or APIs
- Handle data source failures gracefully

### Data Preprocessing
- Clean financial data (handle missing values, standardize categories)
- Identify and categorize recurring expenses
- Standardize product data formats
- Convert all financial values to monthly format

### Data Validation
- Schema validation using Great Expectations or TFDV
- Anomaly detection (missing values, outliers, invalid formats)
- Data quality checks
- Generate data statistics and schema documentation

### Data Versioning
- Track data versions using DVC with GCP Cloud Storage as remote backend
- Maintain data lineage
- Enable reproducibility
- Store versioned data in GCP Cloud Storage buckets

### Bias Detection
- Data slicing across demographic groups
- Performance analysis across slices
- Bias mitigation strategies

## Running the Pipeline

### Local Development

1. **Start Airflow services** (if using Docker Compose):
   ```bash
   docker-compose up airflow-webserver airflow-scheduler
   ```

2. **Access Airflow UI**: http://localhost:8080

3. **Trigger DAGs** from the Airflow UI or via CLI:
   ```bash
   airflow dags trigger <dag_id>
   ```

### Production on GCP

For production, Airflow can be deployed on GCP Composer (managed Airflow):
- DAGs are automatically synced from Cloud Storage
- Managed infrastructure with automatic scaling
- Integrated with other GCP services (BigQuery, Cloud Storage, etc.)

## Testing

Run data pipeline tests:
```bash
pytest tests/
```

## Configuration

Edit `config/pipeline_config.yaml` to configure:
- Data sources
- Preprocessing steps
- Validation rules
- Alert thresholds
