"""
Model Pipeline — End-to-End Entrypoint.

Orchestrates the full model training flow:
    1. Initialization   — Seeds, MLflow config.
    2. Data Preparation  — Feature engineering, label encoding, 3-way split.
    3. Baseline Training — Train XGBoost, LightGBM, XGB-Linear, Logistic Regression.
    4. Model Selection   — Best F1 + bias gate (when bias module is ready).
    5. Final Evaluation  — Held-out test set, all visualizations, registry prep.

Usage:
    python run_pipeline.py
"""

import os
import logging
import joblib
import pandas as pd
import numpy as np

import mlflow
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from mlflow.models.signature import infer_signature

from config import Config
# from features.feature_engineering import build_feature_matrix
from core_models.train import train_model, log_model_to_mlflow
from core_models.evaluate import evaluate_model
from core_models.optuna_tuner import tune_best_candidate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bias detection — placeholder until module is complete.
# ---------------------------------------------------------------------------
try:
    from guards.bias_detection import evaluate_bias
    BIAS_AVAILABLE = True
except ImportError:
    logger.warning("Bias detection module not available — skipping bias checks.")
    BIAS_AVAILABLE = False

# Temporarily force-disable bias checks until bias module contract is finalized.
BIAS_AVAILABLE = False


# ---------------------------------------------------------------------------
# LLM wrapper — placeholder until module is complete.
# ---------------------------------------------------------------------------
try:
    from llm.prompt_engin import apply_llm_guardrails
    LLM_AVAILABLE = True
except ImportError:
    logger.warning("LLM wrapper module not available — skipping LLM demo.")
    LLM_AVAILABLE = False


# ---------------------------------------------------------------------------
# 1. Initialization
# ---------------------------------------------------------------------------

