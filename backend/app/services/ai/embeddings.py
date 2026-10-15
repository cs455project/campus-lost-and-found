"""Embeddings: turning text into a list of numbers (a "vector").

Two texts that MEAN similar things get vectors that point in similar
directions, even with different words ("black backpack" vs "dark bag").
Comparing two vectors is then just a dot product: 1.0 = same direction
(very similar), 0 = unrelated. This is called "cosine similarity".

Two embedders, chosen with EMBEDDING_BACKEND in .env:

  local    fastembed + BAAI/bge-small-en-v1.5: a small open-source model
           (~70 MB, ONNX, CPU only, no PyTorch). Downloaded once into
           backend/.cache/models the first time it's used. Understands meaning.

  hashing  a tiny built-in fallback with no download: it only notices
           shared words. Used automatically if the local model can't load
           (e.g. no internet on first run), and in the tests.

Every vector is stored with the name of the model that made it, because
vectors from different models can't be compared.
"""

import hashlib
import logging
import re
import threading
from typing import Protocol

import numpy as np

from app.core.config import get_settings

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str  # stored next to every vector (items.embedding_model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One unit-length vector per text."""
        ...


def _normalise(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    return vector / length if length > 0 else vector


class HashingEmbedder:
    """Words (and pairs of neighbouring words) are hashed into 384 slots.
    Texts sharing words get similar vectors. No understanding of meaning,
    but free, instant, and always available."""

    name = "hashing-v1"
    DIM = 384
    _WORD = re.compile(r"[a-z0-9]+")
    # Very common words that say nothing about the item.
    _SKIP = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "with", "my", "is", "it",
             "to", "for", "near", "from", "this", "that", "was", "has", "have", "i", "lost", "found"}

    def _tokens(self, text: str) -> list[str]:
        words = [w.rstrip("s") if len(w) > 3 else w for w in self._WORD.findall(text.lower())]
        words = [w for w in words if w not in self._SKIP]
        return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vector = np.zeros(self.DIM, dtype=np.float32)
            for token in self._tokens(text):
                digest = hashlib.md5(token.encode()).digest()
                slot = int.from_bytes(digest[:4], "little") % self.DIM
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[slot] += sign * (0.5 if "_" in token else 1.0)
            out.append(_normalise(vector).tolist())
        return out


class LocalModelEmbedder:
    """fastembed (ONNX Runtime) with a small open-source model."""

    def __init__(self, model_name: str, cache_dir):
        from fastembed import TextEmbedding  # imported here: only needed for this backend

        self.name = model_name
        self._model = TextEmbedding(model_name, cache_dir=str(cache_dir))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_normalise(np.asarray(v, dtype=np.float32)).tolist() for v in self._model.embed(texts)]


_lock = threading.Lock()
_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """The embedder chosen in settings, loaded once. If the local model can't
    be loaded, fall back to hashing (and say so in the log), so matching
    keeps working."""
    global _embedder
    with _lock:
        if _embedder is None:
            settings = get_settings()
            if settings.embedding_backend == "local":
                try:
                    _embedder = LocalModelEmbedder(settings.embedding_model, settings.embedding_cache_dir)
                    log.info("Embeddings: local model %s", settings.embedding_model)
                except Exception:
                    log.exception("Could not load embedding model %s; using the hashing fallback",
                                  settings.embedding_model)
                    _embedder = HashingEmbedder()
            else:
                _embedder = HashingEmbedder()
        return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Swap the embedder (tests). None = load from settings again."""
    global _embedder
    with _lock:
        _embedder = embedder
