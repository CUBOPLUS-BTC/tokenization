FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/tokenization/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY . /app

EXPOSE 8002

CMD ["uvicorn", "services.tokenization.main:app", "--host", "0.0.0.0", "--port", "8002"]
