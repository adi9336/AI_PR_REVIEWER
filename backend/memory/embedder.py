"""embedder — text → VECTOR(256) embedding.

Production: OpenAI text-embedding-3-large (256 dimensions).
Fallback: deterministic hash-based embedder (no API key needed).

The embedder interface is an abstract class; concrete implementations are
selected by ``get_embedder()`` based on whether OPENAI_API_KEY is set.
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
from numpy import ndarray


class Embedder(ABC):
    """Abstract embedder interface. Returns a 256-dim float32 vector."""

    @abstractmethod
    def embed(self, text: str) -> ndarray:
        """Embed a single text → 256-dim float32 vector."""
        ...

    def embed_batch(self, texts: list[str]) -> list[ndarray]:
        """Embed multiple texts. Default: sequential; override for batched APIs."""
        return [self.embed(t) for t in texts]

    @staticmethod
    def dims() -> int:
        return 256


class OpenAIEmbedder(Embedder):
    """Production embedder using OpenAI text-embedding-3-large (256-dim).

    The API returns 3072-dim by default; we request 256 via the
    ``dimensions`` parameter supported by text-embedding-3-large.
    """

    def __init__(self, api_key: str | None = None, model: str = "text-embedding-3-large") -> None:
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError("OPENAI_API_KEY is required for OpenAIEmbedder")
        self._client: Any = OpenAI(api_key=key)
        self._model = model

    def embed(self, text: str) -> ndarray:
        resp = self._client.embeddings.create(
            model=self._model,
            input=text,
            dimensions=self.dims(),
        )
        vec = resp.data[0].embedding
        return np.array(vec, dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> list[ndarray]:
        if not texts:
            return []
        resp = self._client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self.dims(),
        )
        return [np.array(d.embedding, dtype=np.float32) for d in resp.data]


class HashEmbedder(Embedder):
    """Deterministic hash-based embedder — no API key needed.

    Not semantically meaningful for cosine similarity (unlike OpenAI), but
    it IS deterministic and provides smooth gradients for exact-near
    matches and high lexical overlap. Used when OPENAI_API_KEY is absent
    so the system is testable without external dependencies.

    Strategy: split text into token shingles, hash each shingle into the
    256-dim vector, and accumulate. Two texts with similar tokens will
    have similar vectors; identical texts get identical vectors.
    """

    def embed(self, text: str) -> ndarray:
        vec = np.zeros(self.dims(), dtype=np.float32)
        # Token shingles: sliding window of 2-grams, plus single tokens
        tokens = _simple_tokenize(text)
        if not tokens:
            return vec

        # Single tokens
        for token in tokens:
            hash_into(token, vec)
        # 2-gram shingles
        for i in range(len(tokens) - 1):
            hash_into(f"{tokens[i]} {tokens[i + 1]}", vec)
        # 3-gram shingles
        for i in range(len(tokens) - 2):
            hash_into(f"{tokens[i]} {tokens[i + 1]} {tokens[i + 2]}", vec)

        # L2 normalize so cosine distance works correctly
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return vec


def _simple_tokenize(text: str) -> list[str]:
    """Lowercase + split on non-alphanumeric. Keeps underscore-joined identifiers."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "_":
            current.append(ch)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))
    return tokens


def hash_into(token: str, vec: ndarray) -> None:
    """Hash a token into the 256-dim vector, setting ±1 at hash positions."""
    h = hashlib.sha256(token.encode("utf-8")).digest()
    # Use 4 bytes each for position (0..255) and sign
    pos = int.from_bytes(h[0:1], "little") % vec.shape[0]
    sign = 1.0 if (h[4] & 1) else -1.0
    vec[pos] += sign


def get_embedder() -> Embedder:
    """Return the best available embedder.

    OpenAIEmbedder if OPENAI_API_KEY is set; HashEmbedder otherwise.
    """
    if os.getenv("OPENAI_API_KEY"):
        return OpenAIEmbedder()
    return HashEmbedder()