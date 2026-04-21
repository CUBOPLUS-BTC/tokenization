FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY scripts/requirements-migrations.txt /tmp/requirements-migrations.txt
COPY services/auth/requirements.txt /tmp/requirements-auth.txt
RUN pip install -r /tmp/requirements-migrations.txt -r /tmp/requirements-auth.txt

COPY . /app

CMD ["python", "scripts/db_bootstrap.py", "--migrate-only"]