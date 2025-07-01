# Kubernetes-Native Network Performance Monitoring Service

This project provides a comprehensive solution for continuous network validation within a Kubernetes cluster. Leveraging industry-standard tools like `iperf3`, `Prometheus`, and `Grafana`, it offers proactive monitoring of network performance between nodes, helping to identify and troubleshoot latency, bandwidth, and packet loss issues before they impact applications.

## Features

*   **Continuous N-to-N Testing:** Automatically measures network performance between all nodes in the cluster.
*   **Kubernetes-Native:** Deploys as standard Kubernetes workloads (DaemonSet, Deployment).
*   **Dynamic Discovery:** Exporter automatically discovers iperf3 server pods using the Kubernetes API.
*   **Prometheus Integration:** Translates iperf3 results into standard Prometheus metrics for time-series storage.
*   **Grafana Visualization:** Provides a rich, interactive dashboard with heatmaps and time-series graphs.
*   **Helm Packaging:** Packaged as a Helm chart for easy deployment and configuration management.
*   **Automated CI/CD:** Includes a GitHub Actions workflow for building and publishing the exporter image and Helm chart.

## Architecture

The service is based on a decoupled architecture:

1.  **iperf3-server DaemonSet:** Deploys an `iperf3` server pod on every node to act as a test endpoint. Running on the host network to measure raw node performance.
2.  **iperf3-exporter Deployment:** A centralized service that uses the Kubernetes API to discover server pods, orchestrates `iperf3` client tests against them, parses the JSON output, and exposes performance metrics via an HTTP endpoint.
3.  **Prometheus & Grafana Stack:** A standard monitoring backend (like `kube-prometheus-stack`) that scrapes the exporter's metrics and visualizes them in a custom dashboard.

This separation of concerns ensures scalability, resilience, and aligns with Kubernetes operational principles.

## Getting Started

### Prerequisites

*   A running Kubernetes cluster.
*   `kubectl` configured to connect to your cluster.
*   Helm v3+ installed.
*   A Prometheus instance configured to scrape services (ideally using the Prometheus Operator and ServiceMonitors).
*   A Grafana instance accessible and configured with Prometheus as a data source.

### Installation with Helm

1.  Add the Helm chart repository (replace with your actual repo URL once published):

    ```/dev/null/helm-install.sh#L1-1
    helm repo add iperf3-monitor https://malarinv.github.io/iperf3-monitor/
    ```

2.  Update your Helm repositories:

    ```/dev/null/helm-install.sh#L3-3
    helm repo update
    ```

3.  Install the chart:

    ```/dev/null/helm-install.sh#L5-8
    helm install iperf3-monitor iperf3-monitor/iperf3-monitor \
      --namespace monitoring # Or your preferred namespace \
      --create-namespace \
      --values values.yaml # Optional: Use a custom values file
    ```

    > **Note:** Ensure your Prometheus instance is configured to scrape services in the namespace where you install the chart and that it recognizes `ServiceMonitor` resources with the label `release: prometheus-operator` (if using the standard `kube-prometheus-stack` setup).

### Configuration

The Helm chart is highly configurable via the `values.yaml` file. You can override default settings by creating your own `values.yaml` and passing it during installation (`--values my-values.yaml`).

Refer to the comments in the default `values.yaml` for a detailed explanation of each parameter:

