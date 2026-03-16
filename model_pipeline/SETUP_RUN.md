## Runbook

### Option 1: Docker (recommended)
```bash
# Start all services (MLflow, PostgreSQL, RustFS, ml-trainer)
docker compose up --build

# Enter the training container
docker exec -it ml-trainer bash

# Run the modular pipeline (trains XGBoost, LightGBM, LinearBoost)
cd /app/src
python run_pipeline.py

# Or run the standalone linear pipeline
python pipeline_linear.py
```

### Option 2: Local virtualenv
```bash
# 1) Move into the model pipeline folder so relative paths resolve there.
cd model_pipeline

# 2) Create and activate a local environment for isolated dependencies.
python3.11 -m venv .venv
source .venv/bin/activate

# 3) Install pipeline dependencies (MLflow, XGBoost, LightGBM, Optuna, etc.).
pip install -r model-requirements.txt

# 4) Start MLflow tracking server in this folder (Terminal A).
#    - backend-store-uri: run metadata DB
#    - default-artifact-root: model/artifact files
mlflow server \
	--backend-store-uri sqlite:///mlflow.db \
	--default-artifact-root ./mlruns \
	--host 127.0.0.1 \
	--port 5000

# 5) In a second terminal (Terminal B), activate env again and run pipeline.
cd model_pipeline
source .venv/bin/activate

# 6) Point training script to local MLflow server and run end-to-end pipeline.
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 python src/run_pipeline.py

# 7) Open MLflow UI to review runs, metrics, artifacts, and registered models.
open http://127.0.0.1:5000
```

### Individual Components
```bash
# Test DB connectivity (once DB integration is live)
python src/data/db_loader.py

# Run data validation after loading
python src/data/validate_data.py

# View MLflow UI
open http://localhost:5000
```

### Running Tests
```bash
pytest tests/ -v
```
