import json
from asyncio import CancelledError, Lock, Queue, sleep
from collections import deque
from contextlib import asynccontextmanager
from random import Random, randint
from time import monotonic
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.api_core.exceptions import NotFound
from google.cloud import firestore
from httpx import AsyncClient
from pydantic import BaseModel, NonNegativeInt

client = AsyncClient()
db = firestore.AsyncClient(project="scisoc-backend")
BUFFER_TARGET = 25

REQUEST_LOCK = Lock()
WAIT_UNTIL = float("-inf")

sse_queues: dict[int, Queue] = {}

random_buffer = deque()
groups = db.collection("groups")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await client.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])


@app.get("/")
async def welcome() -> str:
    return "Hello, this is the backend!"


@app.post("/flip")
async def coin_flip(id: str) -> Literal["head", "tail"]:
    global WAIT_UNTIL

    prob = 0.3 * Random(id).random() + 0.35

    if not len(random_buffer):
        async with REQUEST_LOCK:
            if monotonic() < WAIT_UNTIL:
                await sleep(max(WAIT_UNTIL - monotonic(), 0.0))
            response = await client.post(
                "https://api.random.org/json-rpc/4/invoke",
                json={
                    "jsonrpc": "2.0",
                    "method": "generateDecimalFractions",
                    "params": {
                        "apiKey": "f0727e79-2b4e-4ed9-a05c-dc7fe92e7c4a",
                        "n": BUFFER_TARGET,
                        "decimalPlaces": 14,
                    },
                    "id": id,
                },
            )
            data = response.json()["result"]
            WAIT_UNTIL = monotonic() + data["advisoryDelay"] * 0.001
            random_buffer.extend(data["random"]["data"])

    if random_buffer.popleft() > prob:
        outcome = "head"
    else:
        outcome = "tail"

    group = groups.document(id)
    counter = group.collection(outcome).document(str(randint(0, 9)))
    try:
        await counter.update({"count": firestore.Increment(1)})
    except NotFound:
        for i in range(10):
            await group.collection("head").document(str(i)).set({"count": 0})
            await group.collection("tail").document(str(i)).set({"count": 0})
        await counter.update({"count": firestore.Increment(1)})

    for q in sse_queues.values():
        await q.put((id, outcome))

    return outcome


class groupAggregateFlips(BaseModel):
    heads: NonNegativeInt
    tails: NonNegativeInt


async def counter(id: str):
    head_total = 0
    async for doc in groups.document(id).collection("head").stream():
        head_total += doc.get("count")

    tail_total = 0
    async for doc in groups.document(id).collection("tail").stream():
        tail_total += doc.get("count")

    return {"heads": head_total, "tails": tail_total}


@app.get("/count")
async def count_flips(id: str) -> groupAggregateFlips:
    return await counter(id)


@app.get("/groups")
async def list_groups() -> list[str]:
    return [doc.id async for doc in groups.list_documents()]


async def info_streamer(q: Queue):
    data = {doc.id: (await counter(doc.id)) async for doc in groups.list_documents()}

    thread = randint(0, 1 << 64 - 1)
    while thread in sse_queues:
        thread = randint(0, 1 << 64 - 1)
    sse_queues[thread] = q

    yield f"event:sync\ndata:{json.dumps(data)}\n\n"

    try:
        while True:
            event = await q.get()
            yield f"event:incr\ndata:{json.dumps(event)}\n\n"
    except CancelledError:
        del sse_queues[thread]


@app.get("/stream")
async def stream_info() -> StreamingResponse:
    queue = Queue()
    return StreamingResponse(info_streamer(queue), media_type="text/event-stream")
