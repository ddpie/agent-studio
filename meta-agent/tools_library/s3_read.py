"""S3 file read tool."""

TOOL_META = {
    "id": "s3_read",
    "name": "S3 Read",
    "description": "Read files from S3 buckets with auto-parsing for CSV/JSON/TSV",
    "category": "aws",
}

TOOL_NAMES = "s3_read"

TOOL_CODE = '''
@tool
def s3_read(bucket: str, key: str, max_bytes: int = 500000) -> str:
    """Read a file from an S3 bucket with automatic format detection.

    Supports CSV, TSV, JSON, and plain text files. Large files are truncated.

    Args:
        bucket: The S3 bucket name.
        key: The S3 object key (file path).
        max_bytes: Maximum bytes to read. Default 500KB. Set higher for large files.

    Returns:
        File content as text. For CSV/TSV, includes a summary header (row count, columns).

    Example:
        s3_read("my-bucket", "data/report.csv")
        s3_read("my-bucket", "attachments/session123/data.json")
    """
    import boto3
    import json

    if not bucket or not key:
        return "Error: bucket and key are required."

    try:
        s3 = boto3.client("s3")
        # Get file size first
        head = s3.head_object(Bucket=bucket, Key=key)
        size = head["ContentLength"]
        content_type = head.get("ContentType", "")

        if size > max_bytes:
            resp = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}")
            raw = resp["Body"].read()
            truncated = True
        else:
            resp = s3.get_object(Bucket=bucket, Key=key)
            raw = resp["Body"].read()
            truncated = False

        # Try text decoding
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError:
                return f"Binary file ({size} bytes). Cannot display as text."

        # Auto-detect and summarize structured data
        ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
        summary = ""

        if ext in ("csv", "tsv") or "csv" in content_type:
            sep = "\\t" if ext == "tsv" else ","
            lines = text.split("\\n")
            non_empty = [l for l in lines if l.strip()]
            if non_empty:
                header = non_empty[0]
                cols = [c.strip().strip('"') for c in header.split(sep)]
                summary = f"[File: {key} | Size: {size} bytes | Rows: ~{len(non_empty) - 1} | Columns: {len(cols)} — {', '.join(cols[:15])}{'...' if len(cols) > 15 else ''}]\\n\\n"

        elif ext == "json":
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    summary = f"[File: {key} | Size: {size} bytes | JSON array with {len(parsed)} items]\\n\\n"
                elif isinstance(parsed, dict):
                    summary = f"[File: {key} | Size: {size} bytes | JSON object with keys: {', '.join(list(parsed.keys())[:10])}]\\n\\n"
            except json.JSONDecodeError:
                pass

        if not summary:
            summary = f"[File: {key} | Size: {size} bytes]\\n\\n"

        result = summary + text
        if truncated:
            result += f"\\n\\n... (truncated at {max_bytes} bytes, total {size} bytes)"

        return result

    except Exception as e:
        return f"Error reading s3://{bucket}/{key}: {e}"
'''
