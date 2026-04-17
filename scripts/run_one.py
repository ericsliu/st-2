"""Execute exactly one turn."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.auto_turn import run_one_turn

result = run_one_turn()
print(f"Result: {result}")
