FROM python:3.11.9-slim

ARG INSTALL_ML=false
ARG TORCH_VERSION=2.10.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/CodeRadar

WORKDIR /app

COPY requirements-api.txt /app/CodeRadar/requirements-api.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /app/CodeRadar/requirements-api.txt

RUN if [ "${INSTALL_ML}" = "true" ]; then \
        python -m pip install "torch==${TORCH_VERSION}" \
            --index-url https://download.pytorch.org/whl/cpu && \
        python -m pip install "sentence-transformers==5.6.0"; \
    fi

COPY . /app/CodeRadar
WORKDIR /app/CodeRadar

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
