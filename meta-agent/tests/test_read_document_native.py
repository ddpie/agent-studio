"""Tests for the read_document builtin tool.

The tool lives as a literal string inside BUILTIN_TOOLS_CODE in
templates.agent_template_v2. We exec that string into a fresh namespace and
then drive the function with a mocked S3 client plus small synthetic
PDF/xlsx/csv fixtures generated in-test.
"""

import io
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Stub modules the template expects at import time ──────────────────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "MODEL_ID": "mock-model",
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/test-role",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


def _exec_builtin(s3_mock):
    """Execute BUILTIN_TOOLS_CODE with boto3.client patched to return s3_mock."""
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE

    ns: dict = {"__name__": "builtin_tools_test"}
    with patch("boto3.client", return_value=s3_mock):
        exec(BUILTIN_TOOLS_CODE, ns)
    return ns


def _make_s3_get_mock(body_bytes: bytes):
    """Build a MagicMock S3 client that responds to head_object/get_object."""
    s3 = MagicMock()
    s3.head_object.return_value = {"ContentLength": len(body_bytes)}
    s3.get_object.return_value = {"Body": io.BytesIO(body_bytes)}
    return s3


# ── Fixtures: synthetic documents generated at import time ────────────────


def _make_pdf_bytes(pages: list[str]) -> bytes:
    """Construct a minimal PDF with a short text string on each page.

    We hand-roll the PDF so the test suite doesn't need reportlab/fpdf.
    pypdf can parse and extract text from the output.
    """
    pytest.importorskip("pypdf")
    objs: list[str] = []

    def add(body: str) -> int:
        objs.append(body)
        return len(objs)

    content_ids: list[int] = []
    for text in pages:
        # Escape PDF string literal parens/backslashes
        esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({esc}) Tj ET"
        content_ids.append(add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"))

    font_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Pages object will live just after the N page objects we're about to create.
    pages_id = len(objs) + len(content_ids) + 1

    page_ids: list[int] = []
    for cid in content_ids:
        page_ids.append(
            add(
                f"<< /Type /Page /Parent {pages_id} 0 R "
                f"/MediaBox [0 0 612 792] "
                f"/Contents {cid} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            )
        )

    actual_pages_id = add(
        "<< /Type /Pages /Kids [" + " ".join(f"{p} 0 R" for p in page_ids) + f"] /Count {len(page_ids)} >>"
    )
    assert actual_pages_id == pages_id
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()

    xref_start = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_start}\n%%EOF"
    ).encode()
    return bytes(out)


def _make_xlsx_bytes(sheets: dict[str, list[list]]) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    # remove default sheet if we're creating our own
    default = wb.active
    wb.remove(default)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Tests ─────────────────────────────────────────────────────────────────


def test_read_document_rejects_cross_workspace_key():
    s3 = _make_s3_get_mock(b"")
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    result = ns["read_document"]("workspaces/ws-beta/storage/uploads/u/report.pdf")
    assert "workspaces/ws-alpha/storage/" in result
    assert "Cross-workspace" in result or "not allowed" in result.lower()
    # Must not have actually fetched the object
    assert not s3.get_object.called


def test_read_document_accepts_chat_attachment_key():
    """Chat attachments live at uploads/attachments/<session>/<file> and must be readable."""
    csv_bytes = b"name,qty\napple,3\npear,7\n"
    s3 = _make_s3_get_mock(csv_bytes)
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("uploads/attachments/sess-abc-123/data.csv")
    assert "apple" in out and "pear" in out
    assert s3.get_object.called


def test_read_document_still_accepts_workspace_storage_key():
    """Existing workspace-storage path must keep working alongside the new attachment prefix."""
    csv_bytes = b"name,qty\napple,3\npear,7\n"
    s3 = _make_s3_get_mock(csv_bytes)
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("workspaces/ws-alpha/storage/uploads/u/data.csv")
    assert "apple" in out and "pear" in out
    assert s3.get_object.called


def test_read_document_rejects_unknown_prefix():
    """Anything that is neither workspaces/<ws>/storage/ nor uploads/attachments/<sess>/ is rejected."""
    s3 = _make_s3_get_mock(b"")
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    result = ns["read_document"]("random/prefix/file.pdf")
    assert "Error" in result
    assert not s3.get_object.called


def test_read_document_rejects_attachments_without_session():
    """uploads/attachments/ by itself (no session segment) must not leak."""
    s3 = _make_s3_get_mock(b"")
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    # Missing session segment
    result = ns["read_document"]("uploads/attachments/foo.pdf")
    assert "Error" in result
    assert not s3.get_object.called
    # Session segment but no trailing filename
    result2 = ns["read_document"]("uploads/attachments/sess-only/")
    assert "Error" in result2
    assert not s3.get_object.called


