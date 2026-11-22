"""Paraphrase catch: a verse must match by meaning (vectors) and share key words (FTS5).

Requiring both signals rejects most ordinary sermon talk that merely sounds religious.
"""
import re
from dataclasses import dataclass

import numpy as np

from .bible import ROOT, Ref

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_DIR = ROOT / "models"
POOL = 50
RERANK = 12
COVERAGE_WEIGHT = 0.3
STOP_WORDS = set("""a an the and or but of to in on at for with is are was were be been it its this that these those i
you he she we they me my your his her our their him them who whom which what when where why how not no do does did will
would shall should can could may might have has had as by from so if than then there here all any some nobody everyone
know says said say about like just very really""".split())


def load_embedder(threads=2):
    from fastembed import TextEmbedding

    options = dict(cache_dir=str(MODEL_DIR), threads=threads)
    try:
        return TextEmbedding(EMBED_MODEL, local_files_only=True, **options)
    except Exception:  # not cached yet; only possible with internet
        return TextEmbedding(EMBED_MODEL, **options)


@dataclass(frozen=True)
class Candidate:
    ref: Ref
    score: float


class SemanticIndex:
    def __init__(self, library):
        rows = library.vector_rows()
        if not rows:
            raise RuntimeError("no vectors in the database")
        self.library = library
        self.refs = [(book, chapter, verse) for book, chapter, verse, _ in rows]
        blob = b"".join(row[3] for row in rows)
        self.vectors = np.frombuffer(blob, dtype=np.int8).reshape(len(rows), -1).astype(np.float32) / 127.0
        self.model = load_embedder()

    def _keyword_refs(self, text):
        words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP_WORDS]
        if not words:
            return set()
        return {tuple(row[:3]) for row in self.library.keyword_search(" OR ".join(words), POOL)}

    def _coverage(self, words, ref):
        """Share of the spoken key words found in this verse, matching on word starts."""
        text = " ".join(self.library.text(code, ref) or "" for code in self.library.codes).lower()
        return sum(word[:4] in text for word in words) / len(words) if words else 0.0

    def scores(self, text, refs):
        """Meaning score of text against each given verse (its first verse for a range)."""
        query = next(iter(self.model.embed([text])))
        query = (query / np.linalg.norm(query)).astype(np.float32)
        where = {ref: i for i, ref in enumerate(self.refs)}
        return [float(self.vectors[where[(r.book, r.chapter, r.verse)]] @ query)
                if (r.book, r.chapter, r.verse) in where else 0.0 for r in refs]

    def search(self, text, limit=2):
        """Best verses by meaning that also share key words, best first. Score is meaning only."""
        query = next(iter(self.model.embed([text])))
        scores = self.vectors @ (query / np.linalg.norm(query)).astype(np.float32)
        keyword_hits = self._keyword_refs(text)
        agreed = [i for i in np.argsort(-scores)[:POOL] if self.refs[i] in keyword_hits][:RERANK]
        words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP_WORDS]
        ranked = sorted(((float(scores[i]) + COVERAGE_WEIGHT * self._coverage(words, Ref(*self.refs[i])), i)
                         for i in agreed), reverse=True)
        return [Candidate(Ref(*self.refs[i]), float(scores[i])) for _, i in ranked[:limit]]
