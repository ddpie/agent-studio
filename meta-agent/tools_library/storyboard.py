"""Split text into storyboard frames and generate images for each."""

TOOL_META = {
    "id": "storyboard",
    "name": "Storyboard Generator",
    "description": "Split text/script into storyboard frames, generate concept art for each frame, and optionally combine into GIF",
    "category": "visualization",
}

TOOL_NAMES = "create_storyboard"

TOOL_CODE = '''
@tool
def create_storyboard(script: str, num_frames: int = 4, style: str = "concept-art", aspect_ratio: str = "16:9", create_gif: bool = False, style_context: str = "", negative_prompt: str = "") -> str:
    """Split a script/description into storyboard frames and generate an image for each.

    Takes a narrative description or script and:
    1. Splits it into sequential visual frames (scenes/shots)
    2. Generates a concept art image for each frame
    3. Optionally combines frames into an animated GIF

    Use this for: activity storyboards, cutscene pre-visualization, marketing campaign
    visual sequences, NPC dialogue scene illustration.

    Args:
        script: The narrative text to split into frames. Can be a story description,
            activity announcement, cutscene script, or marketing copy.
        num_frames: Number of storyboard frames to generate (2-8). Default 4.
        style: Art style for all frames. One of "concept-art", "anime", "photorealistic",
            "watercolor", "pixel-art", "none". Default "concept-art".
        aspect_ratio: Aspect ratio for frames. "16:9" (widescreen) or "1:1". Default "16:9".
        create_gif: Whether to combine frames into an animated GIF. Default False.
        style_context: Optional visual context from the project's art style guide (retrieved
            from knowledge base). When provided, overrides the style parameter. Should contain
            art direction keywords: palette, lighting, atmosphere, composition constraints,
            etc. Keep under 60 English words to avoid CLIP token truncation.
        negative_prompt: Things to exclude from all frames (e.g. "neon colors, modern
            buildings"). When empty, a sensible default is used. Project-level exclusions
            from the art style guide should be passed here.

    Returns:
        JSON with frame descriptions, S3 keys for each image, and optionally a GIF S3 key.
    """
    import base64
    import io
    import json
    import os
    import time

    import boto3

    region = "us-west-2"  # Stability AI image models only available in us-west-2
    s3_bucket = os.getenv("AGENT_STUDIO_S3_BUCKET", "")
    model_id = "stability.sd3-5-large-v1:0"
    num_frames = max(2, min(8, num_frames))

    style_prefixes = {
        "concept-art": "concept art, digital painting, cinematic composition, ",
        "anime": "anime style, cel-shaded, vibrant colors, ",
        "photorealistic": "photorealistic, 8k, cinematic lighting, ",
        "watercolor": "watercolor painting, soft artistic style, ",
        "pixel-art": "pixel art, retro game style, ",
        "none": "",
    }
    neg = negative_prompt or "text, watermark, signature, blurry, low quality, deformed, ugly"

    # Step 1: Use the LLM-generated frame descriptions
    # The agent calling this tool should have already broken the script into frames,
    # but as a fallback we split by sentences/paragraphs
    lines = [l.strip() for l in script.replace("\\\\n", "\\n").split("\\n") if l.strip()]
    if len(lines) >= num_frames:
        frames_text = lines[:num_frames]
    else:
        # Simple split by even chunks
        words = script.split()
        chunk_size = max(1, len(words) // num_frames)
        frames_text = []
        for i in range(num_frames):
            start = i * chunk_size
            end = start + chunk_size if i < num_frames - 1 else len(words)
            frames_text.append(" ".join(words[start:end]))

    bedrock = boto3.client("bedrock-runtime", region_name=region)
    s3 = boto3.client("s3", region_name=region) if s3_bucket else None

    results = []
    image_bytes_list = []
    timestamp = int(time.time())

    _style_ctx = style_context.strip()
    style_prefix = "" if _style_ctx else style_prefixes.get(style, style_prefixes["concept-art"])

    for idx, frame_desc in enumerate(frames_text):
        if _style_ctx:
            full_prompt = f"frame {idx+1} of {num_frames}, storyboard shot, {frame_desc}, {_style_ctx}"
        else:
            full_prompt = f"{style_prefix}frame {idx+1} of {num_frames}, storyboard shot, {frame_desc}"

        try:
            body = json.dumps({
                "prompt": full_prompt,
                "negative_prompt": neg,
                "aspect_ratio": aspect_ratio,
                "output_format": "png",
                "seed": (timestamp + idx * 1000) % 4294967295,
            })

            response = bedrock.invoke_model(modelId=model_id, body=body)
            result = json.loads(response["body"].read())

            if "images" not in result or not result["images"]:
                results.append({"frame": idx + 1, "description": frame_desc, "error": "No image returned"})
                continue

            img_b64 = result["images"][0]
            img_bytes = base64.b64decode(img_b64)
            image_bytes_list.append(img_bytes)

            frame_result = {"frame": idx + 1, "description": frame_desc}

            if s3:
                s3_key = f"storyboards/{timestamp}/frame-{idx+1:02d}.png"
                s3.put_object(Bucket=s3_bucket, Key=s3_key, Body=img_bytes, ContentType="image/png")
                url = s3.generate_presigned_url("get_object", Params={"Bucket": s3_bucket, "Key": s3_key}, ExpiresIn=3600)
                frame_result["s3_key"] = s3_key
                frame_result["url"] = url

            results.append(frame_result)

        except Exception as e:
            results.append({"frame": idx + 1, "description": frame_desc, "error": str(e)})

    # Step 3: Optionally create GIF
    gif_result = None
    if create_gif and len(image_bytes_list) >= 2:
        try:
            from PIL import Image

            pil_frames = []
            for img_bytes in image_bytes_list:
                img = Image.open(io.BytesIO(img_bytes))
                pil_frames.append(img.convert("RGBA"))

            gif_buffer = io.BytesIO()
            pil_frames[0].save(
                gif_buffer,
                format="GIF",
                save_all=True,
                append_images=pil_frames[1:],
                duration=1500,
                loop=0,
            )
            gif_bytes = gif_buffer.getvalue()

            if s3:
                gif_key = f"storyboards/{timestamp}/storyboard.gif"
                s3.put_object(Bucket=s3_bucket, Key=gif_key, Body=gif_bytes, ContentType="image/gif")
                gif_url = s3.generate_presigned_url("get_object", Params={"Bucket": s3_bucket, "Key": gif_key}, ExpiresIn=3600)
                gif_result = {"s3_key": gif_key, "url": gif_url}

        except ImportError:
            gif_result = {"error": "PIL not available for GIF creation"}
        except Exception as e:
            gif_result = {"error": f"GIF creation failed: {str(e)}"}

    output = {
        "total_frames": len(results),
        "style": style,
        "frames": results,
    }
    if gif_result:
        output["gif"] = gif_result

    return json.dumps(output, ensure_ascii=False)
'''
