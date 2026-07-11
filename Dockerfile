FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ src/
COPY README.md ./

# Writable dir for probe dumps if ever run in-pod (mounted emptyDir in k8s).
RUN mkdir -p /app/data

# Idempotent — the CronJob script runs `migrate` before `snapshot` each night.
ENTRYPOINT ["uv", "run", "--no-dev", "duo-tracker"]
