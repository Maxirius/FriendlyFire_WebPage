FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/auth

COPY requirements.txt .
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libffi-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y gcc libffi-dev \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

COPY app.py db.py security.py mojang.py mailer.py avatar.py ./

# Non-root runtime; DB lives in mounted volume /data
RUN useradd --system --uid 850 --create-home ffauth \
 && mkdir -p /data /srv/web \
 && chown -R ffauth:ffauth /srv/auth /data
USER ffauth

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", \
     "--port", "8000", "--workers", "2", "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
