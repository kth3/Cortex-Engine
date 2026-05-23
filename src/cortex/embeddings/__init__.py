"""
Cortex Embeddings Package
"""
from .provider import get_embeddings, preload_model
from .hardware import detect_gpu, release_gpu

__all__ = [
    "get_embeddings",
    "preload_model",
    "detect_gpu",
    "release_gpu",
]
