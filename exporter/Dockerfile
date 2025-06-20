# Stage 1: Build stage with dependencies
FROM python:3.9-slim as builder

# Declare TARGETARCH for use in this stage
ARG TARGETARCH
WORKDIR /app

# Install iperf3 and build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc iperf3 libiperf-dev && \
    rm -rf /var/lib/apt/lists/*

# Determine the correct libiperf source directory based on TARGETARCH
# and copy libiperf.so.0 to a canonical temporary location /tmp/lib/ within the builder stage.
RUN echo "Builder stage TARGETARCH: ${TARGETARCH}" && \
    LIBIPERF_SRC_DIR_SEGMENT="" && \
    if [ "${TARGETARCH}" = "amd64" ]; then \
        LIBIPERF_SRC_DIR_SEGMENT="x86_64-linux-gnu"; \
    elif [ "${TARGETARCH}" = "arm64" ]; then \
        LIBIPERF_SRC_DIR_SEGMENT="aarch64-linux-gnu"; \
    else \
        echo "Unsupported TARGETARCH in builder: ${TARGETARCH}" && exit 1; \
    fi && \
    mkdir -p /tmp/lib && \
    cp "/usr/lib/${LIBIPERF_SRC_DIR_SEGMENT}/libiperf.so.0" /tmp/lib/libiperf.so.0

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Final runtime stage
FROM python:3.9-slim

WORKDIR /app

# Copy iperf3 binary from the builder stage
COPY --from=builder /usr/bin/iperf3 /usr/bin/iperf3
# Copy the prepared libiperf.so.0 from the builder's canonical temporary location
# into a standard library path in the final image.
COPY --from=builder /tmp/lib/libiperf.so.0 /usr/lib/libiperf.so.0

# Copy installed Python packages from the builder stage
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages

# Copy the exporter application code
COPY exporter.py .

# Expose the metrics port
EXPOSE 9876

# Set the entrypoint
CMD ["python", "exporter.py"]