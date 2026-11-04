"""Verify the mapped OpenAI API with a real generation request."""

import argparse
import json
import os
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"}

    def request(path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        query = urllib.request.Request(
            base + path, data=data, headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(query, timeout=180) as response:
            return json.load(response)

    models = request("/models")["data"]
    if not models:
        raise RuntimeError("The API returned no models")
    model = models[0]["id"]
    response = request("/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with one word: hello"}],
        "temperature": 0,
        "max_tokens": 32,
        "chat_template_kwargs": {"enable_thinking": False},
    })
    message = response["choices"][0]["message"]
    if not message.get("content"):
        raise RuntimeError(f"No text returned by the model: {message}")
    print(json.dumps({"base_url": base, "model": model,
                      "response": message["content"], "status": "passed"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
