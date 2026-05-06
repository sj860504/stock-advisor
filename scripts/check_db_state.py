import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.strategy_state_repo import StrategyStateRepo

state = StrategyStateRepo.load("sean")
for k, v in state.items():
    print(f"--- {k} ---")
    if isinstance(v, dict):
        for sub_k, sub_v in v.items():
            print(f"  {sub_k}: {sub_v}")
    else:
        print(f"  {v}")
print("Done.")
