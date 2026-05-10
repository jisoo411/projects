from functools import lru_cache
from openai import AsyncOpenAI
from config import settings


@lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key, max_retries=settings.openai_max_retries)


async def embed(text: str) -> list[float]:
    resp = await _client().embeddings.create(model="text-embedding-3-small", input=text)
    return resp.data[0].embedding
