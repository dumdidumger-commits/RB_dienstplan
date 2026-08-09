# Gleiche Basis wie das Nachbar-Add-on "Vivendi Dienstplan Sync": python:slim (Debian) statt
# Alpine, aus denselben Gruenden (Alpines musl-libc hat bei diesem Nutzer schon zu kaputten
# pip-Installationen gefuehrt).
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium fuer Playwright - ZenWave ist wie Vivendi eine JavaScript-SPA (Next.js), ein reiner
# HTTP-Client reicht nicht (siehe Modul-Docstring in app/zenwave.py).
RUN playwright install --with-deps chromium

COPY app/ .

CMD ["python3", "-u", "main.py"]