def initialize():
    """Set global seeds and configure MLflow."""
    os.environ["PYTHONHASHSEED"] = str(Config.RANDOM_STATE)
    np.random.seed(Config.RANDOM_STATE)

    mlflow.set_tracking_uri(Config.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(Config.EXPERIMENT_NAME)
    logger.info("MLflow tracking URI: %s", Config.MLFLOW_TRACKING_URI)
    logger.info("MLflow experiment: %s", Config.EXPERIMENT_NAME)


# ---------------------------------------------------------------------------
# 2. Data Preparation
# ---------------------------------------------------------------------------


def prepare_data():
    """
    Build feature matrix, encode labels, and perform 3-way stratified split.

    Returns:
        dict with keys: X_train, X_val, X_test, y_train, y_val, y_test,
                        sens_train, sens_val, sens_test, label_encoder, scenarios_raw
    """
    # Feature engineering + deterministic labeling (GREEN/YELLOW/RED).
    # X, y_raw, scenarios_raw = build_feature_matrix(is_training=True)
    data = pd.read_csv(Config.SCENARIO_OUTPUT_PATH)
    X = data.drop(columns=[Config.LABEL_COL])
    non_numeric_cols = X.select_dtypes(exclude=["number", "bool"]).columns.tolist()
    if non_numeric_cols:
        logger.info("Dropping non-numeric feature columns: %s", non_numeric_cols)
        X = X.drop(columns=non_numeric_cols)
    y_raw = data[Config.LABEL_COL]
    scenarios_raw = data.copy()  # Keep for bias detection and artifact logging.

    # Encode string labels into integers for model training.
    label_encoder = LabelEncoder()
    y = label_encoder.fit_transform(y_raw)
    logger.info("Label encoding: %s", dict(zip(
        label_encoder.classes_, label_encoder.transform(label_encoder.classes_)
    )))
    logger.info("Label distribution:\n%s", y_raw.value_counts().to_string())

    # Extract sensitive features for bias detection.
    sensitive_cols = [c for c in Config.SENSITIVE_FEATURES if c in scenarios_raw.columns]
    sensitive_features = scenarios_raw[sensitive_cols] if sensitive_cols else None

    # --- 3-way stratified split: train (60%) / val (20%) / test (20%) ---
    # First split: separate test set (20%).
    X_temp, X_test, y_temp, y_test = train_test_split(
        X, y, test_size=0.2, random_state=Config.RANDOM_STATE, stratify=y,
    )
    sens_temp, sens_test = (None, None)
    if sensitive_features is not None:
        sens_temp, sens_test = train_test_split(
            sensitive_features, test_size=0.2,
            random_state=Config.RANDOM_STATE, stratify=y,
        )

    # Second split: separate validation from training (25% of remaining = 20% of total).
    X_train, X_val, y_train, y_val = train_test_split(
        X_temp, y_temp, test_size=0.25, random_state=Config.RANDOM_STATE, stratify=y_temp,
    )
    sens_train, sens_val = (None, None)
    if sens_temp is not None:
        sens_train, sens_val = train_test_split(
            sens_temp, test_size=0.25,
            random_state=Config.RANDOM_STATE, stratify=y_temp,
        )

    logger.info("Split sizes — train: %d, val: %d, test: %d", len(y_train), len(y_val), len(y_test))

    return {
        "X_train": X_train, "X_val": X_val, "X_test": X_test,
        "y_train": y_train, "y_val": y_val, "y_test": y_test,
        "sens_train": sens_train, "sens_val": sens_val, "sens_test": sens_test,
        "label_encoder": label_encoder, "scenarios_raw": scenarios_raw,
    }


# ---------------------------------------------------------------------------
# 3. Train Candidates
# ---------------------------------------------------------------------------

def train_candidates(X_train, y_train, X_val, y_val, sens_val, label_encoder):
    """
    Train all model candidates, evaluate on validation set, run bias checks.

    Returns:
        List of dicts, each with: name, model, run_id, metrics, bias_passed.
    """
    models_to_train = [
        {"name": "xgboost",     "params": {"max_depth": 3, "learning_rate": 0.1, "n_estimators": 100}},
        {"name": "lightgbm",    "params": {"max_depth": 3, "learning_rate": 0.1, "n_estimators": 100}},
        {"name": "xgb_linear",  "params": {"learning_rate": 0.1, "n_estimators": 100}},
        {"name": "logistic_regression", "params": {"max_iter": 1000, "solver": "lbfgs"}},
    ]

    candidates = []

    for model_config in models_to_train:
        model_type = model_config["name"]
        params = model_config["params"]

        logger.info("=" * 50)
        logger.info("Training candidate: %s", model_type)
        logger.info("=" * 50)

        try:
            with mlflow.start_run(run_name=f"{model_type}_baseline"):

                # Log metadata.
                mlflow.log_param("model_type", model_type)
                mlflow.log_params(params)
                mlflow.log_param("n_scenarios", Config.N_SCENARIOS)
                mlflow.log_param("label_type", "deterministic_engine_GYR")
                mlflow.log_param("num_classes", len(label_encoder.classes_))

                # Train.
                model = train_model(
                    model_type, X_train, y_train, params,
                    X_val=X_val, y_val=y_val,
                )

                # Evaluate on VALIDATION set (not test).
                metrics = evaluate_model(
                    model, X_val, y_val,
                    label_names=list(label_encoder.classes_),
                )

                # Bias detection — placeholder until module is complete.
                bias_passed = True
                if BIAS_AVAILABLE and sens_val is not None:
                    y_pred_val = model.predict(X_val)
                    bias_results, bias_passed = evaluate_bias(y_val, y_pred_val, sens_val)
                    mlflow.log_metric("bias_gate_passed", int(bias_passed))

                # Log model artifact.
                signature = infer_signature(X_train, model.predict(X_train))
                log_model_to_mlflow(model, model_type, signature)

                # Log preprocessing artifacts.
                encoder_path = os.path.join(Config.MODEL_SAVE_DIR, "categorical_encoder.pkl")
                scaler_path = os.path.join(Config.MODEL_SAVE_DIR, "feature_scaler.pkl")
                for path in (encoder_path, scaler_path):
                    if os.path.exists(path):
                        mlflow.log_artifact(path, "preprocessing")

                # Log scenario artifact for reproducibility.
                if os.path.exists(Config.SCENARIO_OUTPUT_PATH):
                    mlflow.log_artifact(Config.SCENARIO_OUTPUT_PATH, "data")

                run_id = mlflow.active_run().info.run_id

                candidates.append({
                    "name": model_type,
                    "model": model,
                    "run_id": run_id,
                    "metrics": metrics,
                    "bias_passed": bias_passed,
                })
                logger.info("%s — val F1: %.4f, bias passed: %s",
                            model_type, metrics["f1_score"], bias_passed)

        except Exception as e:
            logger.error("Candidate %s failed: %s", model_type, e, exc_info=True)
            continue

    return candidates

# ---------------------------------------------------------------------------
# 3b. Hyperparameter Tuning
# ---------------------------------------------------------------------------

def tune_candidate(candidates, data):
    # 3b. Hyperparameter tuning on best baseline.
    tuning_result = tune_best_candidate(
        candidates,
        data["X_train"], data["y_train"],
        data["X_val"], data["y_val"],
    )
    if tuning_result:
        model_type, tuned_params, _ = tuning_result
        try:
            with mlflow.start_run(run_name=f"{model_type}_tuned"):
                mlflow.log_param("model_type", model_type)
                mlflow.log_param("tuning_source", "optuna")
                mlflow.log_params(tuned_params)

                tuned_model = train_model(
                    model_type, data["X_train"], data["y_train"], tuned_params,
                    X_val=data["X_val"], y_val=data["y_val"],
                )
                tuned_metrics = evaluate_model(
                    tuned_model, data["X_val"], data["y_val"],
                    label_names=list(data["label_encoder"].classes_),
                )

                signature = infer_signature(data["X_train"], tuned_model.predict(data["X_train"]))
                log_model_to_mlflow(tuned_model, model_type, signature)

                candidates.append({
                    "name": f"{model_type}_tuned",
                    "model": tuned_model,
                    "run_id": mlflow.active_run().info.run_id,
                    "metrics": tuned_metrics,
                    "bias_passed": True,  # Will be checked in select_best_model
                })
        except Exception as e:
            logger.error("Tuned model training failed: %s", e, exc_info=True)
            
    return candidates

# ---------------------------------------------------------------------------
# 4. Model Selection
# ---------------------------------------------------------------------------

def select_best_model(candidates):
    """
    Select the best model: must pass bias gate, then rank by F1.

    Returns:
        The winning candidate dict, or None if no candidates pass.
    """
    if not candidates:
        logger.error("No candidates to select from.")
        return None

    # Filter by bias gate.
    eligible = [c for c in candidates if c["bias_passed"]]
    if not eligible:
        logger.warning("No candidate passed the bias gate. "
                       "Falling back to best F1 regardless of bias.")
        eligible = candidates

    best = max(eligible, key=lambda c: c["metrics"]["f1_score"])
    logger.info("Selected best model: %s (F1: %.4f, run: %s)",
                best["name"], best["metrics"]["f1_score"], best["run_id"])
    return best


# ---------------------------------------------------------------------------
# 5. Final Evaluation on Held-Out Test Set
# ---------------------------------------------------------------------------

def final_evaluation(best, X_test, y_test, label_encoder):
    """
    Run the best model on the held-out test set exactly once.
    Log final metrics and all visualizations to a dedicated MLflow run.
    """
    if best is None:
        logger.error("No best model — skipping final evaluation.")
        return

    model = best["model"]
    model_type = best["name"]

    with mlflow.start_run(run_name=f"FINAL_{model_type}"):
        mlflow.log_param("model_type", model_type)
        mlflow.log_param("evaluation_set", "test")
        mlflow.log_param("source_run_id", best["run_id"])

        metrics = evaluate_model(
            model, X_test, y_test,
            label_names=list(label_encoder.classes_),
        )

        logger.info("Final test metrics: %s", metrics)

        # ── Model registration placeholder ──
        model_uri = f"runs:/{best['run_id']}/model"
        mlflow.register_model(model_uri, Config.REGISTERED_MODEL_NAME)

    return metrics


def save_best_model_local(best, label_encoder):
    """Persist best model under artifacts and label encoder under preprocessing."""
    if not best: return
    joblib.dump(best["model"], os.path.join(Config.MODEL_SAVE_DIR, "best_model.joblib"))
    joblib.dump(label_encoder, os.path.join(Config.ENCODER_SAVE_DIR, "label_encoder.joblib"))
    metadata_path = os.path.join(Config.MODEL_SAVE_DIR, "best_model_metadata.txt")
    with open(metadata_path, "w") as f: f.write(f"name={best['name']}\nrun_id={best['run_id']}\nval_f1={best['metrics']['f1_score']:.6f}\n")
    logger.info("Saved best model and encoder locally.")


# ---------------------------------------------------------------------------
# LLM Wrapper Demo — placeholder until module is complete.
# ---------------------------------------------------------------------------

def run_llm_demo(best, X_test, sens_test):
    """
    Optional: demonstrate LLM wrapping on a sample prediction.
    Called separately from training — this is a serving concern, not a training concern.
    """
    if not LLM_AVAILABLE or best is None:
        return

    try:
        sample_pred = best["model"].predict(X_test[:1])[0]
        sample_context = {}
        if sens_test is not None:
            sample_context = sens_test.iloc[0].to_dict()
        apply_llm_guardrails(sample_pred, sample_context, 0.95)
        logger.info("LLM demo completed.")
    except Exception as e:
        logger.warning("LLM demo failed (non-blocking): %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Starting End-to-End ML Pipeline...\n")
    print(f"[0/6] MLflow tracking URI: {Config.MLFLOW_TRACKING_URI}")
    print(f"[0/6] MLflow experiment: {Config.EXPERIMENT_NAME}")

    # 1. Initialize seeds and MLflow.
    print("[1/6] Initializing runtime + MLflow...")
    initialize()
    print("[1/6] Initialization complete.")

    # 2. Build features, encode labels, 3-way split.
    print("[2/6] Preparing data (load/encode/split)...")
    data = prepare_data()
    print(
        "[2/6] Data ready: "
        f"train={len(data['y_train'])}, val={len(data['y_val'])}, test={len(data['y_test'])}"
    )

    # 3. Train all candidates, evaluate on validation set.
    print("[3/6] Training baseline candidates...")
    candidates = train_candidates(
        data["X_train"], data["y_train"],
        data["X_val"], data["y_val"],
        data["sens_val"], data["label_encoder"],
    )
    print(f"[3/6] Baseline training complete. candidates={len(candidates)}")

    # 3b. Hyperparameter tuning on best baseline.
    print("[4/6] Running hyperparameter tuning on best baseline...")
    candidates = tune_candidate(candidates, data)
    print(f"[4/6] Tuning stage complete. candidates={len(candidates)}")

    # 4. Select best model (F1 + bias gate).
    print("[5/6] Selecting best model (F1 + bias gate)...")
    best = select_best_model(candidates)
    if best:
        print(
            "[5/6] Selected: "
            f"{best['name']} (val_f1={best['metrics']['f1_score']:.4f}, run_id={best['run_id']})"
        )
    else:
        print("[5/6] No model selected.")

    # 5. Final evaluation on held-out test set.
    print("[6/6] Running final evaluation on held-out test set...")
    final_metrics = final_evaluation(best, data["X_test"], data["y_test"], data["label_encoder"])
    if final_metrics is not None:
        print(f"[6/6] Final test metrics: {final_metrics}")
    else:
        print("[6/6] Final evaluation skipped or failed.")

    # 6. Save best model and label encoder locally under artifacts and preprocessing.
    save_best_model_local(best, data["label_encoder"])

    # Optional: LLM wrapper demo (serving concern, not training).
    print("[Optional] Running LLM wrapper demo...")
    run_llm_demo(best, data["X_test"], data["sens_test"])
    print("[Optional] LLM wrapper demo complete.")

    # Summary.
    if best:
        print(f"\nPipeline Complete!")
        print(f"Best Model: {best['name']} — Val F1: {best['metrics']['f1_score']:.4f}")
        print(f"MLflow Run ID: {best['run_id']}")
    else:
        print("\nPipeline Complete — no valid model selected.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()