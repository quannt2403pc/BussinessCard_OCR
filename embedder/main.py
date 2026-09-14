import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "intfloat/multilingual-e5-small"
MAX_TEXTS = 64
BATCH_SIZE = 16
PREFIXES: dict[str, str] = {"passage": "passage: ", "query": "query: "}


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)
    kind: Literal["passage", "query"]

    @field_validator("texts")
    @classmethod
    def reject_blank_texts(cls, texts: list[str]) -> list[str]:
        if any(not text.strip() for text in texts):
            raise ValueError("texts không được chứa chuỗi rỗng")
        return texts


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    dim: int
    model: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model: str
    dim: int
    max_seq_length: int


def resolve_model_name() -> str:
    requested = os.environ.get("EMBEDDING_MODEL", "").strip()
    baked = os.environ.get("BAKED_EMBEDDING_MODEL", "").strip()
    if baked and requested and requested != baked:
        raise RuntimeError(
            f"EMBEDDING_MODEL={requested!r} nhưng image chỉ chứa {baked!r}; hãy build lại image embedder"
        )
    return baked or requested or DEFAULT_MODEL


def with_prefix(text: str, kind: str) -> str:
    body = text.strip()
    for prefix in PREFIXES.values():
        marker = prefix.strip()
        if body.startswith(marker):
            body = body[len(marker) :].lstrip()
            break
    return PREFIXES[kind] + body


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    name = resolve_model_name()
    model = SentenceTransformer(name, device="cpu")
    app.state.model = model
    app.state.model_name = name
    app.state.dim = int(model.encode(["dim"], normalize_embeddings=True).shape[1])
    app.state.max_seq_length = int(model.max_seq_length)
    yield


app = FastAPI(title="embedder", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model=app.state.model_name,
        dim=app.state.dim,
        max_seq_length=app.state.max_seq_length,
    )


@app.post("/embed", response_model=EmbedResponse)
def embed(request: EmbedRequest) -> EmbedResponse:
    texts = [with_prefix(text, request.kind) for text in request.texts]
    vectors = app.state.model.encode(texts, normalize_embeddings=True, batch_size=BATCH_SIZE)
    return EmbedResponse(vectors=vectors.tolist(), dim=app.state.dim, model=app.state.model_name)
