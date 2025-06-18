# Stage 1: Build stage with dependencies
FROM python:3.9-slim as builder

WORKDIR /app

# Install iperf3 and build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc iperf3 libiperf-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Final runtime stage
FROM python:3.9-slim

WORKDIR /app

# Copy iperf3 binary and library from the builder stage
COPY --from=builder /usr/bin/iperf3 /usr/bin/iperf3
COPY --from=builder /usr/lib/x86_64-linux-gnu/libiperf.so.0 /usr/lib/x86_64-linux-gnu/libiperf.so.0

# Copy installed Python packages from the builder stage
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages

# Copy the exporter application code
COPY exporter.py .

# Expose the metrics port
EXPOSE 9876

# Set the entrypoint
CMD ["python", "exporter.py"]