{{- define "demo.image" -}}
{{ .root.Values.image.registry }}traceforge/{{ .name }}:{{ .root.Values.image.tag }}
{{- end -}}
