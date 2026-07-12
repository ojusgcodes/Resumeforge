# ResumeForge backend.
#
# Why a container: WeasyPrint (which renders the six resume templates from their
# real HTML, so the PDF matches the thumbnail) needs the Pango/Cairo system
# libraries. Render's native Python runtime won't let us apt-get those, so we
# ship them ourselves. This is NOT a return to Chromium — WeasyPrint has no
# browser and uses a small fraction of the memory that OOM-killed the old
# Playwright build on the 512MB tier.
FROM python:3.11-slim

# Runtime libraries for WeasyPrint. Liberation fonts are metric-compatible with
# Arial/Helvetica, which is what the resume templates ask for — without them the
# text reflows and the layout drifts from the preview.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libfribidi0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        shared-mime-info \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects PORT; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
