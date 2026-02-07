"""
Data Ingestion Orchestrator for SavVio Pipeline
Routes data loading to appropriate source (GCS or API) based on environment configuration.
This is the main entry point for the data ingestion step in the Airflow DAG.
"""

import logging
from typing import Tuple
import pandas as pd

# Import configuration
import sys
from pathlib import Path

# Add parent directory to path to import from ingest module
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    ENVIRONMENT,
    DATA_SOURCE,
    GCS_BUCKET_NAME,
    FINANCIAL_BLOB,
    PRODUCT_BLOB,
    REVIEW_BLOB,
    FINANCIAL_RAW_PATH,
    PRODUCT_RAW_PATH,
    REVIEW_RAW_PATH,
    GCP_CREDENTIALS_PATH,
    GCP_PROJECT_ID,
    API_BASE_URL,
    API_KEY,
    API_TIMEOUT,
    FINANCIAL_API_ENDPOINT,
    PRODUCT_API_ENDPOINT,
    REVIEW_API_ENDPOINT,
    get_config_summary
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'
)
logger = logging.getLogger(__name__)


def load_from_gcs() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Load all datasets from Google Cloud Storage.
    
    Returns:
        Tuple of (financial_df, product_df, review_df)
    """
    logger.info("=" * 80)
    logger.info("DATA SOURCE: Google Cloud Storage (GCS)")
    logger.info("=" * 80)
    
    # Import GCS loader
    from ingest.gcs_loader import load_financial_data, load_product_data, load_review_data
    
    try:
        # Load financial data (CSV)
        financial_df = load_financial_data(
            bucket_name=GCS_BUCKET_NAME,
            blob_name=FINANCIAL_BLOB,
            destination_path=FINANCIAL_RAW_PATH,
            credentials_path=GCP_CREDENTIALS_PATH,
            project_id=GCP_PROJECT_ID
        )
        
        # Load product data (JSON)
        product_df = load_product_data(
            bucket_name=GCS_BUCKET_NAME,
            blob_name=PRODUCT_BLOB,
            destination_path=PRODUCT_RAW_PATH,
            credentials_path=GCP_CREDENTIALS_PATH,
            project_id=GCP_PROJECT_ID
        )
        
        # Load review data (JSON)
        review_df = load_review_data(
            bucket_name=GCS_BUCKET_NAME,
            blob_name=REVIEW_BLOB,
            destination_path=REVIEW_RAW_PATH,
            credentials_path=GCP_CREDENTIALS_PATH,
            project_id=GCP_PROJECT_ID
        )
        
        logger.info("=" * 80)
        logger.info("GCS DATA LOADING COMPLETE")
        logger.info(f"Financial records: {len(financial_df)}")
        logger.info(f"Product records: {len(product_df)}")
        logger.info(f"Review records: {len(review_df)}")
        logger.info("=" * 80)
        
        return financial_df, product_df, review_df
        
    except Exception as e:
        logger.error(f"Failed to load data from GCS: {e}")
        raise


def load_from_api() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Load all datasets from API endpoints.
    
    Returns:
        Tuple of (financial_df, product_df, review_df)
    """
    logger.info("=" * 80)
    logger.info("DATA SOURCE: REST API")
    logger.info("=" * 80)
    
    # Import API loader
    from ingest.api_loader import load_financial_data, load_product_data, load_review_data
    
    try:
        # Load financial data
        financial_df = load_financial_data(
            api_base_url=API_BASE_URL,
            endpoint=FINANCIAL_API_ENDPOINT.replace(API_BASE_URL, ''),  # Extract endpoint path
            destination_path=FINANCIAL_RAW_PATH,
            api_key=API_KEY,
            timeout=API_TIMEOUT
        )
        
        # Load product data
        product_df = load_product_data(
            api_base_url=API_BASE_URL,
            endpoint=PRODUCT_API_ENDPOINT.replace(API_BASE_URL, ''),
            destination_path=PRODUCT_RAW_PATH,
            api_key=API_KEY,
            timeout=API_TIMEOUT
        )
        
        # Load review data
        review_df = load_review_data(
            api_base_url=API_BASE_URL,
            endpoint=REVIEW_API_ENDPOINT.replace(API_BASE_URL, ''),
            destination_path=REVIEW_RAW_PATH,
            api_key=API_KEY,
            timeout=API_TIMEOUT
        )
        
        logger.info("=" * 80)
        logger.info("API DATA LOADING COMPLETE")
        logger.info(f"Financial records: {len(financial_df)}")
        logger.info(f"Product records: {len(product_df)}")
        logger.info(f"Review records: {len(review_df)}")
        logger.info("=" * 80)
        
        return financial_df, product_df, review_df
        
    except Exception as e:
        logger.error(f"Failed to load data from API: {e}")
        raise


