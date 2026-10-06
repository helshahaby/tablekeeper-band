FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    PYTHONTZPATH=""

# IANA tz data is vendored from the tzdata package at build time; PYTHONTZPATH=""
# makes zoneinfo use only that copy, independent of the host or base image.
RUN pip install --no-cache-dir "tzdata==2025.2"

WORKDIR /srv
COPY app ./app

RUN python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin'); ZoneInfo('America/New_York')" \
    && python -m compileall -q app

EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8080'), timeout=2)" || exit 1

CMD ["python", "-m", "app.server"]
