# Stage 1: Build stage with dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Final runtime stage
FROM python:3.12-slim

WORKDIR /app

# Install iperf3
RUN apt-get update && \
    apt-get install -y --no-install-recommends iperf3 && \
    rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from the builder stage
COPY --from=builder /install /usr/local

# Copy the exporter application code
COPY exporter.py .

# Expose the metrics port
EXPOSE 9876

# Run as non-root user (nobody)
USER 65534:65534

# Set the entrypoint
CMD ["python", "exporter.py"]