def run_ingestion() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Main ingestion function that routes to appropriate data source.
    This is the function called by Airflow DAG.
    
    Returns:
        Tuple of (financial_df, product_df, review_df)
        
    Raises:
        ValueError: If data source is not supported
        Exception: If data loading fails
    """
    logger.info("╔" + "═" * 78 + "╗")
    logger.info("║" + " " * 25 + "SAVVIO DATA INGESTION" + " " * 32 + "║")
    logger.info("╚" + "═" * 78 + "╝")
    
    # Log configuration
    logger.info("\nConfiguration:")
    config_summary = get_config_summary()
    for key, value in config_summary.items():
        logger.info(f"  {key}: {value}")
    
    logger.info(f"\nEnvironment: {ENVIRONMENT}")
    logger.info(f"Data Source: {DATA_SOURCE}")
    
    try:
        # Route to appropriate loader based on data source
        if DATA_SOURCE.lower() == 'gcs':
            financial_df, product_df, review_df = load_from_gcs()
        elif DATA_SOURCE.lower() == 'api':
            financial_df, product_df, review_df = load_from_api()
        else:
            raise ValueError(
                f"Unsupported data source: {DATA_SOURCE}. "
                "Must be 'gcs' or 'api'"
            )
        
        # Validate that we got data
        if financial_df.empty:
            logger.warning("Financial data is empty!")
        if product_df.empty:
            logger.warning("Product data is empty!")
        if review_df.empty:
            logger.warning("Review data is empty!")
        
        logger.info("\n" + "=" * 80)
        logger.info("DATA INGESTION SUCCESSFUL")
        logger.info("=" * 80)
        
        return financial_df, product_df, review_df
        
    except Exception as e:
        logger.error("\n" + "=" * 80)
        logger.error("DATA INGESTION FAILED")
        logger.error(f"Error: {e}")
        logger.error("=" * 80)
        raise


def run_ingestion_task(**context):
    """
    Airflow-compatible task function.
    Uses XCom to pass data to downstream tasks.
    
    Args:
        **context: Airflow context (includes ti, ds, etc.)
    """
    try:
        # Run ingestion
        financial_df, product_df, review_df = run_ingestion()
        
        # Push data shapes to XCom for monitoring
        task_instance = context['ti']
        task_instance.xcom_push(key='financial_shape', value=financial_df.shape)
        task_instance.xcom_push(key='product_shape', value=product_df.shape)
        task_instance.xcom_push(key='review_shape', value=review_df.shape)
        
        # Push file paths to XCom for downstream tasks
        task_instance.xcom_push(key='financial_path', value=FINANCIAL_RAW_PATH)
        task_instance.xcom_push(key='product_path', value=PRODUCT_RAW_PATH)
        task_instance.xcom_push(key='review_path', value=REVIEW_RAW_PATH)
        
        logger.info("Data paths pushed to XCom for downstream tasks")
        
        return {
            'financial_records': len(financial_df),
            'product_records': len(product_df),
            'review_records': len(review_df),
            'status': 'success'
        }
        
    except Exception as e:
        logger.error(f"Ingestion task failed: {e}")
        raise


# Standalone execution for testing
if __name__ == "__main__":
    try:
        logger.info("Running data ingestion in standalone mode...")
        financial_df, product_df, review_df = run_ingestion()
        
        print("\n" + "=" * 80)
        print("INGESTION SUMMARY")
        print("=" * 80)
        print(f"\nFinancial Data:")
        print(f"  Shape: {financial_df.shape}")
        print(f"  Columns: {list(financial_df.columns)}")
        print(f"  Sample:\n{financial_df.head(3)}")
        
        print(f"\nProduct Data:")
        print(f"  Shape: {product_df.shape}")
        print(f"  Columns: {list(product_df.columns)}")
        print(f"  Sample:\n{product_df.head(3)}")
        
        print(f"\nReview Data:")
        print(f"  Shape: {review_df.shape}")
        print(f"  Columns: {list(review_df.columns)}")
        print(f"  Sample:\n{review_df.head(3)}")
        
        print("\n" + "=" * 80)
        print("✓ Standalone ingestion test completed successfully")
        print("=" * 80)
        
    except Exception as e:
        logger.error(f"Standalone execution failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)