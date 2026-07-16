"""A stand-in for a real project's import cost.

Every task file in this comparison imports this module, so each runner pays the
same ~0.25 s of "project imports" the moment it loads its tasks. That is the
cost footman's completion avoids (it reads a cached manifest) and the incumbents
pay on every TAB (they re-import the tasks file to answer completion).
"""

import os
import time

# The benchmark sets COMPARISON_IMPORT_COST to isolate whether a runner pays the
# project-import cost during completion: measure completion at cost 0 vs 0.25 and
# read the delta. A runner that re-imports tasks per TAB shows a ~0.25 s delta.
_cost = float(os.environ.get("COMPARISON_IMPORT_COST", "0.25"))
if _cost:
    time.sleep(_cost)

VERSION = "1.0"
