"""
Prometheus exporter for iperf3 network performance monitoring.

This script runs iperf3 tests between the node it's running on (source) and
other iperf3 server pods discovered in a Kubernetes cluster. It then exposes
these metrics for Prometheus consumption.

Configuration is primarily through environment variables and command-line arguments
for log level.
"""
import os
import time
import logging
import argparse
import sys
from kubernetes import client, config
from prometheus_client import start_http_server, Gauge
import iperf3

# --- Global Configuration & Setup ---

# Argument parsing for log level configuration
# The command-line --log-level argument takes precedence over the LOG_LEVEL env var.
# Defaults to INFO if neither is set.
parser = argparse.ArgumentParser(description="iperf3 Prometheus exporter.")
parser.add_argument(
    '--log-level',
    default=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
    help='Set the logging level. Overrides LOG_LEVEL environment variable. (Default: INFO)'
)
args = parser.parse_args()
log_level_str = args.log_level

# Convert log level string (e.g., 'INFO') to its numeric representation (e.g., logging.INFO)
numeric_level = getattr(logging, log_level_str.upper(), None)
if not isinstance(numeric_level, int):
    # This case should ideally not be reached if choices in argparse are respected.
    logging.error(f"Invalid log level: {log_level_str}. Defaulting to INFO.")
    numeric_level = logging.INFO
logging.basicConfig(level=numeric_level, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Prometheus Metrics Definition ---
# These gauges will be used to expose iperf3 test results.
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

def discover_iperf_servers():
    """
    Discovers iperf3 server pods within a Kubernetes cluster.

    It uses the in-cluster Kubernetes configuration to connect to the API.
    The target namespace and label selector for iperf3 server pods are configured
    via environment variables:
    - IPERF_SERVER_NAMESPACE (default: 'default')
    - IPERF_SERVER_LABEL_SELECTOR (default: 'app=iperf3-server')

    Returns:
        list: A list of dictionaries, where each dictionary contains the 'ip'
              and 'node_name' of a discovered iperf3 server pod. Returns an
              empty list if discovery fails or no servers are found.
    """
    try:
        config.load_incluster_config() # Assumes running inside a Kubernetes pod
        v1 = client.CoreV1Api()

        namespace = os.getenv('IPERF_SERVER_NAMESPACE', 'default')
        label_selector = os.getenv('IPERF_SERVER_LABEL_SELECTOR', 'app=iperf3-server')

        logging.info(f"Discovering iperf3 servers with label '{label_selector}' in namespace '{namespace}'")

        # Use list_namespaced_pod to query only the specified namespace
        ret = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector, watch=False)

        servers = []
        for item in ret.items:
            # No need to filter by namespace here as the API call is already namespaced
            if item.status.pod_ip and item.status.phase == 'Running':
                servers.append({
                    'ip': item.status.pod_ip,
                    'node_name': item.spec.node_name # Node where the iperf server pod is running
                })
        logging.info(f"Discovered {len(servers)} iperf3 server pods in namespace '{namespace}'.")
        return servers
    except config.ConfigException as e:
        logging.error(f"Kubernetes config error: {e}. Is the exporter running in a cluster with RBAC permissions?")
        return []
    except Exception as e:
        logging.error(f"Error discovering iperf servers: {e}")
        return [] # Return empty list on error to avoid crashing the main loop

