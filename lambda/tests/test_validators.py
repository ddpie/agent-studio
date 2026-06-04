"""Tests for shared.validators module."""

from shared.validators import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    parse_pagination,
    validate_id,
    validate_path,
    validate_secret_key,
)

# ── validate_id ──


class TestValidateId:
    """Tests for validate_id()."""

    def test_valid_alphanumeric(self):
        assert validate_id("abc123") is None

    def test_valid_with_hyphens_and_underscores(self):
        assert validate_id("my-agent_v2") is None

    def test_valid_single_char(self):
        assert validate_id("a") is None

    def test_valid_max_length(self):
        assert validate_id("a" * 128) is None

    def test_invalid_empty_string(self):
        err = validate_id("")
        assert err is not None
        assert "Invalid id" in err

    def test_invalid_none(self):
        err = validate_id(None)
        assert err is not None

    def test_invalid_special_chars(self):
        for char in ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")", " ", ".", "/"]:
            err = validate_id(f"test{char}value")
            assert err is not None, f"Should reject char: {char}"

    def test_invalid_too_long(self):
        err = validate_id("a" * 129)
        assert err is not None
        assert "128 characters" in err

    def test_invalid_path_traversal(self):
        err = validate_id("../etc/passwd")
        assert err is not None

    def test_invalid_unicode(self):
        err = validate_id("agent-名前")
        assert err is not None

    def test_invalid_null_bytes(self):
        err = validate_id("agent\x00id")
        assert err is not None

    def test_invalid_newlines(self):
        err = validate_id("agent\nid")
        assert err is not None

    def test_custom_name_in_error(self):
        err = validate_id("bad value!", "workspaceId")
        assert "workspaceId" in err

    def test_valid_all_uppercase(self):
        assert validate_id("ABCXYZ") is None

    def test_valid_all_digits(self):
        assert validate_id("1234567890") is None

    def test_invalid_extremely_long(self):
        err = validate_id("x" * 10000)
        assert err is not None


# ── validate_path ──


class TestValidatePath:
    """Tests for validate_path()."""

    def test_valid_simple_path(self):
        assert validate_path("folder/file.txt") is None

    def test_valid_nested_path(self):
        assert validate_path("a/b/c/d/e.py") is None

    def test_valid_unicode_filename(self):
        # Chinese filenames are allowed
        assert validate_path("文件夹/报告.md") is None

    def test_valid_root_relative(self):
        assert validate_path("file.txt") is None

    def test_valid_with_spaces(self):
        assert validate_path("my folder/my file.txt") is None

    def test_valid_with_dots_in_filename(self):
        assert validate_path("archive.tar.gz") is None

    def test_valid_leading_dot(self):
        assert validate_path(".hidden/file") is None

    def test_valid_max_length(self):
        assert validate_path("a" * 512) is None

    def test_invalid_empty(self):
        err = validate_path("")
        assert err is not None

    def test_invalid_none(self):
        err = validate_path(None)
        assert err is not None

    def test_invalid_dot_dot_traversal(self):
        err = validate_path("folder/../etc/passwd")
        assert err is not None
        assert "Invalid path" in err

    def test_invalid_leading_dot_dot(self):
        err = validate_path("../secret")
        assert err is not None

    def test_invalid_double_slash(self):
        err = validate_path("folder//file.txt")
        assert err is not None

    def test_invalid_backslash(self):
        err = validate_path("folder\\file.txt")
        assert err is not None

    def test_invalid_null_byte(self):
        err = validate_path("file\x00.txt")
        assert err is not None

    def test_invalid_control_chars(self):
        for c in ["\x01", "\x0a", "\x0d", "\x1f", "\x7f"]:
            err = validate_path(f"folder/{c}file")
            assert err is not None, f"Should reject control char 0x{ord(c):02x}"

    def test_invalid_too_long(self):
        err = validate_path("a" * 513)
        assert err is not None
        assert "too long" in err.lower()

    def test_invalid_extremely_long(self):
        err = validate_path("b" * 10000)
        assert err is not None

    def test_dots_in_middle_of_segment_is_ok(self):
        # "file..name" is ok — only ".." as a standalone segment is blocked
        assert validate_path("folder/file..name") is None

    def test_dot_dot_not_as_segment_is_ok(self):
        # "...test" has ".." but not as a path segment
        assert validate_path("folder/...test") is None

    def test_single_dot_segment_is_ok(self):
        # Single "." is not blocked (only "..")
        assert validate_path("./file.txt") is None


