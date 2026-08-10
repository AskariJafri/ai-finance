FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Hugging Face Spaces runs the container as UID 1000 and will not write as root.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

# Dependencies first, from the pinned list, so a source edit does not reinstall
# the world on every build (free-tier builders are slow).
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN pip install --no-deps . && chown -R app:app /app
USER app

# Spaces serves 7860; other hosts inject $PORT. Both are honoured.
ENV PORT=7860
EXPOSE 7860

# Migrations run here, not in an orchestrator hook — this is the only startup
# path a plain container host executes. Shell form so ${PORT} expands.
CMD alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
