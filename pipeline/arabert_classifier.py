"""
pipeline/arabert_classifier.py
--------------------------------
Zero-shot Arabic topic classifier backed by AraBERT embeddings.

Strategy:
  - For each topic, pre-compute a centroid embedding from its Arabic name +
    a few representative keywords (up to 5).
  - At inference time, embed the document text (title + first 512 tokens) and
    return the topic with the highest cosine similarity as the predicted label,
    with the similarity score as the confidence.

The model is loaded ONCE on first use (lazy init) and reused across all
documents processed in the same pipeline run.

Device priority: MPS (Apple Silicon) → CUDA → CPU.

Usage::
    from pipeline.arabert_classifier import AraBERTClassifier

    clf = AraBERTClassifier.get_instance(topics)   # singleton
    topic_slug, confidence = clf.classify(title_ar, normalized_text)
"""
from __future__ import annotations

import logging
from typing import Optional

import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

_MODEL_NAME = "aubmindlab/bert-base-arabertv2"
_MAX_TOKENS  = 512   # BERT hard limit


def _pick_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class AraBERTClassifier:
    """
    Singleton AraBERT zero-shot topic classifier.

    Call ``AraBERTClassifier.get_instance(topics)`` once at structurer
    initialisation; subsequent calls return the same object.
    """

    _instance: Optional["AraBERTClassifier"] = None

    # ── Singleton factory ──────────────────────────────────────────────────

    @classmethod
    def get_instance(cls, topics: list[dict]) -> "AraBERTClassifier":
        if cls._instance is None:
            cls._instance = cls(topics)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Force reload — useful in tests."""
        cls._instance = None

    # ── Construction ───────────────────────────────────────────────────────

    def __init__(self, topics: list[dict]) -> None:
        from transformers import AutoTokenizer, AutoModel  # deferred import

        self.device = _pick_device()
        logger.info(f"[AraBERT] Loading model on device={self.device}")

        self.tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
        self.model = AutoModel.from_pretrained(_MODEL_NAME)
        self.model.to(self.device)
        self.model.eval()

        # Pre-compute topic label embeddings
        self._topics      = topics
        self._topic_slugs: list[str]         = []
        self._topic_embs:  torch.Tensor | None = None
        self._build_topic_embeddings()

        logger.info(f"[AraBERT] Ready — {len(self._topic_slugs)} topic embeddings on {self.device}")

    # ── Embedding helpers ──────────────────────────────────────────────────

    def _embed(self, texts: list[str]) -> torch.Tensor:
        """
        Return mean-pooled CLS embeddings for a list of texts.
        Shape: (len(texts), 768).
        """
        enc = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=_MAX_TOKENS,
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            out = self.model(**enc)

        # Mean-pool over non-padding tokens (more robust than CLS alone)
        mask = enc["attention_mask"].unsqueeze(-1).float()
        pooled = (out.last_hidden_state * mask).sum(1) / mask.sum(1)
        return F.normalize(pooled, dim=-1)

    def _build_topic_embeddings(self) -> None:
        """Pre-compute one embedding per topic from its name + top-5 keywords."""
        label_texts: list[str] = []
        slugs: list[str] = []

        for td in self._topics:
            name_ar   = td.get("name_ar", "")
            keywords  = td.get("keywords_ar", [])[:5]
            label_str = name_ar
            if keywords:
                label_str += " " + " ".join(keywords)
            label_texts.append(label_str)
            slugs.append(td["id"])

        if not label_texts:
            return

        self._topic_slugs = slugs
        self._topic_embs  = self._embed(label_texts).cpu()   # keep on CPU to save GPU VRAM

    # ── Public API ─────────────────────────────────────────────────────────

    def classify(self, title_ar: str, normalized_text: str) -> tuple[str, float]:
        """
        Classify a document and return (topic_slug, confidence).

        Input text is formed from: title_ar + first 400 chars of normalized_text.
        Confidence = cosine similarity ∈ [0, 1] (normalised embeddings → dot product).
        """
        if self._topic_embs is None or not self._topic_slugs:
            return ("", 0.0)

        query = f"{title_ar} {normalized_text[:400]}".strip()
        q_emb = self._embed([query]).cpu()   # (1, 768)

        # Cosine similarity: since both tensors are L2-normalised, dot product = cosine
        sims = (self._topic_embs @ q_emb.T).squeeze(1)   # (n_topics,)
        best_idx = int(sims.argmax())
        confidence = float(sims[best_idx])

        # Clamp to [0, 1] — cosine can be slightly negative for unrelated topics
        confidence = round(max(0.0, min(1.0, confidence)), 4)
        return (self._topic_slugs[best_idx], confidence)
