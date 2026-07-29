# Bewusst python:slim (Debian) statt eines Alpine-Images: Alpines musl-libc hat in der
# Vergangenheit bei diesem Nutzer schon zu kaputten pip-Installationen gefuehrt (siehe
# Anmerkung in README). Debian-slim ist etwas groesser, aber deutlich weniger fehleranfaellig
# fuer pandas/openpyxl/google-api-python-client, die alle C-Extensions mitbringen.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + alle noetigen Systembibliotheken fuer Playwright (--with-deps macht das
# passende apt-get fuer dieses Debian-Basisimage automatisch). Das macht das Image deutlich
# groesser (mehrere hundert MB) - bewusster Trade-off, siehe Begruendung in requirements.txt.
RUN playwright install --with-deps chromium

COPY app/ .

CMD ["python3", "-u", "main.py"]
