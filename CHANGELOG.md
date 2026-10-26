# Changelog

## 0.8.0

- **Per-arm prompts.** An arm can set `prompt_prefix` and `prompt_suffix`, added to every question's prompt in that arm. Before this, the key was accepted and silently ignored, so an arm defined only by its prompt ran exactly like the baseline.
- **Unknown keys are errors.** Every table is checked against the keys the loader reads; a misspelt or unsupported option now stops the trial with a message naming it, instead of being dropped.
- **The prompt is saved.** Each run writes the exact prompt it sent to `prompt.txt` beside `run.json`.

## 0.7.0

Claim-match labels renamed from overclaim/underclaim to false_pass/false_fail (the comparison is the agent's report against the independent check, not claim honesty). Old result files are read with the old labels mapped automatically.
