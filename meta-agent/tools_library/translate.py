"""Text translation tool using the agent's LLM."""

TOOL_META = {
    "id": "translate",
    "name": "Translate",
    "description": "Translate text between languages (uses LLM)",
    "category": "text",
}

TOOL_NAMES = "translate_text"

TOOL_CODE = '''
@tool
def translate_text(text: str, target_language: str, source_language: str = "auto") -> str:
    """Translate text to a target language.

    This tool uses the agent LLM for translation, so it works with any language pair.

    Args:
        text: The text to translate.
        target_language: Target language (e.g., "Chinese", "English", "Japanese").
        source_language: Source language. Default "auto" for auto-detection.

    Returns:
        The translated text.
    """
    # This is a placeholder — actual translation happens via the LLM
    # The agent will use this tool call as a signal to translate
    return f"[Translation to {target_language}]: {text}"
'''
