"""
Kaggle Data Loader for SavVio Pipeline
Handles downloading datasets directly from Kaggle and formatting them 
correctly for the preprocessing pipeline.
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Optional
import pandas as pd
import kagglehub

# Configure logging
from src.utils import setup_logging
setup_logging()
logger = logging.getLogger(__name__)


def load_financial_data(
    dataset_handle: str,
    destination_path: str
) -> pd.DataFrame:
    """
    Download financial data from Kaggle and save it to the destination path.
    
    Args:
        dataset_handle: Kaggle dataset handle (e.g., 'miadul/personal-finance-ml-dataset')
        destination_path: Local destination path (CSV)
        
    Returns:
        pd.DataFrame: Financial data
    """
    logger.info("=" * 60)
    logger.info(f"Loading Financial Data from Kaggle: {dataset_handle}")
    logger.info("=" * 60)
    
    try:
        # Download dataset using kagglehub
        logger.info("Downloading dataset via kagglehub...")
        cache_dir = kagglehub.dataset_download(dataset_handle)
        logger.info(f"Dataset downloaded to cache: {cache_dir}")
        
        # Find the CSV file in the downloaded directory
        csv_files = list(Path(cache_dir).glob("*.csv"))
        if not csv_files:
            raise FileNotFoundError(f"No CSV file found in downloaded Kaggle dataset at {cache_dir}")
            
        source_csv = csv_files[0]
        logger.info(f"Found source CSV: {source_csv.name}")
        
        # Ensure destination directory exists
        Path(destination_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Copy file to destination
        shutil.copy2(source_csv, destination_path)
        logger.info(f"Data copied to {destination_path}")
        
        # Load and verify
        df = pd.read_csv(destination_path)
        logger.info(f"Financial data loaded successfully: {df.shape}")
        
        return df
        
    except Exception as e:
        logger.error(f"Failed to load financial data from Kaggle: {e}")
        raise


def load_product_data(
    dataset_handle: str,
    destination_path: str
) -> pd.DataFrame:
    """
    Download product data from Kaggle, convert it from CSV to JSONL, 
    and save it to the destination path.
    
    Args:
        dataset_handle: Kaggle dataset handle (e.g., 'lokeshparab/amazon-products-dataset')
        destination_path: Local destination path (JSONL)
        
    Returns:
        pd.DataFrame: Product data
    """
    logger.info("=" * 60)
    logger.info(f"Loading Product Data from Kaggle: {dataset_handle}")
    logger.info("=" * 60)
    
    try:
        # Download dataset using kagglehub
        logger.info("Downloading dataset via kagglehub...")
        cache_dir = kagglehub.dataset_download(dataset_handle)
        
        # Usually 'amazon_products.csv' or similar is the main file
        # We will look for anything containing 'product' or just use the largest CSV
        csv_files = list(Path(cache_dir).glob("*product*.csv"))
        if not csv_files:
            # Fallback to the first CSV found
            csv_files = list(Path(cache_dir).glob("*.csv"))
            
        if not csv_files:
            raise FileNotFoundError(f"No CSV file found in downloaded Kaggle dataset at {cache_dir}")
            
        # Prioritize files with 'product' in name, or take the first one
        source_csv = sorted(csv_files, key=lambda p: 'product' not in p.name.lower())[0]
        logger.info(f"Found source CSV for products: {source_csv.name}")
        
        # Ensure destination directory exists
        Path(destination_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Load CSV and save as JSONL to match pipeline expectations
        logger.info("Converting CSV to JSONL format...")
        
        # Use chunking to prevent memory issues with large Kaggle datasets
        chunks = pd.read_csv(source_csv, chunksize=100000, low_memory=False)
        first_chunk = True
        
        # Create an empty dataframe to hold a sample for the return value
        df_sample = None
        
        # Write chunks to JSONL
        with open(destination_path, 'w', encoding='utf-8') as f:
            for chunk in chunks:
                if first_chunk:
                    df_sample = chunk.copy()
                    first_chunk = False
                
                # Write lines
                json_str = chunk.to_json(orient='records', lines=True, force_ascii=False)
                f.write(json_str)
                if not json_str.endswith('\n'):
                    f.write('\n')
                    
        logger.info(f"Data converted and saved to {destination_path}")
        
        # Return the sample dataframe (to satisfy the caller expecting a DataFrame)
        # We return the sample to avoid loading 5GB into memory just for logging
        if df_sample is not None:
             logger.info(f"Product data loaded (returning sample dataframe chunk: {df_sample.shape})")
             return df_sample
        return pd.DataFrame()
        
    except Exception as e:
        logger.error(f"Failed to load product data from Kaggle: {e}")
        raise


def load_review_data(
    dataset_handle: str,
    destination_path: str
) -> pd.DataFrame:
    """
    Download review data from Kaggle, convert it from CSV to JSONL,
    and save it to the destination path.
    
    Args:
        dataset_handle: Kaggle dataset handle
        destination_path: Local destination path (JSONL)
        
    Returns:
        pd.DataFrame: Review data
    """
    logger.info("=" * 60)
    logger.info(f"Loading Review Data from Kaggle: {dataset_handle}")
    logger.info("=" * 60)
    
    try:
        # Download dataset using kagglehub
        logger.info("Downloading dataset via kagglehub...")
        cache_dir = kagglehub.dataset_download(dataset_handle)
        
        # We look for a reviews csv file. The amazon dataset often splits products and reviews.
        csv_files = list(Path(cache_dir).glob("*review*.csv"))
        
        if not csv_files:
            # If no explicit reviews file exists, it might be in an 'amazon_categories.csv' 
            # or we create a dummy file to satisfy the pipeline if it doesn't strictly exist 
            # in this specific Kaggle dataset variant.
            logger.warning("No explicit review CSV found. Checking all CSVs...")
            all_csvs = list(Path(cache_dir).glob("*.csv"))
            source_csv = sorted(all_csvs, key=lambda p: os.path.getsize(p), reverse=True)[0]
            logger.info(f"Using fallback CSV (largest file) for reviews: {source_csv.name}")
        else:
            source_csv = csv_files[0]
            logger.info(f"Found source CSV for reviews: {source_csv.name}")
        
        # Ensure destination directory exists
        Path(destination_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Load CSV and save as JSONL
        logger.info("Converting CSV to JSONL format...")
        
        chunks = pd.read_csv(source_csv, chunksize=100000, low_memory=False)
        first_chunk = True
        df_sample = None
        
        with open(destination_path, 'w', encoding='utf-8') as f:
            for chunk in chunks:
                if first_chunk:
                    df_sample = chunk.copy()
                    first_chunk = False
                
                json_str = chunk.to_json(orient='records', lines=True, force_ascii=False)
                f.write(json_str)
                if not json_str.endswith('\n'):
                    f.write('\n')
                    
        logger.info(f"Data converted and saved to {destination_path}")
        
        if df_sample is not None:
             logger.info(f"Review data loaded (returning sample dataframe chunk: {df_sample.shape})")
             return df_sample
        return pd.DataFrame()
        
    except Exception as e:
        logger.error(f"Failed to load review data from Kaggle: {e}")
        raise
