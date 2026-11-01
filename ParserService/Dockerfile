FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Очікується попередній pack.sh → runtime/ з cosmyparser + web.parser_*
COPY . .

ENV PARSER_HOST=0.0.0.0
ENV PARSER_PORT=8091
ENV PARSER_CATALOG_SQLITE_ONLY=1
ENV PARSER_PROJECT_ROOT=/app/runtime

EXPOSE 8091
CMD ["python", "run.py"]
