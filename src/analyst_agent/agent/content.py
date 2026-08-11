"""Message content is a list of typed blocks, not a string — the Responses API returns
reasoning, tool calls and text separately. Reading `.content` directly elsewhere is a bug.
"""

# Chat Completions and the Responses API label answer text differently.
TEXT_TYPES = ("text", "output_text")
REASONING_TYPES = ("reasoning", "thinking")


def text_of(content: str | list) -> str:
    """The visible answer. Reasoning and tool-call blocks are dropped."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") in TEXT_TYPES
    )


def reasoning_of(content: str | list) -> str:
    """The reasoning summary, or "" when the turn produced none. Empty is normal."""
    if isinstance(content, str):
        return ""
    out: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in REASONING_TYPES:
            continue
        # The Responses API nests summary_text items under `summary`; Anthropic-style
        # thinking blocks put the text directly on the block.
        for item in block.get("summary") or []:
            if isinstance(item, dict) and item.get("text"):
                out.append(item["text"])
        if block.get("thinking"):
            out.append(block["thinking"])
    return "\n\n".join(out)
