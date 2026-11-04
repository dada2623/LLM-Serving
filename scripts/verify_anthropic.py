"""Exercise the local Anthropic API, including a streamed tool round trip.

Only emits synthetic probe data; reads API_KEY from the project's ignored .env.
No shell command or external tool is executed by this probe.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--service", choices=("vllm", "sglang"), default="vllm")
parser.add_argument("--long-context", action="store_true", help="Also exercise chunked prefill")
parser.add_argument("--long-context-repeat", type=int, default=256,
                    help="Number of synthetic data lines in the long prompt")
parser.add_argument("--long-context-max-output", type=int, default=64,
                    help="Output token reservation for the long prompt request")
args = parser.parse_args()
MODEL = "Qwen3.5-9B-AWQ"
settings = dict(
    line.split("=", 1)
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines()
    if line.strip() and not line.lstrip().startswith("#") and "=" in line
)
port_field, default_port = (
    ("VLLM_PORT", "8000") if args.service == "vllm" else ("SGLANG_PORT", "30000")
)
BASE_URL = f"http://127.0.0.1:{settings.get(port_field, default_port)}"
MODEL = settings.get("SERVED_MODEL_NAME", MODEL)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path, payload):
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "Authorization": "Bearer " + settings["API_KEY"],
        },
    )
    try:
        return opener.open(request, timeout=300)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode()}") from None


def request_json(path, payload):
    with post(path, payload) as response:
        return json.load(response)


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    report = {"service": args.service, "base_url": BASE_URL, "model": MODEL,
              "checked_at": datetime.now(timezone.utc).isoformat()}
    output = ROOT / "outputs" / f"{args.service}-anthropic-check.json"
    output.parent.mkdir(exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    report["status"] = "in_progress"
    save()
    messages = [{"role": "user", "content": "Reply with just hello."}]
    report["count_tokens"] = request_json(
        "/v1/messages/count_tokens", {"model": MODEL, "messages": messages}
    )
    check(report["count_tokens"]["input_tokens"] > 0, "Token counting failed")
    hello = request_json(
        "/v1/messages",
        {"model": MODEL, "max_tokens": 64, "messages": messages, "temperature": 0},
    )
    check(hello["stop_reason"] == "end_turn", "Text response did not finish")
    check(
        any(b.get("type") == "text" and "hello" in b.get("text", "").lower()
            for b in hello["content"]),
        "No expected greeting in text response",
    )
    report["text"] = hello
    chinese = request_json("/v1/messages", {
        "model": MODEL, "max_tokens": 96, "temperature": 0,
        "messages": [{"role": "user", "content": "请用一句简短的中文说明你能提供什么帮助。"}],
    })
    check(chinese["stop_reason"] == "end_turn", "Chinese response did not finish")
    check(any(
        "\u4e00" <= char <= "\u9fff"
        for block in chinese["content"] if block.get("type") == "text"
        for char in block.get("text", "")
    ), "No Chinese text returned")
    report["chinese"] = chinese
    save()
    if args.long_context:
        long_messages = [{"role": "user", "content": (
            "下面是测试数据。只回复末尾的口令，不要复述数据。\n"
            + "数据行：甲乙丙丁戊己庚辛壬癸。\n" * args.long_context_repeat
            + "口令：chunked-prefill-ok"
        )}]
        report["long_context_tokens"] = request_json("/v1/messages/count_tokens", {
            "model": MODEL, "messages": long_messages,
        })["input_tokens"]
        check(report["long_context_tokens"] > 512, "Prompt did not exercise chunked prefill")
        report["long_context_output_reservation"] = args.long_context_max_output
        report["long_context_total_reservation"] = (
            report["long_context_tokens"] + args.long_context_max_output
        )
        long_response = request_json("/v1/messages", {
            "model": MODEL, "max_tokens": args.long_context_max_output,
            "temperature": 0, "messages": long_messages,
        })
        check(long_response["stop_reason"] == "end_turn", "Long prompt response did not finish")
        check(any("chunked-prefill-ok" in b.get("text", "") for b in long_response["content"]),
              "Long prompt response did not return the expected marker")
        report["long_context_response"] = long_response
        save()
    tools = [{
        "name": "get_probe_value",
        "description": "Get the stored probe value. Call this tool to answer the user.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    }]
    messages = [{"role": "user", "content": "Use get_probe_value with key demo, then tell me its value."}]
    events = []
    with post("/v1/messages", {
        "model": MODEL, "max_tokens": 256, "temperature": 0,
        "messages": messages, "tools": tools,
        "tool_choice": {"type": "auto"}, "stream": True,
    }) as response:
        for line in response:
            if line.startswith(b"data: "):
                events.append(json.loads(line[6:]))
    check(not any(e["type"] == "error" for e in events), "Error in SSE stream")
    blocks = {}
    arguments = {}
    for event in events:
        if event["type"] == "content_block_start":
            blocks[event["index"]] = event["content_block"]
        elif event["type"] == "content_block_delta":
            index, delta = event["index"], event["delta"]
            if delta["type"] == "input_json_delta":
                arguments[index] = arguments.get(index, "") + delta["partial_json"]
            elif delta["type"] == "text_delta":
                blocks[index]["text"] = blocks[index].get("text", "") + delta["text"]
    for index, value in arguments.items():
        blocks[index]["input"] = json.loads(value)
    content = [block for _, block in sorted(blocks.items())]
    calls = [block for block in content if block["type"] == "tool_use"]
    if len(calls) != 1:
        report["status"] = "failed"
        report["failed_stream_events"] = events
        save()
    check(len(calls) == 1, "Expected exactly one streamed tool call")
    call = calls[0]
    check(call["name"] == "get_probe_value" and call["input"] == {"key": "demo"},
          "Incorrect tool call name or arguments")
    check(any(e["type"] == "message_stop" for e in events), "Stream did not end")
    check(any(e["type"] == "message_delta" and
              e.get("delta", {}).get("stop_reason") == "tool_use" for e in events),
          "Stream did not stop for tool use")
    report["streamed_tool_call"] = call
    save()
    messages += [
        {"role": "assistant", "content": content},
        {"role": "user", "content": [{"type": "tool_result",
          "tool_use_id": call["id"], "content": "probe-ok-42"}]},
    ]
    result = request_json("/v1/messages", {
        "model": MODEL, "max_tokens": 128, "temperature": 0,
        "messages": messages, "tools": tools, "tool_choice": {"type": "none"},
    })
    check(result["stop_reason"] == "end_turn", "Tool round trip did not finish")
    check(any("probe-ok-42" in block.get("text", "") for block in result["content"]),
          "Model did not use the synthetic tool result")
    report["tool_result_response"] = result
    report["status"] = "passed"
    save()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