# ── validate_secret_key ──


class TestValidateSecretKey:
    """Tests for validate_secret_key()."""

    def test_valid_simple(self):
        assert validate_secret_key("MY_API_KEY") is None

    def test_valid_with_hyphens(self):
        assert validate_secret_key("openai-api-key") is None

    def test_valid_alphanumeric(self):
        assert validate_secret_key("key123") is None

    def test_valid_max_length(self):
        assert validate_secret_key("k" * 128) is None

    def test_invalid_empty(self):
        err = validate_secret_key("")
        assert err is not None

    def test_invalid_none(self):
        err = validate_secret_key(None)
        assert err is not None

    def test_invalid_dots(self):
        err = validate_secret_key("my.key")
        assert err is not None

    def test_invalid_slashes(self):
        err = validate_secret_key("path/to/key")
        assert err is not None

    def test_invalid_spaces(self):
        err = validate_secret_key("my key")
        assert err is not None

    def test_invalid_too_long(self):
        err = validate_secret_key("k" * 129)
        assert err is not None
        assert "128 characters" in err

    def test_invalid_unicode(self):
        err = validate_secret_key("密钥")
        assert err is not None

    def test_invalid_null_bytes(self):
        err = validate_secret_key("key\x00val")
        assert err is not None

    def test_custom_name_in_error(self):
        err = validate_secret_key("bad!", "secretName")
        assert "secretName" in err


# ── parse_pagination ──


class TestParsePagination:
    """Tests for parse_pagination()."""

    def test_defaults_no_params(self):
        limit, cursor = parse_pagination({})
        assert limit == DEFAULT_LIMIT
        assert cursor is None

    def test_explicit_limit(self):
        limit, cursor = parse_pagination({"limit": "50"})
        assert limit == 50
        assert cursor is None

    def test_explicit_cursor(self):
        limit, cursor = parse_pagination({"cursor": "abc123"})
        assert limit == DEFAULT_LIMIT
        assert cursor == "abc123"

    def test_both_params(self):
        limit, cursor = parse_pagination({"limit": "10", "cursor": "xyz"})
        assert limit == 10
        assert cursor == "xyz"

    def test_limit_clamped_to_max(self):
        limit, _ = parse_pagination({"limit": "999"})
        assert limit == MAX_LIMIT

    def test_limit_clamped_to_min(self):
        limit, _ = parse_pagination({"limit": "0"})
        assert limit == 1

    def test_negative_limit_clamped(self):
        limit, _ = parse_pagination({"limit": "-5"})
        assert limit == 1

    def test_invalid_limit_falls_back_to_default(self):
        limit, _ = parse_pagination({"limit": "not_a_number"})
        assert limit == DEFAULT_LIMIT

    def test_none_limit_falls_back_to_default(self):
        limit, _ = parse_pagination({"limit": None})
        assert limit == DEFAULT_LIMIT

    def test_float_limit_truncated(self):
        # int("3.5") raises ValueError, should fallback
        limit, _ = parse_pagination({"limit": "3.5"})
        assert limit == DEFAULT_LIMIT

    def test_empty_string_limit(self):
        limit, _ = parse_pagination({"limit": ""})
        assert limit == DEFAULT_LIMIT

    def test_limit_at_max_boundary(self):
        limit, _ = parse_pagination({"limit": str(MAX_LIMIT)})
        assert limit == MAX_LIMIT

    def test_limit_just_over_max(self):
        limit, _ = parse_pagination({"limit": str(MAX_LIMIT + 1)})
        assert limit == MAX_LIMIT
