FROM python:3.14-slim

WORKDIR /app

# System deps for reportlab
RUN apt-get update && \
    apt-get install -y --no-install-recommends libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Writable dirs for runtime data
RUN mkdir -p instance logs backups invoices_pdf expenses_files documents_files \
    tax_forms invoice_logos pdf_signature_files static

ENV FLASK_DEBUG=0
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

VOLUME ["/app/instance", "/app/backups", "/app/logs"]

ENTRYPOINT ["python", "docker_entrypoint.py"]
