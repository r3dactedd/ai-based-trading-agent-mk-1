FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=registry_ca \
    if [ -f /run/secrets/registry_ca ]; then export PIP_CERT=/run/secrets/registry_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY *.py schema.sql ./
