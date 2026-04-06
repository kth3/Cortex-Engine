import pytest
import os
import json
from unittest.mock import MagicMock, patch
import sys

# Ensure scripts directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.cortex.vector_engine import chunk_text

def test_chunk_text_empty_or_whitespace():
    """Test with empty string and whitespace-only string."""
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []

def test_chunk_text_short_text():
    """Test with text shorter than the chunk size."""
    assert chunk_text("Hello world", chunk_size=50) == ["Hello world"]

def test_chunk_text_paragraphs_combine():
    """Test combining multiple short paragraphs until they reach chunk size."""
    text = "Para 1\n\nPara 2\n\nPara 3"
    # All paragraphs fit in one chunk (14 + 6 + 2 = 22 <= 30)
    assert chunk_text(text, chunk_size=30) == ["Para 1\n\nPara 2\n\nPara 3"]
    # First two fit in 15, third goes to next chunk
    assert chunk_text(text, chunk_size=15) == ["Para 1\n\nPara 2", "Para 3"]

def test_chunk_text_long_paragraph():
    """Test splitting a single long paragraph that exceeds chunk size."""
    text = "0123456789" * 3  # 30 chars
    chunks = chunk_text(text, chunk_size=20, overlap=5)
    assert chunks == [
        "01234567890123456789",  # First 20 chars
        "567890123456789"        # Remaining 15 chars (with 5 chars overlap)
    ]

def test_chunk_text_mixed_paragraphs():
    """Test a mix of short paragraphs and long paragraphs that need splitting."""
    text = "Short para\n\n" + ("0123456789" * 3) + "\n\nAnother short"
    chunks = chunk_text(text, chunk_size=20, overlap=5)
    assert chunks == [
        "Short para",
        "01234567890123456789",
        "567890123456789",
        "Another short"
    ]

def test_vector_metadata_persistence(tmp_path):
    """Test that metadata is correctly saved to and loaded from JSON."""
    workspace = str(tmp_path)
    from scripts.cortex import vector_engine

    # Mock faiss module
    mock_faiss = MagicMock()

    with patch.dict(sys.modules, {'faiss': mock_faiss}):
        mock_index = MagicMock()
        mock_faiss.read_index.return_value = mock_index

        # Need to create the data dir and a mock index file
        data_dir = vector_engine._get_data_dir(workspace)
        idx_path = vector_engine._index_path(workspace)
        with open(idx_path, "wb") as f:
            f.write(b"mock faiss data")

        meta_data = [{"id": "test-id", "text": "test-content", "meta": {"key": "value"}}]

        # 1. Save
        vector_engine._save_faiss_index(workspace, mock_index, meta_data)

        # 2. Check if JSON file exists and has correct extension
        meta_path = vector_engine._meta_path(workspace)
        assert meta_path.endswith(".json")
        assert os.path.exists(meta_path)

        # Verify content is JSON
        with open(meta_path, "r", encoding="utf-8") as f:
            saved_meta = json.load(f)
            assert saved_meta == meta_data

        # 3. Load and verify
        loaded_index, loaded_meta = vector_engine._load_faiss_index(workspace)
        assert loaded_meta == meta_data
        assert loaded_index == mock_index
        mock_faiss.read_index.assert_called_once_with(idx_path)

def test_old_metadata_warning(tmp_path, capsys):
    """Test that the system warns about old .pkl metadata files."""
    workspace = str(tmp_path)
    from scripts.cortex import vector_engine

    # Mock faiss module
    mock_faiss = MagicMock()

    with patch.dict(sys.modules, {'faiss': mock_faiss}):
        data_dir = vector_engine._get_data_dir(workspace)
        # Create an old .pkl file
        old_meta_path = os.path.join(data_dir, "vectors_meta.pkl")
        with open(old_meta_path, "wb") as f:
            import pickle
            pickle.dump([{"old": "data"}], f)

        # Create a mock index file
        idx_path = vector_engine._index_path(workspace)
        with open(idx_path, "wb") as f:
            f.write(b"mock faiss data")

        # Load (should trigger warning because .json doesn't exist yet)
        index, meta = vector_engine._load_faiss_index(workspace)

        captured = capsys.readouterr()
        assert "WARNING: Old metadata format found" in captured.err
        assert "Please re-index" in captured.err

        # Should return None, [] because JSON file is missing
        assert index is None
        assert meta == []
