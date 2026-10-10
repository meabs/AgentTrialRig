"""Score an answer against the answer key written in the trial file."""

from __future__ import annotations

import re

from .config import Question


def score(q: Question, answer: str) -> dict:
    hits = [bool(re.search(p, answer, re.I)) for p in q.expect]
    recall = (sum(hits) / len(hits)) if hits else None
    forbidden = [p for p in q.forbid if re.search(p, answer, re.I)]
    correct = None
    if recall is not None:
        correct = recall >= q.min_recall and not forbidden
    elif q.forbid:
        correct = not forbidden
    return {"recall": recall, "missing": [p for p, h in zip(q.expect, hits) if not h], "forbidden_hit": forbidden, "correct": correct}
