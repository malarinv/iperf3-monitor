# Stage 1: Build stage with dependencies
FROM python:3.9-slim as builder

# Declare TARGETARCH for use in this stage
ARG TARGETARCH
WORKDIR /app

# Minimal dependencies for builder stage if any Python packages had C extensions.
# Assuming requirements.txt does not need gcc or other build tools for now.
# If pip install fails later, add necessary build tools (e.g., gcc, python3-dev) here.
RUN apt-get update && \
    # apt-get install -y --no-install-recommends gcc python3-dev # Example if needed
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Final runtime stage
FROM python:3.9-slim

WORKDIR /app

# Install iperf3 and its runtime dependency libsctp1 directly in the final stage.
# This simplifies the Dockerfile by removing the need to copy iperf3 components from the builder.
RUN apt-get update && \
    apt-get install -y --no-install-recommends iperf3 libsctp1 && \
    rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from the builder stage
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages

# Copy the exporter application code
COPY exporter.py .

# Expose the metrics port
EXPOSE 9876

# Set the entrypoint
CMD ["python", "exporter.py"]
