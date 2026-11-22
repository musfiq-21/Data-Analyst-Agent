"""A scripted stand-in for the Ollama client, so the loop can be tested offline."""
from sqlagent.llm import ChatResult


def reply(content="", tool_calls=None, prompt_tokens=10, completion_tokens=5):
    return ChatResult(content=content, tool_calls=tool_calls or [],
                      prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def call(name, **arguments):
    return {"name": name, "arguments": arguments}


class FakeLLM:
    """Returns scripted replies in order. Records every message list it was sent."""

    model = "fake-model"

    def __init__(self, replies, raise_on=None):
        self.replies = list(replies)
        self.seen = []
        # index (or set of indexes) of the calls that raise
        self.raise_on = {raise_on} if isinstance(raise_on, int) else set(raise_on or ())

    def chat(self, messages, tools=None):
        self.seen.append({"messages": [dict(m) for m in messages], "tools": tools})
        if len(self.seen) - 1 in self.raise_on:
            raise ConnectionError("ollama is not running")
        if not self.replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self.replies.pop(0)
