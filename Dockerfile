FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git make && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN make setup
ENV FORGE_RUNS_DIR=/output/runs
ENTRYPOINT ["/app/.venv/bin/python", "-m", "forge"]
CMD ["--help"]
