"""Build the paraphrase-search vectors. Already committed; rerun only to change the method.

Usage: python scripts/build_vectors.py OUTPUT.npy CODE [CODE ...]
Each verse's embeddings across the given translations are averaged, so a modern-worded
translation helps match how pastors actually paraphrase.
"""
import sys
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from verse_screen.bible import ROOT, Library  # noqa: E402
from verse_screen.semantic import EMBED_MODEL  # noqa: E402


def main(output, codes):
    library = Library()
    order = library.verse_list()
    model = TextEmbedding(EMBED_MODEL, cache_dir=str(ROOT / "models"), threads=6)
    total = np.zeros((len(order), 384), dtype=np.float32)
    for code in codes:
        texts = [library.text(code, ref) or library.text("KJV", ref) for ref in order]
        for index, vector in enumerate(model.embed(texts, batch_size=64)):
            total[index] += vector / np.linalg.norm(vector)
        print("embedded", code, flush=True)
    total /= np.linalg.norm(total, axis=1, keepdims=True)
    np.save(output, np.round(total * 127).astype(np.int8))
    print("saved", output, total.shape)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
