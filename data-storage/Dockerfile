FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN test -s /usr/local/bin/uvicorn \
    && python -c "import fastapi, llama_cpp; assert fastapi.__file__ and llama_cpp.__file__"

COPY app ./app
COPY models ./models
COPY scripts ./scripts

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]