def run_iperf_test(server_ip, server_port, protocol, source_node_name, dest_node_name):
    """
    Runs a single iperf3 test against a specified server and publishes metrics.

    Args:
        server_ip (str): The IP address of the iperf3 server.
        server_port (int): The port number of the iperf3 server.
        protocol (str): The protocol to use ('tcp' or 'udp').
        source_node_name (str): The name of the source node (where this exporter is running).
        dest_node_name (str): The name of the destination node (where the server is running).

    The test duration is controlled by the IPERF_TEST_DURATION environment variable
    (default: 5 seconds).
    """
    logging.info(f"Running iperf3 {protocol.upper()} test from {source_node_name} to {dest_node_name} ({server_ip}:{server_port})")

    iperf_client = iperf3.Client()
    iperf_client.server_hostname = server_ip
    iperf_client.port = server_port
    iperf_client.protocol = protocol
    iperf_client.duration = int(os.getenv('IPERF_TEST_DURATION', 5)) # Test duration in seconds
    iperf_client.json_output = True # Enables easy parsing of results

    try:
        result = iperf_client.run()
        parse_and_publish_metrics(result, source_node_name, dest_node_name, protocol)
    except Exception as e:
        # Catch unexpected errors during client.run() or parsing
        logging.error(f"Exception during iperf3 test or metric parsing for {dest_node_name}: {e}")
        labels = {'source_node': source_node_name, 'destination_node': dest_node_name, 'protocol': protocol}
        IPERF_TEST_SUCCESS.labels(**labels).set(0)
        try:
            IPERF_BANDWIDTH_MBPS.labels(**labels).set(0)
            IPERF_JITTER_MS.labels(**labels).set(0)
            IPERF_PACKETS_TOTAL.labels(**labels).set(0)
            IPERF_LOST_PACKETS.labels(**labels).set(0)
        except KeyError:
            logging.debug(f"KeyError setting failure metrics for {labels} after client.run() exception.")


def parse_and_publish_metrics(result, source_node, dest_node, protocol):
    """
    Parses the iperf3 test result and updates Prometheus gauges.

    Args:
        result (iperf3.TestResult): The result object from the iperf3 client.
        source_node (str): Name of the source node.
        dest_node (str): Name of the destination node.
        protocol (str): Protocol used for the test ('tcp' or 'udp').
    """
    labels = {'source_node': source_node, 'destination_node': dest_node, 'protocol': protocol}

    # Handle failed tests (e.g., server unreachable) or missing result object
    if not result or result.error:
        error_message = result.error if result and result.error else "No result object from iperf3 client"
        logging.warning(f"Test from {source_node} to {dest_node} ({protocol.upper()}) failed: {error_message}")
        IPERF_TEST_SUCCESS.labels(**labels).set(0)
        # Set all relevant metrics to 0 on failure to clear stale values from previous successes
        try:
            IPERF_BANDWIDTH_MBPS.labels(**labels).set(0)
            IPERF_JITTER_MS.labels(**labels).set(0) # Applicable for UDP, zeroed for TCP later
            IPERF_PACKETS_TOTAL.labels(**labels).set(0) # Applicable for UDP, zeroed for TCP later
            IPERF_LOST_PACKETS.labels(**labels).set(0) # Applicable for UDP, zeroed for TCP later
        except KeyError:
            # This can happen if labels were never registered due to continuous failures
            logging.debug(f"KeyError when setting failure metrics for {labels}. Gauges might not be initialized.")
        return

    # If we reach here, the test itself was successful in execution
    IPERF_TEST_SUCCESS.labels(**labels).set(1)

    # Determine bandwidth:
    # Order of preference: received_Mbps, sent_Mbps, Mbps, then JSON fallbacks.
    # received_Mbps is often most relevant for TCP client perspective.
    # sent_Mbps can be relevant for UDP or as a TCP fallback.
    bandwidth_mbps = 0
    if hasattr(result, 'received_Mbps') and result.received_Mbps is not None:
        bandwidth_mbps = result.received_Mbps
    elif hasattr(result, 'sent_Mbps') and result.sent_Mbps is not None:
        bandwidth_mbps = result.sent_Mbps
    elif hasattr(result, 'Mbps') and result.Mbps is not None: # General attribute from iperf3 library
        bandwidth_mbps = result.Mbps
    # Fallback to raw JSON if direct attributes are None or missing
    elif result.json:
        # Prefer received sum, then sent sum from the JSON output's 'end' summary
        if 'end' in result.json and 'sum_received' in result.json['end'] and \
           result.json['end']['sum_received'].get('bits_per_second') is not None:
            bandwidth_mbps = result.json['end']['sum_received']['bits_per_second'] / 1000000.0
        elif 'end' in result.json and 'sum_sent' in result.json['end'] and \
             result.json['end']['sum_sent'].get('bits_per_second') is not None:
            bandwidth_mbps = result.json['end']['sum_sent']['bits_per_second'] / 1000000.0

    IPERF_BANDWIDTH_MBPS.labels(**labels).set(bandwidth_mbps)

    # UDP specific metrics
    if protocol == 'udp':
        # These attributes are specific to UDP tests in iperf3
        IPERF_JITTER_MS.labels(**labels).set(getattr(result, 'jitter_ms', 0) if result.jitter_ms is not None else 0)
        IPERF_PACKETS_TOTAL.labels(**labels).set(getattr(result, 'packets', 0) if result.packets is not None else 0)
        IPERF_LOST_PACKETS.labels(**labels).set(getattr(result, 'lost_packets', 0) if result.lost_packets is not None else 0)
    else:
        # For TCP tests, ensure UDP-specific metrics are set to 0
        try:
             IPERF_JITTER_MS.labels(**labels).set(0)
             IPERF_PACKETS_TOTAL.labels(**labels).set(0)
             IPERF_LOST_PACKETS.labels(**labels).set(0)
        except KeyError:
            # Can occur if labels not yet registered (e.g. first test is TCP)
            logging.debug(f"KeyError for {labels} when zeroing UDP metrics for TCP test.")
            pass

