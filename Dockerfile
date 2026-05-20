FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py gcs_store.py config.py ai_analyze.py ai_chat.py index.html about.html sim.html favicon.svg ./
COPY appsheet_sync.py haifu_sync.py manage_jrn_sync.py ./
COPY docs/AI_SYSTEM_PROMPT.md docs/AI_CHAT_PROMPT.md docs/
COPY data/ data/

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "300", "server:app"]
