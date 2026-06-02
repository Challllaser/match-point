import os
import sys

PROJECT_DIR = "/home/YOUR_USERNAME/match-point"

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

os.environ.setdefault("DATA_DIR", PROJECT_DIR)

from app import application
