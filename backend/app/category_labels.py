"""Shared display dictionary; source codes remain unchanged in SQL/CSV."""
import json
from pathlib import Path
LABELS=json.loads((Path(__file__).resolve().parents[2]/'frontend/src/categoryLabels.json').read_text())
def category_label(code):
    return LABELS.get(code,code)
