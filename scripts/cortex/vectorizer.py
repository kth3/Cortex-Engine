"""Compatibility wrapper for embedding batch vectorization."""
from cortex.embeddings.hardware import detect_gpu, _maybe_flush_gpu
from cortex.embeddings.batch import batch_vectorize_nodes, batch_vectorize_memories, GPU_THRESHOLD
