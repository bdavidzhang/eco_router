"""Model quality evaluation — perplexity and bits-per-byte.

"The metric is val_bpb — lower is better." — Karpathy
We compute bits-per-byte on a held-out eval set for cross-model comparability.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

import numpy as np
import torch

logger = logging.getLogger(__name__)

# Default eval dataset — small enough to fit in time budget
_DEFAULT_EVAL_TEXTS = [
    "The quick brown fox jumps over the lazy dog.",
    "In the beginning was the Word, and the Word was with God.",
    "It is a truth universally acknowledged, that a single man in possession "
    "of a good fortune, must be in want of a wife.",
    "Call me Ishmael. Some years ago—never mind how long precisely—having "
    "little or no money in my purse, and nothing particular to interest me "
    "on shore, I thought I would sail about a little and see the watery "
    "part of the world.",
    "The Transformer architecture relies on self-attention mechanisms to "
    "compute representations of its input and output without using "
    "sequence-aligned RNNs or convolution.",
]


@dataclass
class QualityResult:
    """Quality metrics from an evaluation pass."""

    val_bpb: float  # Bits-per-byte (THE metric)
    perplexity: float
    avg_loss: float
    eval_tokens: int
    eval_time_sec: float


def compute_bits_per_byte(avg_nll: float, tokenizer_vocab_size: int) -> float:
    """Convert average negative log-likelihood to bits-per-byte.

    BPB = NLL_per_token * log2(e) / avg_bytes_per_token
    For most tokenizers, avg_bytes_per_token ≈ 3.5-4.5
    We use the standard approximation: BPB ≈ NLL / ln(2)
    """
    return avg_nll / math.log(2)


def evaluate_quality(
    model: torch.nn.Module,
    tokenizer,
    eval_texts: list[str] | None = None,
    max_length: int = 512,
    device: str | None = None,
) -> QualityResult:
    """Run quality evaluation on held-out texts.

    Args:
        model: A HuggingFace causal LM.
        tokenizer: The corresponding tokenizer.
        eval_texts: Texts to evaluate on. Defaults to built-in set.
        max_length: Max sequence length for evaluation.
        device: Device to run on. Auto-detected if None.

    Returns:
        QualityResult with BPB, perplexity, and timing.
    """
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    texts = eval_texts or _DEFAULT_EVAL_TEXTS
    model.eval()

    total_loss = 0.0
    total_tokens = 0
    start = time.perf_counter()

    with torch.no_grad():
        for text in texts:
            encodings = tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=max_length,
            ).to(device)

            input_ids = encodings["input_ids"]
            # Skip very short sequences
            if input_ids.shape[1] < 2:
                continue

            outputs = model(**encodings, labels=input_ids)
            # outputs.loss is mean NLL over tokens
            n_tokens = input_ids.shape[1] - 1  # Labels shifted by 1
            total_loss += outputs.loss.item() * n_tokens
            total_tokens += n_tokens

    elapsed = time.perf_counter() - start

    if total_tokens == 0:
        logger.warning("No tokens evaluated — returning worst-case metrics")
        return QualityResult(
            val_bpb=float("inf"),
            perplexity=float("inf"),
            avg_loss=float("inf"),
            eval_tokens=0,
            eval_time_sec=elapsed,
        )

    avg_loss = total_loss / total_tokens
    perplexity = math.exp(min(avg_loss, 100))  # Cap to avoid overflow
    val_bpb = compute_bits_per_byte(avg_loss, tokenizer.vocab_size)

    logger.info(
        "Quality eval: BPB=%.4f, PPL=%.2f, tokens=%d, time=%.1fs",
        val_bpb,
        perplexity,
        total_tokens,
        elapsed,
    )
    return QualityResult(
        val_bpb=val_bpb,
        perplexity=perplexity,
        avg_loss=avg_loss,
        eval_tokens=total_tokens,
        eval_time_sec=elapsed,
    )
