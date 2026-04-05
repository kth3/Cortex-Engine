import pytest
from scripts.jules_mcp import safe_truncate

def test_safe_truncate_shorter_than_max():
    text = "Hello, world!"
    max_len = 20
    assert safe_truncate(text, max_len) == text

def test_safe_truncate_equal_to_max():
    text = "Hello, world!"
    max_len = 13
    assert safe_truncate(text, max_len) == text

def test_safe_truncate_longer_with_newline():
    text = "Line one\nLine two\nLine three"
    max_len = 15
    # Truncated text at max_len is "Line one\nLine t"
    # last_newline is at index 8
    # Result should be "Line one" + "\n\n...[Diff truncated due to length constraints]..."
    expected = "Line one\n\n...[Diff truncated due to length constraints]..."
    assert safe_truncate(text, max_len) == expected

def test_safe_truncate_longer_without_newline():
    text = "This is a long line without any newlines in it."
    max_len = 10
    expected = "This is a ...[Diff truncated]..."
    assert safe_truncate(text, max_len) == expected

def test_safe_truncate_longer_newline_at_zero():
    # If last_newline is 0, it should fallback to "...[Diff truncated]..."
    text = "\nStarting with newline"
    max_len = 5
    # Truncated is "\nStar"
    # last_newline is 0
    expected = "\nStar...[Diff truncated]..."
    assert safe_truncate(text, max_len) == expected

def test_safe_truncate_empty_string():
    assert safe_truncate("", 10) == ""

def test_safe_truncate_max_len_zero():
    # If max_len is 0, text[:0] is "", len("") is 0, so it returns ""
    assert safe_truncate("Hello", 0) == "...[Diff truncated]..."

def test_safe_truncate_max_len_negative():
    # Just in case, checking negative max_len
    # text[:negative] is not empty usually if it's large, but let's see
    # In python "abc"[:-1] is "ab".
    # safe_truncate("abc", -1) -> len("abc") <= -1 is False. truncated = "abc"[:-1] -> "ab". last_newline = -1. returns "ab...[Diff truncated]..."
    assert safe_truncate("Hello", -1) == "Hell...[Diff truncated]..."
