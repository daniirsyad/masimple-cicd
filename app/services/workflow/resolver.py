from app.models import Builder, DeploymentManifest


def resolve_builders_from_selection(group_names, builder_ids):
    """Ordered list of Builder rows for a raw (group_names, builder_ids)
    selection: every Builder whose group_name matches one of `group_names`
    (each group's members in name order — build order within a batch
    doesn't matter, only which builders are batched together), followed by
    the individually-selected (always ungrouped) `builder_ids`, also in name
    order. The shared core behind `resolve_step_builders` (a saved
    WorkflowStep's own selection) and the build-step authoring modal's
    auto-preview (workflows.routes.build_step_preview, which has no saved
    WorkflowStep yet to resolve from).
    """
    builders = []
    seen_ids = set()

    for group_name in sorted(group_names):
        for builder in Builder.query.filter_by(group_name=group_name).order_by(Builder.name).all():
            if builder.id not in seen_ids:
                builders.append(builder)
                seen_ids.add(builder.id)

    selected = Builder.query.filter(Builder.id.in_(builder_ids)).all() if builder_ids else []
    for builder in sorted(selected, key=lambda b: b.name):
        if builder.id not in seen_ids:
            builders.append(builder)
            seen_ids.add(builder.id)

    return builders


def resolve_step_builders(step):
    """Ordered list of Builder rows a "build" WorkflowStep currently targets
    — see `resolve_builders_from_selection`. Resolved fresh every run: a
    live lookup against current group_name membership, not a frozen list
    (see WorkflowStepGroup's docstring for why).
    """
    return resolve_builders_from_selection(
        [group.group_name for group in step.selected_groups],
        [builder.id for builder in step.selected_builders],
    )


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