```iperf3-monitor/charts/iperf3-monitor/values.yaml
# Default values for iperf3-monitor.
# This is a YAML-formatted file.
# Declare variables to be passed into your templates.

# -- Override the name of the chart.
nameOverride: ""

# -- Override the fully qualified app name.
fullnameOverride: ""

# Exporter Configuration (`controllers.exporter`)
# The iperf3 exporter is managed under the `controllers.exporter` section,
# leveraging the `bjw-s/common-library` for robust workload management.
controllers:
  exporter:
    # -- Enable the exporter controller.
    enabled: true
    # -- Set the controller type for the exporter.
    # Valid options are "deployment" or "daemonset".
    # Use "daemonset" for N-to-N node monitoring where an exporter runs on each node (or selected nodes).
    # Use "deployment" for a centralized exporter (typically with replicaCount: 1).
    # @default -- "deployment"
    type: deployment
    # -- Number of desired exporter pods. Only used if type is "deployment".
    # @default -- 1
    replicas: 1

    # -- Application-specific configuration for the iperf3 exporter.
    # These values are used to populate environment variables for the exporter container.
    appConfig:
      # -- Interval in seconds between complete test cycles (i.e., testing all server nodes).
      testInterval: 300
      # -- Log level for the iperf3 exporter (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL).
      logLevel: INFO
      # -- Timeout in seconds for a single iperf3 test run.
      testTimeout: 10
      # -- Protocol to use for testing (tcp or udp).
      testProtocol: tcp
      # -- iperf3 server port to connect to. Should match the server's listening port.
      serverPort: "5201"
      # -- Label selector to find iperf3 server pods.
      # This is templated. Default: 'app.kubernetes.io/name=<chart-name>,app.kubernetes.io/instance=<release-name>,app.kubernetes.io/component=server'
      serverLabelSelector: 'app.kubernetes.io/name={{ include "iperf3-monitor.name" . }},app.kubernetes.io/instance={{ .Release.Name }},app.kubernetes.io/component=server'

    # -- Pod-level configurations for the exporter.
    pod:
      # -- Annotations for the exporter pod.
      annotations: {}
      # -- Labels for the exporter pod (the common library adds its own defaults too).
      labels: {}
      # -- Node selector for scheduling exporter pods. Useful for DaemonSet or specific scheduling with Deployments.
      # Example:
      # nodeSelector:
      #   kubernetes.io/os: linux
      nodeSelector: {}
      # -- Tolerations for scheduling exporter pods.
      # Example:
      # tolerations:
      # - key: "node-role.kubernetes.io/control-plane"
      #   operator: "Exists"
      #   effect: "NoSchedule"
      tolerations: []
      # -- Affinity rules for scheduling exporter pods.
      # Example:
      # affinity:
      #   nodeAffinity:
      #     requiredDuringSchedulingIgnoredDuringExecution:
      #       nodeSelectorTerms:
      #       - matchExpressions:
      #         - key: "kubernetes.io/arch"
      #           operator: In
      #           values:
      #           - amd64
      affinity: {}
      # -- Security context for the exporter pod.
      # securityContext:
      #   fsGroup: 65534
      #   runAsUser: 65534
      #   runAsGroup: 65534
      #   runAsNonRoot: true
      securityContext: {}
      # -- Automount service account token for the pod.
      automountServiceAccountToken: true

    # -- Container-level configurations for the main exporter container.
    containers:
      exporter: # Name of the primary container
        image:
          repository: ghcr.io/malarinv/iperf3-monitor
          tag: "" # Defaults to .Chart.AppVersion
          pullPolicy: IfNotPresent
        # -- Custom environment variables for the exporter container.
        # These are merged with the ones generated from appConfig.
        # env:
        #   MY_CUSTOM_VAR: "my_value"
        env: {}
        # -- Ports for the exporter container.
        ports:
          metrics: # Name of the port
            port: 9876 # Container port for metrics
            protocol: TCP
            enabled: true
        # -- CPU and memory resource requests and limits.
        # resources:
        #   requests:
        #     cpu: "100m"
        #     memory: "128Mi"
        #   limits:
        #     cpu: "500m"
        #     memory: "256Mi"
        resources: {}
        # -- Probes configuration for the exporter container.
        # probes:
        #   liveness:
        #     enabled: true # Example: enable liveness probe
        #     spec: # Customize probe spec if needed
        #       initialDelaySeconds: 30
        #       periodSeconds: 15
        #       timeoutSeconds: 5
        #       failureThreshold: 3
        probes:
          liveness:
            enabled: false
          readiness:
            enabled: false
          startup:
            enabled: false

server:
  # -- Configuration for the iperf3 server container image (DaemonSet).
  image:
    # -- The container image repository for the iperf3 server.
    repository: networkstatic/iperf3
    # -- The container image tag for the iperf3 server.
    tag: latest

  # -- CPU and memory resource requests and limits for the iperf3 server pods (DaemonSet).
  # These should be very low as the server is mostly idle.
  # @default -- A small default is provided if commented out.
  resources: {}
    # requests:
    #   cpu: "50m"
    #   memory: "64Mi"
    # limits:
    #   cpu: "100m"
    #   memory: "128Mi"

  # -- Node selector for scheduling iperf3 server pods.
  # Use this to restrict the DaemonSet to a subset of nodes.
  # @default -- {} (schedule on all nodes)
  nodeSelector: {}

  # -- Tolerations for scheduling iperf3 server pods on tainted nodes (e.g., control-plane nodes).
  # This is often necessary to include master nodes in the test mesh.
  # @default -- Tolerates control-plane and master taints.
  tolerations:
    - key: "node-role.kubernetes.io/control-plane"
      operator: "Exists"
      effect: "NoSchedule"
    - key: "node-role.kubernetes.io/master"
      operator: "Exists"
      effect: "NoSchedule"

rbac:
  # -- If true, create ServiceAccount, ClusterRole, and ClusterRoleBinding for the exporter.
  # Set to false if you manage RBAC externally.
  create: true

serviceAccount:
  # -- The name of the ServiceAccount to use for the exporter pod.
  # Only used if rbac.create is false. If not set, it defaults to the chart's fullname.
  name: ""

serviceMonitor:
  # -- If true, create a ServiceMonitor resource for integration with Prometheus Operator.
  # Requires a running Prometheus Operator in the cluster.
  enabled: true

  # -- Scrape interval for the ServiceMonitor. How often Prometheus scrapes the exporter metrics.
  interval: 60s

  # -- Scrape timeout for the ServiceMonitor. How long Prometheus waits for metrics response.
  scrapeTimeout: 30s

# -- Configuration for the exporter Service.
service:
  # -- Service type. ClusterIP is typically sufficient.
  type: ClusterIP
  # -- Port on which the exporter service is exposed.
  port: 9876
  # -- Target port on the exporter pod.
  targetPort: 9876

# -- Optional configuration for a network policy to allow traffic to the iperf3 server DaemonSet.
# This is often necessary if you are using a network policy controller.
networkPolicy:
  # -- If true, create a NetworkPolicy resource.
  enabled: false
  # -- Specify source selectors if needed (e.g., pods in a specific namespace).
  from: []
  # -- Specify namespace selectors if needed.
  namespaceSelector: {}
  # -- Specify pod selectors if needed.
  podSelector: {}
```

