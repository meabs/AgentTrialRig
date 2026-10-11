"""A stand-in for a coding agent: prints Claude-style stream-json so trials can be tested without a real CLI.

Behaviour is driven by the prompt: it answers with whatever follows "ANSWER:", spends the tokens named by
"TOKENS:", and calls a shell tool when the repository contains a marker file (so an arm can change what it does).
"""
import json
import os
import re
import sys

prompt = sys.argv[1]
answer = (re.search(r"ANSWER:\s*(.*?)(?:TOKENS:|VERIFICATION INSTRUCTION|$)", prompt, re.S) or [None, "no answer"])[1].strip()
claim = re.search(r"CLAIM:\s*(\{.*?\})", prompt)
if claim:
    answer += "\nVERIFICATION: " + claim.group(1)
tokens = int((re.search(r"TOKENS:\s*(\d+)", prompt) or [None, "1000"])[1])
if os.path.exists("MARKER"):
    tokens = int(tokens * 0.5)
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "./.tool/bin/kg callers x"}}]}}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer, "num_turns": 2, "total_cost_usd": 0.01,
                  "usage": {"input_tokens": tokens, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}))
