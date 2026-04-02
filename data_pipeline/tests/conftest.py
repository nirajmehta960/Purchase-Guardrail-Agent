import sys
import os

# Add dags/ to path so 'src' package is importable
dags_dir = os.path.join(os.path.dirname(__file__), '..', 'dags')
dags_dir = os.path.abspath(dags_dir)
if dags_dir not in sys.path:
    sys.path.insert(0, dags_dir)

# Add dags/src to path so direct module imports work
src_dir = os.path.join(dags_dir, 'src')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
