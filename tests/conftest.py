import sys
from pathlib import Path

# Add the repository root to sys.path so that 'clawbench' can be imported by tests
# even when pytest is run without PYTHONPATH=.
sys.path.insert(0, str(Path(__file__).parent.parent))
