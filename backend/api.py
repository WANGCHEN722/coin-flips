from collections import deque
from contextlib import asynccontextmanager
from random import Random, randint
from typing import Literal

from fastapi import FastAPI
from google.api_core.exceptions import NotFound
from google.cloud import firestore
from httpx import AsyncClient
from pydantic import BaseModel, NonNegativeInt

client = AsyncClient()
db = firestore.AsyncClient(project="scisoc-backend")
BUFFER_TARGET = 25

random_buffer = deque()
groups = db.collection("groups")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def welcome() -> str:
    return "Hello, this is the backend!"


@app.get("/flip")
async def coin_flip(id: str) -> Literal["head", "tail"]:
    prob = Random(id).random()

    if not len(random_buffer):
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
        random_buffer.extend(response.json()["result"]["random"]["data"])

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

    return outcome


class groupAggregateFlips(BaseModel):
    heads: NonNegativeInt
    tails: NonNegativeInt


@app.get("/count")
async def count_flips(id: str) -> groupAggregateFlips:
    head_total = 0
    async for doc in groups.document(id).collection("head").stream():
        head_total += doc.get("count")

    tail_total = 0
    async for doc in groups.document(id).collection("tail").stream():
        tail_total += doc.get("count")

    return {"heads": head_total, "tails": tail_total}
