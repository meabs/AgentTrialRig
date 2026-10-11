"""A stand-in agent for exercising the regression workflow without credentials: answers from a fixed script.

DEGRADE=1 makes it answer badly, as a worse model or a broken instruction file would.
"""
import json
import os
import sys

answer = "I could not find them." if os.environ.get("DEGRADE") == "1" else "The classes are Option and Argument."
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer, "num_turns": 2, "total_cost_usd": 0.0,
                  "usage": {"input_tokens": 1000, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}))