def main_loop():
    """
    Main operational loop of the iperf3 exporter.

    This loop periodically:
    1. Fetches configuration from environment variables:
       - IPERF_TEST_INTERVAL (default: 300s): Time between test cycles.
       - IPERF_SERVER_PORT (default: 5201): Port for iperf3 servers.
       - IPERF_TEST_PROTOCOL (default: 'tcp'): 'tcp' or 'udp'.
       - SOURCE_NODE_NAME (critical): Name of the node this exporter runs on.
    2. Discovers iperf3 server pods in the Kubernetes cluster.
    3. Runs iperf3 tests against each discovered server (unless it's on the same node).
    4. Sleeps for the configured test interval.

    If SOURCE_NODE_NAME is not set, the script will log an error and exit.
    """
    # Fetch operational configuration from environment variables
    test_interval = int(os.getenv('IPERF_TEST_INTERVAL', 300))
    server_port = int(os.getenv('IPERF_SERVER_PORT', 5201))
    protocol = os.getenv('IPERF_TEST_PROTOCOL', 'tcp').lower() # Ensure lowercase
    source_node_name = os.getenv('SOURCE_NODE_NAME')

    # SOURCE_NODE_NAME is crucial for labeling metrics correctly.
    if not source_node_name:
        logging.error("CRITICAL: SOURCE_NODE_NAME environment variable not set. This is required. Exiting.")
        sys.exit(1)

    logging.info(
        f"Exporter configured. Source Node: {source_node_name}, "
        f"Test Interval: {test_interval}s, Server Port: {server_port}, Protocol: {protocol.upper()}"
    )

    while True:
        logging.info("Starting new iperf test cycle...")
        servers = discover_iperf_servers()

        if not servers:
            logging.warning("No iperf servers discovered in this cycle. Check K8s setup and RBAC permissions.")
        else:
            for server in servers:
                dest_node_name = server.get('node_name', 'unknown_destination_node') # Default if key missing
                server_ip = server.get('ip')

                if not server_ip:
                    logging.warning(f"Discovered server entry missing an IP: {server}. Skipping.")
                    continue

                # Avoid testing a node against itself
                if dest_node_name == source_node_name:
                    logging.info(f"Skipping test to self: {source_node_name} to {server_ip} (on same node: {dest_node_name}).")
                    continue

                run_iperf_test(server_ip, server_port, protocol, source_node_name, dest_node_name)

        logging.info(f"Test cycle completed. Sleeping for {test_interval} seconds.")
        time.sleep(test_interval)

if __name__ == '__main__':
    # Initial logging (like log level) is configured globally at the start of the script.

    # Fetch Prometheus exporter listen port from environment variable
    listen_port = int(os.getenv('LISTEN_PORT', 9876))

    try:
        # Start the Prometheus HTTP server to expose metrics.
        start_http_server(listen_port)
        logging.info(f"Prometheus exporter listening on port {listen_port}")
    except Exception as e:
        logging.error(f"Failed to start Prometheus HTTP server on port {listen_port}: {e}")
        sys.exit(1) # Exit if the metrics server cannot start

    # Enter the main operational loop.
    # main_loop() contains its own critical checks (e.g., SOURCE_NODE_NAME) and will exit if necessary.
    main_loop()
