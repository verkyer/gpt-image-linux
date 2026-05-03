ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE} AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM ${PYTHON_BASE_IMAGE} AS runtime

RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -s /bin/bash -m appuser

WORKDIR /app
RUN mkdir images data && \
    chown -R appuser:appgroup images data
COPY --from=builder --chown=appuser:appgroup /install /usr/local
COPY --chown=appuser:appgroup app/ ./app/
COPY --chown=appuser:appgroup static/ ./static/

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9090"]
