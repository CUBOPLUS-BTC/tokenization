FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY scripts/requirements-migrations.txt /tmp/requirements-migrations.txt
COPY scripts/requirements-bootstrap.txt /tmp/requirements-bootstrap.txt
RUN pip install -r /tmp/requirements-migrations.txt -r /tmp/requirements-bootstrap.txt

COPY . /app

CMD ["python", "-u", "scripts/db_bootstrap.py", "--migrate-only"]