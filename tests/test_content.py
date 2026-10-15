from praxis.core.llm import reasoning_of, text_of

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
    assert text_of(RESPONSES_BLOCKS) == "Revenue was $412,900 in January."


def test_reasoning_of_reads_nested_summary_text():
    assert reasoning_of(RESPONSES_BLOCKS) == "Checking whether net revenue exists."


def test_plain_string_content_still_works():
    """Chat Completions and any non-Azure provider return a bare string."""
    assert text_of("just a string") == "just a string"
    assert reasoning_of("just a string") == ""


def test_no_reasoning_is_empty_not_an_error():
    """The post-tool synthesis call returns text with no reasoning block."""
    assert reasoning_of([{"type": "text", "text": "hi"}]) == ""


def test_tool_call_blocks_are_not_answer_text():
    blocks = [
        {"type": "function_call", "name": "run_query", "arguments": "{}"},
        {"type": "text", "text": "Done."},
    ]
    assert text_of(blocks) == "Done."


def test_anthropic_style_thinking_block():
    assert reasoning_of([{"type": "thinking", "thinking": "step one"}]) == "step one"


def test_output_text_alias():
    assert text_of([{"type": "output_text", "text": "aliased"}]) == "aliased"
