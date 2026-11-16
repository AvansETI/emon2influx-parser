# Alternative to the systemd service (doc/architecture.md §11).
# Run with: docker run --env-file .env -v spool:/data/spool -v logs:/data/logs <image>
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --system --home /data emon2influx \
    && mkdir -p /data/spool /data/logs && chown -R emon2influx /data
USER emon2influx
WORKDIR /data
ENV SPOOL_DIR=/data/spool LOG_DIR=/data/logs QUARANTINE_FILE=/data/logs/quarantine.jsonl
CMD ["python", "-m", "emon2influx"]
