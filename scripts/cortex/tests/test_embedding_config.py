"""Tests for embedding model resolution via environment variables."""
from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.embeddings import provider


class TestResolveModelId:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CORTEX_EMBEDDING_MODEL", raising=False)
        assert provider._resolve_model_id() == provider.DEFAULT_MODEL_ID

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_MODEL", "google/embeddinggemma-300m")
        assert provider._resolve_model_id() == "google/embeddinggemma-300m"

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_MODEL", "   ")
        assert provider._resolve_model_id() == provider.DEFAULT_MODEL_ID


class TestResolveMaxSeqLength:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CORTEX_EMBEDDING_MAX_SEQ_LENGTH", raising=False)
        assert provider._resolve_max_seq_length() == provider.DEFAULT_MAX_SEQ_LENGTH

    def test_env_override_with_valid_integer(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_MAX_SEQ_LENGTH", "2048")
        assert provider._resolve_max_seq_length() == 2048

    def test_invalid_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_MAX_SEQ_LENGTH", "not-an-int")
        assert provider._resolve_max_seq_length() == provider.DEFAULT_MAX_SEQ_LENGTH

    def test_zero_and_negative_fall_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_MAX_SEQ_LENGTH", "0")
        assert provider._resolve_max_seq_length() == provider.DEFAULT_MAX_SEQ_LENGTH
        monkeypatch.setenv("CORTEX_EMBEDDING_MAX_SEQ_LENGTH", "-512")
        assert provider._resolve_max_seq_length() == provider.DEFAULT_MAX_SEQ_LENGTH

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_MAX_SEQ_LENGTH", "   ")
        assert provider._resolve_max_seq_length() == provider.DEFAULT_MAX_SEQ_LENGTH


class TestResolveTrustRemoteCode:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CORTEX_EMBEDDING_TRUST_REMOTE_CODE", raising=False)
        assert provider._resolve_trust_remote_code() is False

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_TRUST_REMOTE_CODE", "   ")
        assert provider._resolve_trust_remote_code() is False

    def test_truthy_values_enable_trust_remote_code(self, monkeypatch):
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("CORTEX_EMBEDDING_TRUST_REMOTE_CODE", value)
            assert provider._resolve_trust_remote_code() is True

    def test_unknown_value_disables_trust_remote_code(self, monkeypatch):
        monkeypatch.setenv("CORTEX_EMBEDDING_TRUST_REMOTE_CODE", "maybe")
        assert provider._resolve_trust_remote_code() is False


class TestResolveHfToken:
    def test_unset_token_uses_huggingface_fallback(self, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        assert provider._resolve_hf_token() is None

    def test_blank_token_uses_huggingface_fallback(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "   ")
        assert provider._resolve_hf_token() is None

    def test_token_is_stripped(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "  hf_test_token  ")
        assert provider._resolve_hf_token() == "hf_test_token"


class TestModuleConstants:
    def test_module_level_constants_are_strings_and_ints(self):
        assert isinstance(provider.MODEL_ID, str)
        assert isinstance(provider.MAX_SEQ_LENGTH, int)
        assert provider.MAX_SEQ_LENGTH > 0

    def test_default_constants_preserve_qwen_choice(self):
        # 기본 모델 변경은 의도적 결정 — 회귀 테스트로 고정
        assert provider.DEFAULT_MODEL_ID == "Qwen/Qwen3-Embedding-0.6B"
        assert provider.DEFAULT_MAX_SEQ_LENGTH == 4096
        assert provider.DEFAULT_TRUST_REMOTE_CODE is False
