from langchain_core.messages import AIMessageChunk

from praxis.core.llm import reasoning_of, text_of


def chunk(content, **additional_kwargs):
    return AIMessageChunk(content=content, additional_kwargs=additional_kwargs)


# Shape captured from a live Azure gpt-5.1 Responses API call.
RESPONSES_BLOCKS = [
    {
        "id": "rs_05deee93",
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "Checking whether net revenue exists."}],
        "content": [],
    },
    {"type": "text", "text": "Revenue was $412,900 "},
    {"type": "text", "text": "in January."},
]


def test_text_of_joins_text_blocks_and_drops_reasoning():
    assert text_of(chunk(RESPONSES_BLOCKS)) == "Revenue was $412,900 in January."


def test_reasoning_of_reads_nested_summary_text():
    assert reasoning_of(chunk(RESPONSES_BLOCKS)) == "Checking whether net revenue exists."


def test_plain_string_content_still_works():
    """Chat Completions and any non-Azure provider return a bare string."""
    assert text_of(chunk("just a string")) == "just a string"
    assert reasoning_of(chunk("just a string")) == ""


def test_no_reasoning_is_empty_not_an_error():
    """The post-tool synthesis call returns text with no reasoning block."""
    assert reasoning_of(chunk([{"type": "text", "text": "hi"}])) == ""


def test_tool_call_blocks_are_not_answer_text():
    blocks = [
        {"type": "function_call", "name": "run_query", "arguments": "{}"},
        {"type": "text", "text": "Done."},
    ]
    assert text_of(chunk(blocks)) == "Done."


def test_anthropic_style_thinking_block():
    assert reasoning_of(chunk([{"type": "thinking", "thinking": "step one"}])) == "step one"


def test_output_text_alias():
    assert text_of(chunk([{"type": "output_text", "text": "aliased"}])) == "aliased"


def test_gemini_thought_block():
    """Gemini's `include_thoughts` arrives in Anthropic's block shape, signature and all."""
    blocks = [{"type": "thinking", "thinking": "Join orders to customers.", "signature": "c2ln"}]
    assert reasoning_of(chunk(blocks)) == "Join orders to customers."


def test_reasoning_beside_the_content_is_read():
    """Ollama and LiteLLM keep reasoning in `additional_kwargs`, with the answer a bare string."""
    message = chunk("Revenue rose.", reasoning_content="Compare the two months.")
    assert reasoning_of(message) == "Compare the two months."
    assert text_of(message) == "Revenue rose."