## Grafana Dashboard

A custom Grafana dashboard is provided to visualize the collected `iperf3` metrics.

1.  Log in to your Grafana instance.
2.  Navigate to `Dashboards` -> `Import`.
3.  Paste the full JSON model provided below into the text area and click `Load`.
4.  Select your Prometheus data source and click `Import`.

```/dev/null/grafana-dashboard.json
{
"__inputs": [],
"__requires": [
{
"type": "grafana",
"id": "grafana",
"name": "Grafana",
"version": "8.0.0"
},
{
"type": "datasource",
"id": "prometheus",
"name": "Prometheus",
"version": "1.0.0"
}
],
"annotations": {
"list": [
{
"builtIn": 1,
"datasource": {
"type": "grafana",
"uid": "-- Grafana --"
},
"enable": true,
"hide": true,
"iconColor": "rgba(0, 211, 255, 1)",
"name": "Annotations & Alerts",
"type": "dashboard"
}
]
},
"editable": true,
"fiscalYearStartMonth": 0,
"gnetId": null,
"graphTooltip": 0,
"id": null,
"links": [],
"panels": [
{
"datasource": {
"type": "prometheus",
"uid": "prometheus"
},
"gridPos": {
"h": 9,
"w": 24,
"x": 0,
"y": 0
},
"id": 2,
"targets": [
{
"expr": "avg(iperf_network_bandwidth_mbps) by (source_node, destination_node)",
"format": "heatmap",
"legendFormat": "{{source_node}} -> {{destination_node}}",
"refId": "A"
}
],
"cards": { "cardPadding": null, "cardRound": null },
"color": {
"mode": "spectrum",
"scheme": "red-yellow-green",
"exponent": 0.5,
"reverse": false
},
"dataFormat": "tsbuckets",
"yAxis": { "show": true, "format": "short" },
"xAxis": { "show": true }
},
{
"title": "Bandwidth Over Time (Source: $source_node, Dest: $destination_node)",
"type": "timeseries",
"datasource": {
"type": "prometheus",
"uid": "prometheus"
},
"gridPos": {
"h": 8,
"w": 12,
"x": 0,
"y": 9
},
"targets": [
{
"expr": "iperf_network_bandwidth_mbps{source_node=~\"^$source_node$\", destination_node=~\"^$destination_node$\", protocol=~\"^$protocol$\"}",
"legendFormat": "Bandwidth",
"refId": "A"
}
],
"fieldConfig": {
"defaults": {
"unit": "mbps"
}
}
},
{
"title": "Jitter Over Time (Source: $source_node, Dest: $destination_node)",
"type": "timeseries",
"datasource": {
"type": "prometheus",
"uid": "prometheus"
},
"gridPos": {
"h": 8,
"w": 12,
"x": 12,
"y": 9
},
"targets": [
{
"expr": "iperf_network_jitter_ms{source_node=~\"^$source_node$\", destination_node=~\"^$destination_node$\", protocol=\"udp\"}",
"legendFormat": "Jitter",
"refId": "A"
}
],
"fieldConfig": {
"defaults": {
"unit": "ms"
}
}
}
],
"refresh": "30s",
"schemaVersion": 36,
"style": "dark",
"tags": ["iperf3", "network", "kubernetes"],
"templating": {
"list": [
{
"current": {},
"datasource": {
"type": "prometheus",
"uid": "prometheus"
},
"definition": "label_values(iperf_network_bandwidth_mbps, source_node)",
"hide": 0,
"includeAll": false,
"multi": false,
"name": "source_node",
"options": [],
"query": "label_values(iperf_network_bandwidth_mbps, source_node)",
"refresh": 1,
"regex": "",
"skipUrlSync": false,
"sort": 1,
"type": "query"
},
{
"current": {},
"datasource": {
"type": "prometheus",
"uid": "prometheus"
},
"definition": "label_values(iperf_network_bandwidth_mbps{source_node=~\"^$source_node$\"}, destination_node)",
"hide": 0,
"includeAll": false,
"multi": false,
"name": "destination_node",
"options": [],
"query": "label_values(iperf_network_bandwidth_mbps{source_node=~\"^$source_node$\"}, destination_node)",
"refresh": 1,
"regex": "",
"skipUrlSync": false,
"sort": 1,
"type": "query"
},
{
"current": { "selected": true, "text": "tcp", "value": "tcp" },
"hide": 0,
"includeAll": false,
"multi": false,
"name": "protocol",
"options": [
{ "selected": true, "text": "tcp", "value": "tcp" },
{ "selected": false, "text": "udp", "value": "udp" }
],
"query": "tcp,udp",
"skipUrlSync": false,
"type": "custom"
}
]
},
"time": {
"from": "now-1h",
"to": "now"
},
"timepicker": {},
"timezone": "browser",
"title": "Kubernetes iperf3 Network Performance",
"uid": "k8s-iperf3-dashboard",
"version": 1,
"weekStart": ""
}
```

## Repository Structure

The project follows a standard structure:

```/dev/null/repo-structure.txt
.
├── .github/
│   └── workflows/
│       └── release.yml    # GitHub Actions workflow for CI/CD
├── charts/
│   └── iperf3-monitor/    # The Helm chart for the service
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── _helpers.tpl
│           ├── server-daemonset.yaml
│           ├── exporter-deployment.yaml
│           ├── rbac.yaml
│           ├── service.yaml
│           └── servicemonitor.yaml
└── exporter/
    ├── Dockerfile         # Dockerfile for the exporter
    ├── requirements.txt   # Python dependencies
    └── exporter.py        # Exporter source code
├── .gitignore             # Specifies intentionally untracked files
├── LICENSE                # Project license
└── README.md              # This file
```

## Development and CI/CD

The project includes a GitHub Actions workflow (`.github/workflows/release.yml`) triggered on Git tags (`v*.*.*`) to automate:

1.  Linting the Helm chart.
2.  Building and publishing the Docker image for the exporter to GitHub Container Registry (`ghcr.io`).
3.  Updating the Helm chart version based on the Git tag.
4.  Packaging and publishing the Helm chart to GitHub Pages.

## License

This project is licensed under the GNU Affero General Public License v3. See the `LICENSE` file for details.
