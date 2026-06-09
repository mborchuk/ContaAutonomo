FROM python:3.14-slim

WORKDIR /app

# Patch OS packages (clears base-image CVEs in perl-base, libc6, util-linux, …)
# and install system deps for reportlab.
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends libffi-dev && \
    apt-get clean && \
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
