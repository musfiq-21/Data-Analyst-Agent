"""Minimal Ollama /api/chat client (standard library only) with a token ledger.

Token counts are Ollama's own: prompt_eval_count (input) and eval_count (output).
Note: Ollama reuses its KV cache for a shared prompt prefix, so in a multi-step
conversation prompt_eval_count can be smaller than the full prompt length. We
record exactly what Ollama reports; the budget cap uses the same numbers.
"""
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
if not DEFAULT_HOST.startswith("http"):
    DEFAULT_HOST = "http://" + DEFAULT_HOST


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list = field(default_factory=list)  # [{"name": str, "arguments": dict}]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


def parse_response(data):
    """Turn Ollama's JSON response into a ChatResult."""
    msg = data.get("message") or {}
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        calls.append({"name": fn.get("name", ""), "arguments": args})
    return ChatResult(content=msg.get("content") or "", tool_calls=calls,
                      prompt_tokens=int(data.get("prompt_eval_count") or 0),
                      completion_tokens=int(data.get("eval_count") or 0))


def _http_post(url, payload, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"ollama HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ConnectionError(f"cannot reach ollama at {url}: {e.reason}. "
                              "Is `ollama serve` running?") from e


class OllamaChat:
    def __init__(self, model, host=DEFAULT_HOST, temperature=0.3, seed=None,
                 num_ctx=8192, num_predict=512, timeout=300, transport=_http_post):
        self.model = model
        self.url = host.rstrip("/") + "/api/chat"
        self.options = {"temperature": temperature, "num_ctx": num_ctx,
                        "num_predict": num_predict}
        if seed is not None:
            self.options["seed"] = seed
        self.timeout = timeout
        self.transport = transport
        self.ledger = []  # one entry per call

    def chat(self, messages, tools=None):
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": dict(self.options)}
        if tools:
            payload["tools"] = tools
        t0 = time.perf_counter()
        data = self.transport(self.url, payload, self.timeout)
        result = parse_response(data)
        result.seconds = time.perf_counter() - t0
        self.ledger.append({"model": self.model, "prompt_tokens": result.prompt_tokens,
                            "completion_tokens": result.completion_tokens,
                            "seconds": round(result.seconds, 3)})
        return result

    def total_tokens(self):
        return sum(e["prompt_tokens"] + e["completion_tokens"] for e in self.ledger)
