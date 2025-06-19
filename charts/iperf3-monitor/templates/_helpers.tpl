{{/*
Expand the name of the chart.
*/}}
{{- define "iperf3-monitor.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "iperf3-monitor.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create chart's labels
*/}}
{{- define "iperf3-monitor.labels" -}}
helm.sh/chart: {{ include "iperf3-monitor.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "iperf3-monitor.selectorLabels" . }}
{{ if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{ end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "iperf3-monitor.selectorLabels" -}}
app.kubernetes.io/name: {{ include "iperf3-monitor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{ end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "iperf3-monitor.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
    {{- default (include "iperf3-monitor.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
    {{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}