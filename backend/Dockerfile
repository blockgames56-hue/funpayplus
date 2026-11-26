FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STORAGE_DIR=/app/storage
RUN groupadd --gid 10001 pulse && useradd --uid 10001 --gid pulse --create-home pulse
WORKDIR /app
COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels
COPY --chown=pulse:pulse . .
RUN mkdir -p /app/storage && chown pulse:pulse /app/storage
USER pulse
EXPOSE 8765
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn app.main:app --bind 0.0.0.0:8765 --workers 1 --worker-class uvicorn.workers.UvicornWorker --timeout 90 --graceful-timeout 110 --access-logfile /dev/null"]
