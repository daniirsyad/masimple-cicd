import yaml


class _NoAliasDumper(yaml.Dumper):
    """PyYAML's default Dumper emits YAML anchors/aliases (&id001/*id001)
    whenever the exact same dict/list *object* appears more than once in
    the data being dumped — e.g. deployment.build() deliberately reuses one
    `labels` dict across metadata.labels, spec.selector.matchLabels, and
    the pod template's own metadata.labels. That's valid YAML and kubectl
    would accept it, but it's not what a human hand-writing this manifest
    would produce and would be a confusing surprise to paste elsewhere —
    disabling aliasing here instead duplicates the content at each spot,
    matching ordinary hand-written Kubernetes YAML.
    """

    def ignore_aliases(self, data):
        return True


def to_yaml(resource: dict) -> str:
    """The one place YAML serialization happens for this feature — real
    `yaml.dump`, not the dict->json.dumps() shortcut the deploy-time code
    uses elsewhere (see resolver.py/kubernetes_provider.py) — because this
    feature's whole point is producing YAML text a human reads, edits, and
    copies, where JSON-as-YAML's curly braces and quoted keys would look
    nothing like idiomatic Kubernetes YAML.
    """
    return yaml.dump(resource, default_flow_style=False, sort_keys=False, Dumper=_NoAliasDumper)


def rows_to_dict(rows):
    """A FieldList(FormField(KeyValueRowForm)).data list -> {key: value},
    skipping blank rows — every such FieldList always has at least one row
    (min_entries=1) whether or not the user actually filled it in.
    """
    result = {}
    for row in rows or []:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        result[key] = row.get("value") or ""
    return result
