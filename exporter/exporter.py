import os
import time
import logging
from kubernetes import client, config
from prometheus_client import start_http_server, Gauge
import iperf3

# --- Configuration ---
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Prometheus Metrics Definition ---
IPERF_BANDWIDTH_MBPS = Gauge(
    'iperf_network_bandwidth_mbps',
    'Network bandwidth measured by iperf3 in Megabits per second',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_JITTER_MS = Gauge(
    'iperf_network_jitter_ms',
    'Network jitter measured by iperf3 in milliseconds',
    ['source_node', 'destination_node', 'protocol']
)
IPERF_PACKETS_TOTAL = Gauge(
    'iperf_network_packets_total',
    'Total packets transmitted or received during the iperf3 test',
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
    Discover iperf3 server pods in the cluster using the Kubernetes API.
    """
    try:
        # Load in-cluster configuration
        # Assumes the exporter runs in a pod with a service account having permissions
        config.load_incluster_config()
        v1 = client.CoreV1Api()

        namespace = os.getenv('IPERF_SERVER_NAMESPACE', 'default')
        label_selector = os.getenv('IPERF_SERVER_LABEL_SELECTOR', 'app=iperf3-server')

        logging.info(f"Discovering iperf3 servers with label '{label_selector}' in namespace '{namespace}'")

        # List pods across all namespaces with the specified label selector
        # Note: list_pod_for_all_namespaces requires cluster-wide permissions
        ret = v1.list_pod_for_all_namespaces(label_selector=label_selector, watch=False)

        servers = []
        for i in ret.items:
            # Ensure pod has an IP and is running
            if i.status.pod_ip and i.status.phase == 'Running':
                servers.append({
                    'ip': i.status.pod_ip,
                    'node_name': i.spec.node_name
                })
        logging.info(f"Discovered {len(servers)} iperf3 server pods.")
        return servers
    except Exception as e:
        logging.error(f"Error discovering iperf servers: {e}")
        return [] # Return empty list on error to avoid crashing the loop

def run_iperf_test(server_ip, server_port, protocol, source_node, dest_node):
    """
    Runs a single iperf3 test and updates Prometheus metrics.
    """
    logging.info(f"Running iperf3 test from {source_node} to {dest_node} ({server_ip}:{server_port}) using {protocol.upper()}")

    client = iperf3.Client()
    client.server_hostname = server_ip
    client.port = server_port
    client.protocol = protocol
    # Duration of the test (seconds)
    client.duration = int(os.getenv('IPERF_TEST_DURATION', 5))
    # Output results as JSON for easy parsing
    client.json_output = True

    result = client.run()

    # Parse results and update metrics
    parse_and_publish_metrics(result, source_node, dest_node, protocol)

def parse_and_publish_metrics(result, source_node, dest_node, protocol):
    """
    Parses the iperf3 result and updates Prometheus gauges.
    Handles both successful and failed tests.
    """
    labels = {'source_node': source_node, 'destination_node': dest_node, 'protocol': protocol}

    if result and result.error:
        logging.error(f"Test from {source_node} to {dest_node} failed: {result.error}")
        IPERF_TEST_SUCCESS.labels(**labels).set(0)
        # Set metrics to 0 on failure
        try:
            IPERF_BANDWIDTH_MBPS.labels(**labels).set(0)
            IPERF_JITTER_MS.labels(**labels).set(0)
            IPERF_PACKETS_TOTAL.labels(**labels).set(0)
            IPERF_LOST_PACKETS.labels(**labels).set(0)
        except KeyError:
             # Labels might not be registered yet if this is the first failure
             pass
        return

    if not result:
         logging.error(f"Test from {source_node} to {dest_node} failed to return a result object.")
         IPERF_TEST_SUCCESS.labels(**labels).set(0)
         try:
            IPERF_BANDWIDTH_MBPS.labels(**labels).set(0)
            IPERF_JITTER_MS.labels(**labels).set(0)
            IPERF_PACKETS_TOTAL.labels(**labels).set(0)
            IPERF_LOST_PACKETS.labels(**labels).set(0)
         except KeyError:
             pass
         return


    IPERF_TEST_SUCCESS.labels(**labels).set(1)

    # The summary data is typically in result.json['end']['sum_sent'] or result.json['end']['sum_received']
    # The iperf3-python client often exposes this directly as attributes like sent_Mbps or received_Mbps
    # For TCP, we usually care about the received bandwidth on the client side (which is the exporter)
    # For UDP, the client report contains jitter, lost packets, etc.
    bandwidth_mbps = 0
    if hasattr(result, 'received_Mbps') and result.received_Mbps is not None:
        bandwidth_mbps = result.received_Mbps
    elif hasattr(result, 'sent_Mbps') and result.sent_Mbps is not None:
        # Fallback, though received_Mbps is usually more relevant for TCP client
        bandwidth_mbps = result.sent_Mbps
    # Add a check for the raw JSON output structure as a fallback
    elif result.json and 'end' in result.json and 'sum_received' in result.json['end'] and result.json['end']['sum_received']['bits_per_second'] is not None:
         bandwidth_mbps = result.json['end']['sum_received']['bits_per_second'] / 1000000
    elif result.json and 'end' in result.json and 'sum_sent' in result.json['end'] and result.json['end']['sum_sent']['bits_per_second'] is not None:
         bandwidth_mbps = result.json['end']['sum_sent']['bits_per_second'] / 1000000


    IPERF_BANDWIDTH_MBPS.labels(**labels).set(bandwidth_mbps)

    # UDP specific metrics
    if protocol == 'udp':
        # iperf3-python exposes UDP results directly
        IPERF_JITTER_MS.labels(**labels).set(result.jitter_ms if hasattr(result, 'jitter_ms') and result.jitter_ms is not None else 0)
        IPERF_PACKETS_TOTAL.labels(**labels).set(result.packets if hasattr(result, 'packets') and result.packets is not None else 0)
        IPERF_LOST_PACKETS.labels(**labels).set(result.lost_packets if hasattr(result, 'lost_packets') and result.lost_packets is not None else 0)
    else:
        # Ensure UDP metrics are zeroed or absent for TCP tests
        try:
             IPERF_JITTER_MS.labels(**labels).set(0)
             IPERF_PACKETS_TOTAL.labels(**labels).set(0)
             IPERF_LOST_PACKETS.labels(**labels).set(0)
        except KeyError:
            pass
