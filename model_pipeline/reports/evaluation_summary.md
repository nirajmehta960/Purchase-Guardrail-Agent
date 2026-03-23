# Evaluation Summary

Generated at: 2026-03-22 18:30:43

## Baseline Models (Validation Metrics)

### xgboost
- run_id: ebd5ce1276b04796889e57500e1be8fb
- accuracy: 0.9979
- f1_score: 0.9979
- roc_auc: 0.9999
- pr_auc: 0.9998

### lightgbm
- run_id: 75335c34d8bb499daecfe42e51d802ad
- accuracy: 0.9977
- f1_score: 0.9977
- roc_auc: 1.0
- pr_auc: 0.9999

### xgb_linear
- run_id: 509ff3630a1c4618a21aa0cd787a392f
- accuracy: 0.903
- f1_score: 0.8999
- roc_auc: 0.9836
- pr_auc: 0.9614

## Champion Model (Final Test Metrics)

- model_name: xgboost_tuned
- source_run_id: 1eeaae2390124fccab7f09b6fca038f0
- accuracy: 0.9972
- f1_score: 0.9972
- roc_auc: 1.0
- pr_auc: 1.0

## Hyperparameter Sensitivity (Tuned Champion)

- study_name: xgboost_tuning
- completed_trials: 50
- top_hyperparameters:
  - n_estimators: 0.390676
  - reg_lambda: 0.161187
  - max_depth: 0.158082
  - reg_alpha: 0.090782
  - learning_rate: 0.085652
- artifact_paths:
  - /app/reports/sensitivity/xgboost_tuned_param_importance.json
  - /app/reports/sensitivity/xgboost_tuned_param_importance.png
  - /app/reports/sensitivity/xgboost_tuned_param_sensitivity.png
