import sys
import os

# Add dags/ to sys.path so Python finds 'src' as a proper package (dags/src/).
# IMPORTANT: do NOT also add dags/src/ here.  When both are present, Python
# creates a namespace package for 'src' (because dags/src/src/ doesn't exist
# in the second entry), which causes:
#   ModuleNotFoundError: No module named 'src.ingestion'; 'src' is not a package
dags_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'dags'))
if dags_dir not in sys.path:
    sys.path.insert(0, dags_dir)
