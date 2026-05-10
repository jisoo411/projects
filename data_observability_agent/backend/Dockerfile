FROM python:3.12-slim

WORKDIR /app

# System deps for asyncpg (libpq) and Presidio (build-essential for spacy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Presidio NLP model (large — better PII recall in production)
RUN python -m spacy download en_core_web_lg

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
