"""Let test modules import their siblings (dngfixture, test_bake helpers)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
