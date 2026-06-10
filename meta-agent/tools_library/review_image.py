"""Review a generated image against character/scene descriptions using Claude Vision."""

TOOL_META = {
    "id": "review_image",
    "name": "Image Reviewer",
    "description": "Review a generated image against character/scene descriptions using Claude Vision (setting consistency check)",
    "category": "visualization",
}

TOOL_NAMES = "review_image"

TOOL_CODE = '''
@tool
def review_image(s3_key: str, review_instruction: str, character_description: str = "") -> str:
    """Review a generated image against character/scene setting descriptions.

    Reads the image from S3, sends it to Claude Vision along with the review
    instruction and character description, and returns a structured verdict.

    Use this to verify that prototype images match the authoritative character
    settings before circulating them internally. This is NOT a quality or
    aesthetics check — it validates setting consistency only.

    Args:
        s3_key: S3 key of the image to review (e.g. "outputs/generated-images/123.png").
        review_instruction: What to check — e.g. "Does this image match the character
            setting for 月見? Check hair color, eye color, clothing, weapons, forbidden
            elements." Keep concise.
        character_description: The authoritative character visual description retrieved
            from knowledge base. Include hair, eyes, clothing, accessories, forbidden
            items, etc. The reviewer compares the image against THIS description.

    Returns:
        JSON with verdict ("pass"/"fail"), issues list, and summary.
    """
    import base64
    import json
    import os

    import boto3

    region = os.getenv("AWS_REGION", "us-east-1")
    s3_bucket = os.getenv("AGENT_STUDIO_S3_BUCKET", "")
    vision_model = "us.anthropic.claude-sonnet-4-6"

    if not s3_bucket:
        return json.dumps({"error": "S3 bucket not configured"})

    if not s3_key or not review_instruction:
        return json.dumps({"error": "s3_key and review_instruction are required"})

    try:
        s3 = boto3.client("s3", region_name=region)
        obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        image_bytes = obj["Body"].read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        content_type = obj.get("ContentType", "image/png")
        media_type = content_type if content_type.startswith("image/") else "image/png"
    except Exception as e:
        return json.dumps({"error": f"Failed to read image from S3: {str(e)}"})

    review_prompt = f"""You are a visual setting consistency reviewer for a game project.

Your job: compare the generated image against the authoritative character/scene description below, and determine if the image accurately represents the described setting.

## Review Instruction
{review_instruction}

## Authoritative Character/Scene Description
{character_description if character_description else "(No specific description provided — use your best judgment based on the review instruction.)"}

## Your Task
1. Describe what you see in the image (briefly).
2. Compare against the authoritative description point by point.
3. Flag any inconsistencies as issues.
4. Give a verdict: "pass" if no significant setting inconsistencies, "fail" if the image would mislead someone who uses it as a reference.

Respond in this exact JSON format:
{{"verdict": "pass" or "fail", "issues": ["issue 1", "issue 2", ...], "summary": "one sentence overall assessment"}}

Important:
- Do NOT judge artistic quality, hand anatomy, or AI artifacts — this is a prototype image.
- DO flag: wrong hair/eye color, wrong clothing, wrong weapons/accessories, forbidden elements, version-leaked visual content.
- Be strict on character identity markers (hair color, eye color, signature accessories).
- Be lenient on style/quality/composition."""

    try:
        bedrock = boto3.client("bedrock-runtime", region_name=region)
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": review_prompt,
                        },
                    ],
                }
            ],
        })

        response = bedrock.invoke_model(modelId=vision_model, body=body)
        result = json.loads(response["body"].read())

        assistant_text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                assistant_text += block["text"]

        # Try to extract JSON from the response
        json_start = assistant_text.find("{")
        json_end = assistant_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            verdict = json.loads(assistant_text[json_start:json_end])
            return json.dumps(verdict, ensure_ascii=False)

        return json.dumps({
            "verdict": "unknown",
            "issues": [],
            "summary": assistant_text[:500],
        })

    except Exception as e:
        return json.dumps({"error": f"Vision review failed: {str(e)}"})
'''
