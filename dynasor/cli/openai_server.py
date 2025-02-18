import argparse
import json
from typing import List, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from openai import OpenAI
from pydantic import BaseModel

from dynasor.core.cot import effort_level
from dynasor.core.cot import openai_chat_completion_stream




class DynasorOpenAIClient:
    # The Dynasor OpenAI Client is a wrapper that applys the chat template
    # and the reasoning stuff to the OpenAI API of vLLM.
    def __init__(
        self,
        model: str = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        probe: str = "... Oh, I suddenly got the answer to the whole problem, **Final Answer**\n\n\\[ \\boxed{",
        dynasor_saving_effort: tuple = (2, 32),
    ):
        """
        Initialize the OpenAI Chat Client.

        Args:
            api_key: OpenAI API key.
            model: The model name to use (default: deepseek-ai/DeepSeek-R1-Distill-Qwen-7B).
        """
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.probe = probe
        self.dynasor_saving_effort = dynasor_saving_effort


app = FastAPI()
client: Optional[DynasorOpenAIClient] = None

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Models for request validation
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


@app.get("/v1/models")
async def models():
    client = DynasorOpenAIClient()
    models = client.client.models.list()
    return models


# @app.post("/v1/chat/completions")
# async def chat_completions(request: ChatCompletionRequest):
#     """Handle chat completion requests."""
#     client = DynasorOpenAIClient()
#     print(f"request: {request}")
#     response = client.client.chat.completions.create(
#         model=request.model,
#         messages=request.messages,
#         temperature=request.temperature,
#         max_tokens=request.max_tokens,
#         stream=request.stream
#     )
#     return response

import re


def format_history(messages: List[ChatMessage]) -> str:
    """
    Convert chat conversation history into a prompt string.
    """
    formatted = ""
    for message in messages:
        role = message.role
        content = message.content
        if role == "system":
            formatted += "" + content + "\n"
        elif role == "user":
            formatted += "<｜User｜>" + content + "\n"
        else:
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
            formatted += "<｜Assistant｜>" + content + "\n"
    result = formatted + "<｜Assistant｜>"
    return result


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """Handle chat completion requests."""
    client = DynasorOpenAIClient()
    openai_client = client.client
    messages = request.messages
    prompt = format_history(messages)
    stream = request.stream

    # Check for extra body parameters
    dynasor_saving_effort = client.dynasor_saving_effort
    if hasattr(request, "extra_body") and request.extra_body:
        if "dynasor" in request.extra_body:
            dynasor_config = request.extra_body["dynasor"]
            if "saving_effort" in dynasor_config:
                dynasor_saving_effort = effort_level(dynasor_config["saving_effort"])

    generator = openai_chat_completion_stream(
        client=openai_client,
        model=request.model,
        prompt=prompt,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        dynasor_saving_effort=dynasor_saving_effort,
        probeing_suffix=client.probe,
    )
    if not stream:
        result = ""
        for i in generator:
            result += i
            print(result)
        return JSONResponse(content={"choices": [{"message": {"content": result}}]})
    else:
        import time

        async def stream_response():
            request_id = f"chatcmpl-{int(time.time())}"
            created_time = int(time.time())

            # Send the role first
            first_chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": request.model,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ],
            }
            print("yielding first chunk")
            yield f"data: {json.dumps(first_chunk)}\n\n"

            # Stream the content
            for content in generator:
                print("yielding content", content)
                chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": content},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk)}\n\n"

            # Send the final [DONE] message
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_response(), media_type="text/event-stream")


def main():
    parser = argparse.ArgumentParser(description="OpenAI Chat Client")
    parser.add_argument("--api-key", type=str, default="token-abc123", help="API key")
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000/v1",
        help="Base URL (default: http://localhost:8000/v1)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        help="Model name (default: deepseek-ai/DeepSeek-R1-Distill-Qwen-7B)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port (default: 8001)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--probe",
        type=str,
        default="... Oh, I suddenly got the answer to the whole problem, **Final Answer**\n\n\\[ \\boxed{",
        help="Probe (default: ... Oh, I suddenly got the answer to the whole problem, **Final Answer**\n\n\\[ \\boxed{",
    )
    parser.add_argument(
        "--dynasor-saving-effort",
        type=str,
        default="2,32",
        help="Dynasor saving effort. It is a tuple of two integers. The first integer is the number of consistent answer to get before early exit, and the second integer is the number of tokens before probing. (default: 2,32)",
    )
    args = parser.parse_args()
    global client

    dynasor_saving_effort = tuple(map(int, args.dynasor_saving_effort.split(",")))
    assert len(dynasor_saving_effort) == 2
    assert dynasor_saving_effort[0] >= 0
    assert dynasor_saving_effort[1] >= 0
    client = DynasorOpenAIClient(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        probe=args.probe,
        dynasor_saving_effort=dynasor_saving_effort,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
