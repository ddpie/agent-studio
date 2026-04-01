"""S3 file read tool."""

TOOL_META = {
    "id": "s3_read",
    "name": "S3 Read",
    "description": "Read files from S3 buckets",
    "category": "aws",
}

TOOL_NAMES = "s3_read"

TOOL_CODE = '''
@tool
def s3_read(bucket: str, key: str) -> str:
    """Read a file from an S3 bucket.

    Args:
        bucket: The S3 bucket name.
        key: The S3 object key (file path).

    Returns:
        The file content as text, or error message.
    """
    import boto3
    import json

    try:
        s3 = boto3.client("s3")
        resp = s3.get_object(Bucket=bucket, Key=key)
        content = resp["Body"].read()

        # Try text decoding
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return f"Binary file ({len(content)} bytes). Cannot display as text."
    except Exception as e:
        return json.dumps({"error": str(e)})
'''
