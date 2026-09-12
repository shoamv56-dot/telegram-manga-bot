FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
RUN mkdir -p /app/data

ENV CACHE_DB_PATH=/app/data/manga_cache.sqlite3

CMD ["python", "main.py"]