def test_read_document_rejects_missing_workspace_context():
    s3 = _make_s3_get_mock(b"")
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = ""
    result = ns["read_document"]("workspaces/ws-x/storage/a.pdf")
    assert "workspace context" in result.lower()
    assert not s3.get_object.called


def test_read_document_rejects_url_as_key():
    s3 = _make_s3_get_mock(b"")
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    result = ns["read_document"]("https://example.com/foo.pdf")
    assert "Error" in result
    assert not s3.get_object.called


def test_read_document_rejects_unsupported_extension():
    # Note: .txt is supported (added to _PLAINTEXT_EXTS); use a binary
    # extension that genuinely is not handled.
    s3 = _make_s3_get_mock(b"")
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    result = ns["read_document"]("workspaces/ws-alpha/storage/a.exe")
    assert "unsupported file type" in result.lower()
    assert not s3.get_object.called


def test_read_document_rejects_oversized_file():
    s3 = MagicMock()
    s3.head_object.return_value = {"ContentLength": 11 * 1024 * 1024}
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    result = ns["read_document"]("workspaces/ws-alpha/storage/big.pdf")
    assert "10 MB" in result or "exceeds" in result.lower()
    assert not s3.get_object.called


def test_read_document_xlsx_extracts_sheet_headers_and_cells():
    xlsx_bytes = _make_xlsx_bytes(
        {
            "Alpha": [["name", "qty"], ["apple", 3], ["pear", 7]],
            "Beta": [["id"], [1], [2]],
        }
    )
    s3 = _make_s3_get_mock(xlsx_bytes)
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("workspaces/ws-alpha/storage/uploads/u/book.xlsx")
    assert "=== Sheet: Alpha ===" in out
    assert "=== Sheet: Beta ===" in out
    assert "apple" in out and "pear" in out
    assert "qty" in out


def test_read_document_csv_renders_as_table():
    csv_bytes = b"name,qty\napple,3\npear,7\n"
    s3 = _make_s3_get_mock(csv_bytes)
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("workspaces/ws-alpha/storage/uploads/u/data.csv")
    assert "apple" in out and "pear" in out
    assert "qty" in out


def test_read_document_tsv_uses_tab_separator():
    tsv_bytes = b"name\tqty\napple\t3\npear\t7\n"
    s3 = _make_s3_get_mock(tsv_bytes)
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("workspaces/ws-alpha/storage/uploads/u/data.tsv")
    assert "apple" in out and "pear" in out


def test_read_document_pdf_extracts_text_per_page():
    pdf_bytes = _make_pdf_bytes(["Hello from page one.", "Second page content."])
    s3 = _make_s3_get_mock(pdf_bytes)
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("workspaces/ws-alpha/storage/uploads/u/doc.pdf")
    assert "=== Page 1 ===" in out
    assert "=== Page 2 ===" in out
    assert "Hello from page one" in out
    assert "Second page content" in out


def test_read_document_truncates_large_output(monkeypatch):
    # Build a CSV that will definitely exceed 50k chars when rendered.
    rows = ["name,value"]
    for i in range(10_000):
        rows.append(f"item_{i},{i * 37}")
    csv_bytes = ("\n".join(rows) + "\n").encode()
    s3 = _make_s3_get_mock(csv_bytes)
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("workspaces/ws-alpha/storage/uploads/u/big.csv")
    assert "TRUNCATED" in out
    assert len(out) <= 50_000 + 300  # plus continuation hint


def test_read_document_paginates_with_offset(monkeypatch):
    """When the document exceeds one call, offset lets the agent
    continue reading the rest instead of giving up."""
    # A plain-text JSON file large enough to need multiple reads.
    # ~60k chars total so the second call returns the final 10k clean.
    payload = "X" * 60_000
    # Build a mock that returns a fresh BytesIO per get_object call so
    # the second read_document call can re-consume the body.
    s3 = MagicMock()
    s3.head_object.return_value = {"ContentLength": len(payload)}
    s3.get_object.side_effect = lambda **_: {"Body": io.BytesIO(payload.encode())}
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    key = "workspaces/ws-alpha/storage/uploads/u/big.json"

    first = ns["read_document"](key)
    assert "TRUNCATED" in first
    # Hint must carry the continuation offset so the LLM can act.
    assert "offset=50000" in first
    assert "0-50000 of 60000" in first

    second = ns["read_document"](key, offset=50_000)
    assert "TRUNCATED" not in second
    assert second == "X" * 10_000


def test_read_document_offset_past_end_is_empty_but_not_an_error():
    """Harmless overshoot should return a clear empty marker, not an
    exception — the agent may legitimately try one offset too far."""
    s3 = _make_s3_get_mock(b"hello world")
    ns = _exec_builtin(s3)
    ns["_workspace_id"] = "ws-alpha"
    out = ns["read_document"]("workspaces/ws-alpha/storage/uploads/u/tiny.txt", offset=10_000)
    assert "[EMPTY" in out
    assert "past end" in out
