Recorded Messages API shapes for tests/test_ask.py (no live calls). `forced_tool_choice_400.json` is the
error body documented at platform.claude.com/docs/en/api/errors ("Forced tool use not supported"); the other two
are the documented `stop_reason` values `refusal` and `max_tokens` (platform.claude.com/docs/en/api/handling-stop-reasons)
in a Messages response. They were written from the documentation, not captured from a live call.
