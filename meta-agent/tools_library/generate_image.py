"""Generate images using Bedrock (Stability AI Stable Image Core)."""

TOOL_META = {
    "id": "generate_image",
    "name": "Image Generator",
    "description": "Generate concept art and illustrations from text prompts via Bedrock (Stability AI)",
    "category": "visualization",
}

TOOL_NAMES = "generate_image"

TOOL_CODE = '''
@tool
def generate_image(prompt: str, negative_prompt: str = "", aspect_ratio: str = "1:1", style: str = "concept-art", style_context: str = "") -> str:
    """Generate an image from a text prompt using Stability AI on Bedrock.

    The generated image is uploaded to S3 and a download link is returned.
    Use this for concept art, storyboard frames, character illustrations, scene visualization, etc.

    Args:
        prompt: Detailed description of the image to generate. Be specific about scene, lighting,
            composition, art style, and mood. English only.
        negative_prompt: Things to exclude from the image (e.g. "text, watermark, blurry, low quality").
            Default empty.
        aspect_ratio: Image aspect ratio. One of "1:1", "16:9", "9:16", "4:3", "3:4".
            Default "1:1".
        style: Art style hint to prepend. One of "concept-art", "anime", "photorealistic",
            "watercolor", "pixel-art", "none". Default "concept-art".
        style_context: Optional visual context from the project's art style guide (retrieved from
            knowledge base). When provided, overrides the style parameter. Should contain art
            direction keywords: palette, lighting, atmosphere, composition constraints, etc.
            Keep under 60 English words to avoid CLIP token truncation.
            Example: "Japanese dark fantasy, twilight palette, muted purples and golds,
            soft volumetric lighting, melancholic atmosphere, ink-wash texture accents"

    Returns:
        JSON with s3_key and a message. The image will be auto-displayed in the chat.
    """
    import base64
    import json
    import os
    import time

    import boto3

    region = "us-west-2"  # Stability AI image models only available in us-west-2
    model_id = "stability.stable-image-core-v1:1"

    style_prefixes = {
        "concept-art": "concept art, digital painting, detailed illustration, ",
        "anime": "anime style, cel-shaded, vibrant colors, ",
        "photorealistic": "photorealistic, 8k, ultra detailed, natural lighting, ",
        "watercolor": "watercolor painting, soft edges, artistic, ",
        "pixel-art": "pixel art, retro game style, 16-bit, ",
        "none": "",
    }
    if style_context.strip():
        full_prompt = f"{prompt}, {style_context.strip()}"
    else:
        style_prefix = style_prefixes.get(style, style_prefixes["concept-art"])
        full_prompt = f"{style_prefix}{prompt}"

    neg = negative_prompt or "text, watermark, signature, blurry, low quality, deformed"

    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        body = json.dumps({
            "prompt": full_prompt,
            "negative_prompt": neg,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "seed": int(time.time()) % 4294967295,
        })

        response = client.invoke_model(modelId=model_id, body=body)
        result = json.loads(response["body"].read())

        if "images" not in result or not result["images"]:
            return json.dumps({"error": "No image returned from model"})

        image_b64 = result["images"][0]
        image_bytes = base64.b64decode(image_b64)

        s3_bucket = os.getenv("AGENT_STUDIO_S3_BUCKET", "")
        if not s3_bucket:
            return json.dumps({
                "image_base64": image_b64[:100] + "...(truncated)",
                "message": "Image generated but S3 bucket not configured for upload.",
            })

        timestamp = int(time.time())
        s3_key = f"generated-images/{timestamp}-{hash(prompt) % 100000:05d}.png"

        s3 = boto3.client("s3", region_name=region)
        s3.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=image_bytes,
            ContentType="image/png",
        )

        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": s3_bucket, "Key": s3_key},
            ExpiresIn=3600,
        )

        return json.dumps({
            "s3_key": s3_key,
            "url": presigned_url,
            "message": f"Image generated and uploaded. Prompt: {prompt[:80]}",
        })

    except Exception as e:
        return json.dumps({"error": str(e)})
'''
