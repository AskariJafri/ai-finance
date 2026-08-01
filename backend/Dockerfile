FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy source, then install (the package build needs the `app/` dir present).
COPY . .
RUN pip install --upgrade pip && pip install .

EXPOSE 8000

# entrypoint runs migrations then starts the server (see docker-compose command override).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
