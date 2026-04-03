"""list_skills — List all available skills from S3."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


@tool
def list_skills() -> str:
    """List all available skills stored in S3.

    Returns:
        JSON array of skills with id, name, and description.
    """
    s3 = boto3.client("s3", region_name=REGION)

    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key="skills/index.json")
        skills = json.loads(obj["Body"].read().decode("utf-8"))
        return json.dumps(skills, indent=2, ensure_ascii=False)
    except s3.exceptions.NoSuchKey:
        return json.dumps([])
    except Exception as e:
        return json.dumps({"error": str(e)})
