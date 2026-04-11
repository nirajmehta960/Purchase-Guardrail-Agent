# =============================================================================
# SavVio ML Pipeline — Cloud Run Job Container
# =============================================================================
# Builds the training image executed as a Cloud Run job.
# CI pushes this image to Artifact Registry, then runs it via
# `gcloud run jobs execute`.
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

# System deps: libgomp1 for XGBoost, libpq-dev + gcc for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps (cache-friendly layer)
COPY model_pipeline/model-requirements.txt /app/model-requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r model-requirements.txt

# Install savviocore
COPY savviocore/ /app/savviocore/
RUN pip install --no-cache-dir -e /app/savviocore

# Install model_pipeline as editable package (pyproject.toml discovers src/)
COPY model_pipeline/ /app/model_pipeline/
RUN pip install --no-cache-dir -e /app/model_pipeline

ENV PYTHONPATH="/app/model_pipeline/src:/app"

CMD ["python", "model_pipeline/src/run_pipeline.py"]
