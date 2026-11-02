import os
import tempfile
from pathlib import Path

# Ensure DATA_DIR is isolated to a clean temp directory during pytest collection
if "DATA_DIR" not in os.environ or not os.environ["DATA_DIR"]:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="matinee-test-conftest-")
