{{- define "docuchat.name" -}}{{ .Chart.Name }}{{- end }}

{{- define "docuchat.fullname" -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end }}

{{- define "docuchat.labels" -}}
app.kubernetes.io/name: {{ include "docuchat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{- define "docuchat.apiSelector" -}}
app.kubernetes.io/name: {{ include "docuchat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
{{- end }}

{{- define "docuchat.qdrantSelector" -}}
app.kubernetes.io/name: {{ include "docuchat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: qdrant
{{- end }}

{{- define "docuchat.restrictedSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end }}
