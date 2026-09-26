"""
Prometheus exporter for iperf3 network performance monitoring.

This script runs iperf3 tests between the node it's running on (source) and
other iperf3 server pods discovered in a Kubernetes cluster. It then exposes
these metrics for Prometheus consumption.
"""
import os
import sys
import time
import json
import signal
import logging
import argparse
import subprocess
from kubernetes import client, config
from prometheus_client import start_http_server, Gauge

# --- Global Configuration & Setup ---

parser = argparse.ArgumentParser(description="iperf3 Prometheus exporter.")
parser.add_argument(
    '--log-level',
    default=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
    help='Set the logging level. Overrides LOG_LEVEL environment variable. (Default: INFO)'
)
args, _ = parser.parse_known_args()
log_level_str = args.log_level

numeric_level = getattr(logging, log_level_str.upper(), None)
if not isinstance(numeric_level, int):
    logging.error(f"Invalid log level: {log_level_str}. Defaulting to INFO.")
    numeric_level = logging.INFO
logging.basicConfig(level=numeric_level, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Prometheus Metrics Definition ---
IPERF_BANDWIDTH_MBPS = Gauge(
    'iperf_network_bandwidth_mbps',
    'Network bandwidth measured by iperf3 in Megabits per second (Mbps)',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_JITTER_MS = Gauge(
    'iperf_network_jitter_ms',
    'Network jitter measured by iperf3 in milliseconds (ms) for UDP tests',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_PACKETS_TOTAL = Gauge(
    'iperf_network_packets_total',
    'Total packets transmitted/received during the iperf3 UDP test',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_LOST_PACKETS = Gauge(
    'iperf_network_lost_packets_total',
    'Total lost packets during the iperf3 UDP test',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_TEST_SUCCESS = Gauge(
    'iperf_test_success',
    'Indicates if the iperf3 test was successful (1) or failed (0)',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_TCP_RETRANSMITS = Gauge(
    'iperf_network_tcp_retransmits_total',
    'Total TCP retransmits measured by iperf3 during the test',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_TCP_RTT_MS = Gauge(
    'iperf_network_tcp_rtt_ms',
    'Mean TCP round-trip time in milliseconds (ms) measured by iperf3',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_TEST_DURATION_SECONDS = Gauge(
    'iperf_network_test_duration_seconds',
    'Duration of the iperf3 test in seconds',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_LAST_TEST_TIMESTAMP_SECONDS = Gauge(
    'iperf_exporter_last_success_timestamp_seconds',
    'Unix timestamp of the last completed test cycle',
    ['source_node']
)
IPERF_SERVERS_DISCOVERED = Gauge(
    'iperf_exporter_servers_discovered',
    'Number of target iperf3 servers discovered in the cluster',
    ['source_node']
)

# --- Graceful Shutdown & Exceptions ---
shutdown_requested = False
active_subprocess = None

def signal_handler(signum, frame):
    global shutdown_requested, active_subprocess
    logging.info(f"Received signal {signum}. Initiating graceful shutdown...")
    shutdown_requested = True
    if active_subprocess and active_subprocess.poll() is None:
        try:
            active_subprocess.kill()
        except Exception:
            pass

class IperfStallException(Exception):
    """Raised when an iperf3 test experiences a silent or busy-wait stall."""
    pass

class IperfTimeoutException(Exception):
    """Raised when an iperf3 test exceeds its maximum expected deadline."""
    pass

# --- Kubernetes Discovery ---
def discover_iperf_servers(source_node_name):
    """
    Discovers iperf3 server pods within a Kubernetes cluster.
    """
    try:
        config.load_incluster_config()
        v1 = client.CoreV1Api()

        namespace = os.getenv('IPERF_SERVER_NAMESPACE', 'default')
        label_selector = os.getenv('IPERF_SERVER_LABEL_SELECTOR', 'app=iperf3-server')

        logging.info(f"Discovering iperf3 servers with label '{label_selector}' in namespace '{namespace}'")

        ret = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector, watch=False)

        servers = []
        for item in ret.items:
            if item.status.pod_ip and item.status.phase == 'Running':
                servers.append({
                    'ip': item.status.pod_ip,
                    'node_name': item.spec.node_name
                })
        logging.info(f"Discovered {len(servers)} iperf3 server pods in namespace '{namespace}'.")
        IPERF_SERVERS_DISCOVERED.labels(source_node=source_node_name).set(len(servers))
        return servers
    except config.ConfigException as e:
        logging.error(f"Kubernetes config error: {e}. Is the exporter running in a cluster with RBAC permissions?")
        return []
    except Exception as e:
        logging.error(f"Error discovering iperf servers: {e}")
        return []

# --- iperf3 Subprocess Execution with Stall Detection ---
def execute_iperf_test(server_ip, server_port, protocol, duration, connect_timeout_ms, stall_threshold_sec):
    """
    Executes the iperf3 CLI in a subprocess with JSON output and active stall detection.
    """
    global active_subprocess

    cmd = [
        "iperf3",
        "-c", server_ip,
        "-p", str(server_port),
        "-t", str(duration),
        "--connect-timeout", str(connect_timeout_ms),
        "-J",
    ]
    if protocol == 'udp':
        cmd.append("-u")

    logging.debug(f"Executing: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    active_subprocess = proc

    start_time = time.monotonic()
    # Maximum deadline: connect timeout + duration + stall threshold buffer
    connect_timeout_sec = connect_timeout_ms / 1000.0
    max_deadline = start_time + connect_timeout_sec + duration + stall_threshold_sec

    last_io_change = start_time
    last_wchar = 0
    test_transfer_detected = False

    try:
        while proc.poll() is None:
            if shutdown_requested:
                proc.kill()
                proc.wait()
                raise KeyboardInterrupt("Shutdown requested")

            now = time.monotonic()
            if now > max_deadline:
                proc.kill()
                proc.wait()
                raise IperfTimeoutException(f"iperf3 test exceeded absolute deadline of {max_deadline - start_time:.1f}s")

            time.sleep(0.5)

            # Check I/O progress via /proc/<pid>/io to detect busy-spin without I/O
            try:
                with open(f"/proc/{proc.pid}/io", "r") as f:
                    for line in f:
                        if line.startswith("wchar:"):
                            wchar = int(line.split()[1])
                            if wchar > last_wchar:
                                last_wchar = wchar
                                last_io_change = now
                                test_transfer_detected = True
                            break
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass

            # If traffic was active and has now completely ceased progressing for >= stall_threshold_sec
            if test_transfer_detected and (now - last_io_change) >= stall_threshold_sec:
                proc.kill()
                proc.wait()
                raise IperfStallException(f"iperf3 stalled: data transfer ceased for {now - last_io_change:.1f}s")

        stdout, stderr = proc.communicate()
        return proc.returncode, stdout, stderr
    finally:
        active_subprocess = None

# --- Metric Reset Helper ---
def reset_failure_metrics(labels):
    IPERF_TEST_SUCCESS.labels(**labels).set(0)
    for g in (
        IPERF_BANDWIDTH_MBPS,
        IPERF_JITTER_MS,
        IPERF_PACKETS_TOTAL,
        IPERF_LOST_PACKETS,
        IPERF_TCP_RETRANSMITS,
        IPERF_TCP_RTT_MS,
        IPERF_TEST_DURATION_SECONDS,
    ):
        try:
            g.labels(**labels).set(0)
        except KeyError:
            pass

# --- Metric Parsing ---
def parse_and_publish_metrics(data, source_node, dest_node, protocol):
    """
    Parses iperf3 JSON result and updates Prometheus gauges.
    """
    labels = {'source_node': source_node, 'destination_node': dest_node, 'protocol': protocol}

    if not data or not isinstance(data, dict):
        logging.warning(f"Test from {source_node} to {dest_node} ({protocol.upper()}) failed: empty or invalid result")
        reset_failure_metrics(labels)
        return

    if data.get("error"):
        error_msg = data.get("error")
        logging.warning(f"Test from {source_node} to {dest_node} ({protocol.upper()}) failed: {error_msg}")
        reset_failure_metrics(labels)
        return

    end = data.get("end")
    if not end or not isinstance(end, dict):
        logging.warning(f"Test from {source_node} to {dest_node} ({protocol.upper()}) failed: missing 'end' summary in JSON")
        reset_failure_metrics(labels)
        return

    # Successful test execution
    IPERF_TEST_SUCCESS.labels(**labels).set(1)

    sum_received = end.get("sum_received") or {}
    sum_sent = end.get("sum_sent") or {}
    sum_udp = end.get("sum") or {}

    bandwidth_mbps = 0.0

    if protocol == 'tcp':
        if "bits_per_second" in sum_received and sum_received["bits_per_second"] is not None:
            bandwidth_mbps = sum_received["bits_per_second"] / 1_000_000.0
        elif "bits_per_second" in sum_sent and sum_sent["bits_per_second"] is not None:
            bandwidth_mbps = sum_sent["bits_per_second"] / 1_000_000.0

        # TCP Retransmits
        retransmits = sum_sent.get("retransmits", 0)
        IPERF_TCP_RETRANSMITS.labels(**labels).set(retransmits)

        # TCP RTT
        mean_rtt_us = 0.0
        streams = end.get("streams", [])
        if streams and isinstance(streams, list):
            sender_info = streams[0].get("sender", {})
            mean_rtt_us = sender_info.get("mean_rtt", sender_info.get("rtt", 0.0))
        IPERF_TCP_RTT_MS.labels(**labels).set(mean_rtt_us / 1000.0)

        # Zero out UDP-specific gauges
        IPERF_JITTER_MS.labels(**labels).set(0)
        IPERF_PACKETS_TOTAL.labels(**labels).set(0)
        IPERF_LOST_PACKETS.labels(**labels).set(0)

        # Test duration
        test_seconds = sum_received.get("seconds", sum_sent.get("seconds", 0.0))
        IPERF_TEST_DURATION_SECONDS.labels(**labels).set(test_seconds)
    else:
        if "bits_per_second" in sum_udp and sum_udp["bits_per_second"] is not None:
            bandwidth_mbps = sum_udp["bits_per_second"] / 1_000_000.0
        elif "bits_per_second" in sum_sent and sum_sent["bits_per_second"] is not None:
            bandwidth_mbps = sum_sent["bits_per_second"] / 1_000_000.0

        IPERF_JITTER_MS.labels(**labels).set(sum_udp.get("jitter_ms", 0.0) or 0.0)
        IPERF_PACKETS_TOTAL.labels(**labels).set(sum_udp.get("packets", 0) or 0)
        IPERF_LOST_PACKETS.labels(**labels).set(sum_udp.get("lost_packets", 0) or 0)
        IPERF_TCP_RETRANSMITS.labels(**labels).set(0)
        IPERF_TCP_RTT_MS.labels(**labels).set(0)

        test_seconds = sum_udp.get("seconds", 0.0)
        IPERF_TEST_DURATION_SECONDS.labels(**labels).set(test_seconds)

    IPERF_BANDWIDTH_MBPS.labels(**labels).set(bandwidth_mbps)

# --- Test Execution Wrapper ---
def run_iperf_test(server_ip, server_port, protocol, source_node_name, dest_node_name, duration, connect_timeout_ms, stall_threshold_sec):
    """
    Runs a single iperf3 test with stall monitoring and publishes metrics.
    """
    logging.info(f"Running iperf3 {protocol.upper()} test from {source_node_name} to {dest_node_name} ({server_ip}:{server_port})")
    labels = {'source_node': source_node_name, 'destination_node': dest_node_name, 'protocol': protocol}

    try:
        returncode, stdout, stderr = execute_iperf_test(
            server_ip=server_ip,
            server_port=server_port,
            protocol=protocol,
            duration=duration,
            connect_timeout_ms=connect_timeout_ms,
            stall_threshold_sec=stall_threshold_sec,
        )

        if not stdout.strip():
            logging.warning(f"iperf3 produced no stdout for {dest_node_name}. Stderr: {stderr.strip()}")
            reset_failure_metrics(labels)
            return

        try:
            result_json = json.loads(stdout)
        except json.JSONDecodeError as e:
            logging.warning(f"Failed to parse iperf3 JSON output for {dest_node_name}: {e}. Output: {stdout[:200]}")
            reset_failure_metrics(labels)
            return

        parse_and_publish_metrics(result_json, source_node_name, dest_node_name, protocol)
    except IperfStallException as e:
        logging.warning(f"Test from {source_node_name} to {dest_node_name} ({protocol.upper()}) aborted: {e}")
        reset_failure_metrics(labels)
    except IperfTimeoutException as e:
        logging.warning(f"Test from {source_node_name} to {dest_node_name} ({protocol.upper()}) timed out: {e}")
        reset_failure_metrics(labels)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        logging.error(f"Unexpected exception during iperf3 test for {dest_node_name}: {e}")
        reset_failure_metrics(labels)

# --- Main Exporter Loop ---
def main_loop():
    """
    Main operational loop of the iperf3 exporter.
    """
    test_interval = int(os.getenv('IPERF_TEST_INTERVAL', 300))
    server_port = int(os.getenv('IPERF_SERVER_PORT', 5201))
    protocol = os.getenv('IPERF_TEST_PROTOCOL', 'tcp').lower()
    source_node_name = os.getenv('SOURCE_NODE_NAME')

    # Support either IPERF_TEST_DURATION or IPERF_TEST_TIMEOUT from Helm values
    duration = int(os.getenv('IPERF_TEST_DURATION', os.getenv('IPERF_TEST_TIMEOUT', 5)))
    connect_timeout_ms = int(os.getenv('IPERF_CONNECT_TIMEOUT_MS', 3000))
    stall_threshold_sec = int(os.getenv('IPERF_STALL_THRESHOLD_SECONDS', 5))

    if not source_node_name:
        logging.error("CRITICAL: SOURCE_NODE_NAME environment variable not set. This is required. Exiting.")
        sys.exit(1)

    logging.info(
        f"Exporter configured. Source Node: {source_node_name}, "
        f"Test Interval: {test_interval}s, Duration: {duration}s, Server Port: {server_port}, Protocol: {protocol.upper()}, "
        f"Connect Timeout: {connect_timeout_ms}ms, Stall Threshold: {stall_threshold_sec}s"
    )

    while not shutdown_requested:
        logging.info("Starting new iperf test cycle...")
        servers = discover_iperf_servers(source_node_name)

        if not servers:
            logging.warning("No iperf servers discovered in this cycle. Check K8s setup and RBAC permissions.")
        else:
            for server in servers:
                if shutdown_requested:
                    break

                dest_node_name = server.get('node_name', 'unknown_destination_node')
                server_ip = server.get('ip')

                if not server_ip:
                    logging.warning(f"Discovered server entry missing an IP: {server}. Skipping.")
                    continue

                if dest_node_name == source_node_name:
                    logging.info(f"Skipping test to self: {source_node_name} to {server_ip} (on same node: {dest_node_name}).")
                    continue

                run_iperf_test(
                    server_ip=server_ip,
                    server_port=server_port,
                    protocol=protocol,
                    source_node_name=source_node_name,
                    dest_node_name=dest_node_name,
                    duration=duration,
                    connect_timeout_ms=connect_timeout_ms,
                    stall_threshold_sec=stall_threshold_sec,
                )

        if not shutdown_requested:
            IPERF_LAST_TEST_TIMESTAMP_SECONDS.labels(source_node=source_node_name).set(time.time())
            logging.info(f"Test cycle completed. Sleeping for {test_interval} seconds.")
            slept = 0
            while slept < test_interval and not shutdown_requested:
                time.sleep(1)
                slept += 1

    logging.info("Exporter main loop stopped gracefully.")

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    listen_port = int(os.getenv('LISTEN_PORT', 9876))

    try:
        start_http_server(listen_port)
        logging.info(f"Prometheus exporter listening on port {listen_port}")
    except Exception as e:
        logging.error(f"Failed to start Prometheus HTTP server on port {listen_port}: {e}")
        sys.exit(1)

    main_loop()
