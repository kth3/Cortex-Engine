"""Compatibility wrapper for embedding provider."""
from cortex.embeddings.provider import (
    get_embeddings, 
    preload_model, 
    _load_model, 
    MODEL_ID, 
    ENV_PATH, 
    _model, 
    _model_device
)
from cortex.embeddings.hardware import release_gpu
from cortex.embeddings.server_client import _send_to_server, ENGINE_HOST, ENGINE_PORT
