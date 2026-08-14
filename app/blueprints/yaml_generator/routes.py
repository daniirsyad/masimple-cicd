from flask import abort, jsonify, redirect, render_template, request, session, url_for

from app.blueprints.yaml_generator import yaml_generator_bp
from app.blueprints.yaml_generator.forms import (
    ConfigMapForm,
    DeploymentForm,
    IngressForm,
    NetworkPolicyForm,
    SecretForm,
    ServiceForm,
)
from app.services.yaml_generator import configmap, deployment, ingress, network_policy, secret, service
from app.services.yaml_generator.render import to_yaml
from app.utils.decorators import permission_required

# kind -> (form class, build(fields: dict) -> dict) — mirrors this app's
# other factory-dict conventions (app/services/ai/factory.py,
# app/services/registry/factory.py). Lives here rather than in
# app/services/yaml_generator/ since it couples a WTForm (blueprint layer)
# with a build function (service layer) — services don't import from
# blueprints anywhere else in this app, so the dispatch belongs on this
# side of that boundary.
_GENERATORS = {
    "deployment": (DeploymentForm, deployment.build),
    "service": (ServiceForm, service.build),
    "configmap": (ConfigMapForm, configmap.build),
    "secret": (SecretForm, secret.build),
    "ingress": (IngressForm, ingress.build),
    "network_policy": (NetworkPolicyForm, network_policy.build),
}

KIND_LABELS = [
    ("deployment", "Deployment"),
    ("service", "Service"),
    ("configmap", "ConfigMap"),
    ("secret", "Secret"),
    ("ingress", "Ingress"),
    ("network_policy", "NetworkPolicy"),
]


@yaml_generator_bp.route("/")
@permission_required("yaml_generator.view")
def index():
    forms = {kind: form_cls() for kind, (form_cls, _build_fn) in _GENERATORS.items()}
    return render_template("yaml_generator/index.html", forms=forms, kinds=KIND_LABELS)


@yaml_generator_bp.route("/generate/<kind>", methods=["POST"])
@permission_required("yaml_generator.view")
def generate(kind):
    if kind not in _GENERATORS:
        abort(400, description=f"Unknown resource kind '{kind}'.")

    form_cls, build_fn = _GENERATORS[kind]
    # CSRF for this AJAX-only endpoint is already enforced globally
    # (CSRFProtect checks the X-CSRFToken header before this view ever
    # runs, same as every other POST route) — meta={"csrf": False} just
    # stops the form's own redundant csrf_token field validation from
    # failing because the AJAX body never includes that field.
    form = form_cls(request.form, meta={"csrf": False})

    if not form.validate():
        return jsonify({"errors": form.errors}), 400

    resource = build_fn(form.data)
    return jsonify({"yaml": to_yaml(resource)})


@yaml_generator_bp.route("/save-as-manifest", methods=["POST"])
@permission_required("yaml_generator.view")
def save_as_manifest():
    # Nothing is persisted here — this only stages a one-shot prefill for
    # the *existing* DeploymentManifest creation flow (see
    # deployment_manifests.routes.index()), so no deployment_manifest.*
    # permission is checked here; the user hits that check naturally the
    # instant they actually try to save, at create_manifest()'s own gate.
    session["yaml_generator_prefill"] = {
        "name": (request.form.get("name") or "").strip(),
        "yaml_content": request.form.get("yaml_content") or "",
    }
    return redirect(url_for("deployment_manifests.index", open="create"))
