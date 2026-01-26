# Model Development

This directory contains the machine learning model development, training, validation, and bias detection code.

## Structure

- `src/` - Source code for model training, validation, and bias detection
- `notebooks/` - Jupyter notebooks for experimentation and analysis
- `models/` - Trained model artifacts (versioned)
- `experiments/` - MLflow experiment tracking outputs
- `config/` - Model configuration files (hyperparameters, training settings)
- `tests/` - Model testing scripts

## Key Components

### Model Training
- Load versioned data from data pipeline
- Train baseline models (Logistic Regression, XGBoost)
- Implement deterministic financial logic engine
- Hyperparameter tuning (grid search, random search, Bayesian optimization)

### Model Validation
- Performance metrics (accuracy, precision, recall, F1-score, AUC)
- Cross-validation
- Hold-out validation set evaluation

### Bias Detection
- Data slicing across demographic groups
- Performance analysis across slices using Fairlearn or TFMA
- Bias mitigation (re-sampling, fairness constraints, threshold adjustment)
- Generate bias reports and visualizations

### Experiment Tracking
- Track experiments with MLflow or Weights & Biases
- Log hyperparameters, metrics, and model versions
- Compare model performance
- Visualize results (confusion matrices, ROC curves, etc.)

### Model Selection
- Select best model based on validation metrics and bias analysis
- Push selected model to GCP Artifact Registry or Vertex AI Model Registry for versioning and deployment

### Sensitivity Analysis
- Feature importance analysis (SHAP, LIME)
- Hyperparameter sensitivity analysis
- Model interpretability

## Running Model Training

1. **Pull latest versioned data**:
   ```bash
   dvc pull
   ```

2. **Train model**:
   ```bash
   python src/train.py --config config/training_config.yaml
   ```

3. **View experiments in MLflow**:
   ```bash
   mlflow ui
   # Access at http://localhost:5000
   ```

## CI/CD Integration

The model training pipeline is automated via GitHub Actions integrated with GCP:
- Triggers on code changes to model development code
- Runs training and validation (can use GCP Vertex AI Training for distributed training)
- Performs bias detection
- Pushes validated models to GCP Artifact Registry or Vertex AI Model Registry
- Automatically triggers deployment pipeline on GCP Cloud Build

## Testing

Run model development tests:
```bash
pytest tests/
```

## Configuration

Edit `config/training_config.yaml` to configure:
- Model architecture
- Hyperparameters
- Training parameters
- Validation settings
- Bias detection thresholds
