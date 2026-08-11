from app.models import Builder, DeploymentManifest


def resolve_step_builders(step):
    """Ordered list of Builder rows a "build" WorkflowStep currently targets:
    every Builder whose group_name matches one of the step's selected groups
    (see WorkflowStepGroup), each group's members in name order — build
    order within a batch doesn't matter, only which builders are batched
    together — followed by the step's individually selected (always
    ungrouped) Builders, also in name order. Resolved fresh every run: a
    live lookup against current group_name membership, not a frozen list
    (see WorkflowStepGroup's docstring for why).
    """
    builders = []
    seen_ids = set()

    group_names = sorted(g.group_name for g in step.selected_groups)
    for group_name in group_names:
        for builder in Builder.query.filter_by(group_name=group_name).order_by(Builder.name).all():
            if builder.id not in seen_ids:
                builders.append(builder)
                seen_ids.add(builder.id)

    for builder in sorted(step.selected_builders, key=lambda b: b.name):
        if builder.id not in seen_ids:
            builders.append(builder)
            seen_ids.add(builder.id)

    return builders


def resolve_step_manifests(step):
    """Ordered list of DeploymentManifest rows a "deploy" WorkflowStep
    currently targets — same shape as resolve_step_builders, except each
    selected group's members are ordered by DeploymentManifest.order (its
    user-configured drag-and-drop deploy order, ascending), matching the
    walk a manual "Deploy Group" click already does.
    """
    manifests = []
    seen_ids = set()

    group_names = sorted(g.group_name for g in step.selected_groups)
    for group_name in group_names:
        query = (
            DeploymentManifest.query.filter_by(group_name=group_name)
            .order_by(DeploymentManifest.order, DeploymentManifest.name)
            .all()
        )
        for manifest in query:
            if manifest.id not in seen_ids:
                manifests.append(manifest)
                seen_ids.add(manifest.id)

    for manifest in sorted(step.selected_manifests, key=lambda m: m.name):
        if manifest.id not in seen_ids:
            manifests.append(manifest)
            seen_ids.add(manifest.id)

    return manifests
