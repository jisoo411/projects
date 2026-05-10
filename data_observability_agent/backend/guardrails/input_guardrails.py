import re
from dataclasses import dataclass
from functools import lru_cache

from openai import AsyncOpenAI
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

from config import settings


@dataclass
class GuardrailResult:
    blocked: bool
    block_reason: str | None  # "injection" | "off_topic" | None
    redacted_query: str


_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?(prior|previous|above)\s+instructions",
    r"forget\s+everything",
    r"you\s+are\s+now\s+(a|an)\s+\w+",
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"output\s+(your\s+)?(system\s+)?prompt",
    r"print\s+(your\s+)?(system\s+)?prompt",
    r"jailbreak",
    r"DAN\s+mode",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE | re.DOTALL)


@lru_cache(maxsize=1)
def _analyzer() -> AnalyzerEngine:
    return AnalyzerEngine()


@lru_cache(maxsize=1)
def _anonymizer() -> AnonymizerEngine:
    return AnonymizerEngine()


@lru_cache(maxsize=1)
def _openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


def _redact_pii(text: str) -> str:
    results = _analyzer().analyze(text=text, language="en")
    return _anonymizer().anonymize(text=text, analyzer_results=results).text


async def _classify_topic(query: str) -> bool:
    """Returns True if the query is on-topic (pipeline/data observability related)."""
    resp = await _openai_client().chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        max_tokens=5,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a topic classifier. Reply with exactly 'YES' if the user message "
                    "is about data pipelines, Airflow DAGs, data quality, ETL processes, "
                    "or data observability. Reply 'NO' for anything else."
                ),
            },
            {"role": "user", "content": query},
        ],
    )
    return resp.choices[0].message.content.strip().upper().startswith("YES")


async def check_input(query: str) -> GuardrailResult:
    """Run PII redaction, injection detection, and topic classification in order.

    Returns early on injection (skips LLM call). redacted_query always populated
    for audit logging regardless of block outcome.
    """
    redacted = _redact_pii(query)

    if _INJECTION_RE.search(query):
        return GuardrailResult(blocked=True, block_reason="injection", redacted_query=redacted)

    on_topic = await _classify_topic(redacted)
    if not on_topic:
        return GuardrailResult(blocked=True, block_reason="off_topic", redacted_query=redacted)

    return GuardrailResult(blocked=False, block_reason=None, redacted_query=redacted)
