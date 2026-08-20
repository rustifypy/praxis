FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY model_store ./model_store
RUN pip install --no-cache-dir .

ENV MODEL_STORE_PATH=/data/model.git \
    MODEL_STORE_PORT=8500
VOLUME /data
EXPOSE 8500

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['MODEL_STORE_PORT']+'/health').read()"

CMD ["model-store"]
