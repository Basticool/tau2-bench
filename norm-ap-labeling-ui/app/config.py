import os
from pathlib import Path

_APP_DIR = Path(__file__).parent        # norm-ap-labeling-ui/app/
_APP_ROOT = _APP_DIR.parent             # norm-ap-labeling-ui/
_TAU2_DIR = _APP_ROOT.parent            # tau2-bench/
_NORM_COMPLIANCE_DIR = _TAU2_DIR.parent  # norm_compliance/ (repo root)

# Data source paths — override with env vars:
#   TRACES_PATH   path to non_compliant_traces.json (JSONL)
#   NORMS_PATH    path to combined_retail_norms.json
#   PROPS_PATH    path to atomic_propositions.json
DEFAULT_TRACES_PATH = os.environ.get(
    "TRACES_PATH",
    str(_TAU2_DIR / "results" / "non_compliant_traces.json"),
)
DEFAULT_NORMS_PATH = os.environ.get(
    "NORMS_PATH",
    str(_TAU2_DIR / "norms_and_propositions" / "combined_retail_norms.json"),
)
DEFAULT_PROPS_PATH = os.environ.get(
    "PROPS_PATH",
    str(_APP_ROOT / "resources" / "atomic_propositions.json"),
)

# norm_compliance repo root (needed by auto_labeler to import sensors.py)
NORM_COMPLIANCE_REPO = str(_NORM_COMPLIANCE_DIR)

# Storage
RESOURCES_DIR = _APP_ROOT / "resources"
LABELS_DIR = RESOURCES_DIR / "labels"
JOBS_DIR = RESOURCES_DIR / "jobs"
USERS_FILE = RESOURCES_DIR / "users.jsonl"
