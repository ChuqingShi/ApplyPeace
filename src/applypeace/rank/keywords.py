"""Stage 2: free keyword pre-rank (TF-IDF cosine between your resume and each job description)."""

from __future__ import annotations

import math
import re
from collections import Counter

STOP = set("""a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he
her here hers him his how i if in into is it its itself just me more most my no nor not now of off on once only
or other our ours out over own same she should so some such than that the their theirs them then there these
they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours experience work working team teams role job company year years including across within
strong ability skills preferred required requirements responsibilities qualifications""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z][a-z0-9+#.]{1,}", (text or "").lower()) if t not in STOP]


def tfidf_scores(resume: str, documents: dict[str, str]) -> dict[str, float]:
    """Cosine similarity (0..1) of each document to the resume, IDF taken over the documents."""
    doc_tokens = {k: Counter(tokens(v)) for k, v in documents.items()}
    n = len(doc_tokens) + 1
    df = Counter()
    for counts in doc_tokens.values():
        df.update(counts.keys())
    idf = {term: math.log(n / (1 + freq)) + 1 for term, freq in df.items()}

    def vector(counts: Counter) -> dict[str, float]:
        return {t: (1 + math.log(c)) * idf.get(t, math.log(n) + 1) for t, c in counts.items()}

    rv = vector(Counter(tokens(resume)))
    rnorm = math.sqrt(sum(v * v for v in rv.values())) or 1.0
    scores = {}
    for key, counts in doc_tokens.items():
        dv = vector(counts)
        dot = sum(w * rv[t] for t, w in dv.items() if t in rv)
        dnorm = math.sqrt(sum(v * v for v in dv.values())) or 1.0
        scores[key] = dot / (rnorm * dnorm)
    return scores
