# Deployment

This directory contains deployment scripts, configurations, and monitoring setup for the Purchase Guardrail Agent.

## Structure

- `scripts/` - Deployment automation scripts (Terraform, Cloud Build, etc.)
- `config/` - Deployment configuration files
- `monitoring/` - Monitoring and alerting scripts
- `docker/` - Dockerfiles for containerization

## Deployment Strategy

### Cloud Deployment on Google Cloud Platform (GCP)

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

### Deployment Automation

- **Terraform**: Infrastructure as Code for provisioning GCP resources
- **Github Actions**: CI/CD pipeline for automated deployment
- **GitHub Actions**: Triggers on code push to main OR model registry updates

## Deployment Steps

1. **Build Docker image**:
   ```bash
   docker build -f docker/Dockerfile.api -t pga-api:latest .
   ```

2. **Terraform for GCP infrastructure**:
   ```bash
   cd infrastructure/terraform
   # Initialize Terraform with GCP backend
   terraform init
   # Review planned changes
   terraform plan
   # Apply infrastructure changes to GCP
   terraform apply
   ```
