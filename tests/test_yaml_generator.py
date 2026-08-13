import base64

import pytest
import yaml
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Permission, Role, User
from app.services.yaml_generator import configmap, deployment, ingress, secret, service
from app.services.yaml_generator.render import to_yaml

GENERATOR_PASSWORD = "GeneratorPass123!"
VIEW_ONLY_PASSWORD = "ViewOnlyPass123!"


@pytest.fixture
def generator_user(app):
    with app.app_context():
        permissions = [
            Permission(code="yaml_generator.view", description="view"),
            Permission(code="deployment_manifest.view", description="view manifests"),
            Permission(code="deployment_manifest.manage", description="manage manifests"),
        ]
        db.session.add_all(permissions)
        role = Role(name="GeneratorAdmin", description="Test generator role")
        role.permissions = permissions
        db.session.add(role)
        db.session.flush()

        user = User(
            username="generator_test",
            password_hash=generate_password_hash(GENERATOR_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def generator_client(client, generator_user):
    client.post(
        "/login", data={"username": "generator_test", "password": GENERATOR_PASSWORD}, follow_redirects=True
    )
    return client


@pytest.fixture
def view_only_client(app, client):
    with app.app_context():
        # A permission that exists but isn't yaml_generator.view — proves
        # the gate checks the specific code, not just "is authenticated".
        permission = Permission(code="logs.view", description="view logs")
        db.session.add(permission)
        role = Role(name="ViewOnlyRole", description="Has some permission, not yaml_generator.view")
        role.permissions = [permission]
        db.session.add(role)
        db.session.flush()

        user = User(
            username="view_only_test",
            password_hash=generate_password_hash(VIEW_ONLY_PASSWORD),
            is_active=True,
            role_id=role.id,
        )
        db.session.add(user)
        db.session.commit()

    client.post(
        "/login", data={"username": "view_only_test", "password": VIEW_ONLY_PASSWORD}, follow_redirects=True
    )
    return client


def _kv_rows(pairs):
    return [{"key": k, "value": v} for k, v in pairs.items()]


class TestDeploymentBuild:
    def test_builds_expected_shape(self):
        fields = {
            "name": "myapp",
            "namespace": "prod",
            "replicas": 3,
            "image": "myrepo/myapp:1.0.0",
            "container_port": 8080,
            "labels": _kv_rows({"app": "myapp", "tier": "backend"}),
            "env_vars": _kv_rows({"ENV": "production"}),
        }
        resource = deployment.build(fields)

        assert resource["apiVersion"] == "apps/v1"
        assert resource["kind"] == "Deployment"
        assert resource["metadata"]["name"] == "myapp"
        assert resource["metadata"]["namespace"] == "prod"
        assert resource["metadata"]["labels"] == {"app": "myapp", "tier": "backend"}
        assert resource["spec"]["replicas"] == 3
        assert resource["spec"]["selector"]["matchLabels"] == {"app": "myapp", "tier": "backend"}
        container = resource["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "myrepo/myapp:1.0.0"
        assert container["ports"] == [{"containerPort": 8080}]
        assert container["env"] == [{"name": "ENV", "value": "production"}]

    def test_defaults_labels_to_app_name_when_none_given(self):
        fields = {"name": "myapp", "image": "myrepo/myapp:1.0.0", "labels": [], "env_vars": []}
        resource = deployment.build(fields)
        assert resource["metadata"]["labels"] == {"app": "myapp"}

    def test_placeholder_token_passes_through_unescaped(self):
        fields = {
            "name": "myapp",
            "image": "myrepo/myapp:{{SYS:VERSION}}",
            "labels": [],
            "env_vars": [],
        }
        resource = deployment.build(fields)
        rendered = to_yaml(resource)
        assert "myrepo/myapp:{{SYS:VERSION}}" in rendered


class TestServiceBuild:
    def test_builds_expected_shape(self):
        fields = {
            "name": "myapp",
            "namespace": "prod",
            "service_type": "ClusterIP",
            "selector": _kv_rows({"app": "myapp"}),
            "ports": [{"port": 80, "target_port": 8080, "protocol": "TCP"}],
        }
        resource = service.build(fields)

        assert resource["apiVersion"] == "v1"
        assert resource["kind"] == "Service"
        assert resource["spec"]["type"] == "ClusterIP"
        assert resource["spec"]["selector"] == {"app": "myapp"}
        assert resource["spec"]["ports"] == [{"port": 80, "targetPort": 8080, "protocol": "TCP"}]

    def test_port_row_without_target_port_omits_it(self):
        fields = {"name": "myapp", "selector": [], "ports": [{"port": 80, "target_port": None, "protocol": "TCP"}]}
        resource = service.build(fields)
        assert resource["spec"]["ports"] == [{"port": 80, "protocol": "TCP"}]


class TestConfigMapBuild:
    def test_builds_expected_shape(self):
        fields = {"name": "myconfig", "namespace": "prod", "data": _kv_rows({"KEY": "value"})}
        resource = configmap.build(fields)
        assert resource["kind"] == "ConfigMap"
        assert resource["data"] == {"KEY": "value"}


class TestSecretBuild:
    def test_values_are_base64_encoded_not_plaintext(self):
        fields = {"name": "mysecret", "namespace": "prod", "secret_type": "Opaque", "data": _kv_rows({"PASSWORD": "hunter2"})}
        resource = secret.build(fields)

        assert resource["kind"] == "Secret"
        assert resource["type"] == "Opaque"
        assert resource["data"]["PASSWORD"] != "hunter2"
        assert base64.b64decode(resource["data"]["PASSWORD"]).decode() == "hunter2"


class TestIngressBuild:
    def test_builds_expected_shape_without_tls(self):
        fields = {
            "name": "myingress",
            "namespace": "prod",
            "host": "example.com",
            "path": "/",
            "path_type": "Prefix",
            "backend_service_name": "myapp",
            "backend_service_port": 80,
            "tls_secret_name": None,
        }
        resource = ingress.build(fields)

        assert resource["kind"] == "Ingress"
        rule = resource["spec"]["rules"][0]
        assert rule["host"] == "example.com"
        backend = rule["http"]["paths"][0]["backend"]["service"]
        assert backend == {"name": "myapp", "port": {"number": 80}}
        assert "tls" not in resource["spec"]

    def test_tls_block_present_only_when_secret_name_is_set(self):
        fields = {
            "name": "myingress",
            "host": "example.com",
            "backend_service_name": "myapp",
            "backend_service_port": 80,
            "tls_secret_name": "my-tls-secret",
        }
        resource = ingress.build(fields)
        assert resource["spec"]["tls"] == [{"hosts": ["example.com"], "secretName": "my-tls-secret"}]


class TestToYaml:
    def test_round_trips_through_yaml_safe_load(self):
        resource = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}, "data": {"a": "b"}}
        assert yaml.safe_load(to_yaml(resource)) == resource

    def test_produces_block_style_not_json(self):
        resource = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}, "data": {"a": "b"}}
        rendered = to_yaml(resource)
        assert "{" not in rendered
        assert "kind: ConfigMap" in rendered

    def test_does_not_emit_yaml_anchors_for_a_reused_dict(self):
        """Regression coverage: PyYAML's default Dumper emits &id001/*id001
        anchors/aliases whenever the exact same dict object appears more
        than once in the data — deployment.build() deliberately reuses one
        labels dict across metadata.labels/selector.matchLabels/the pod
        template's own labels (found live via docker-compose testing).
        Valid YAML, but not what a human hand-writing this would produce.
        """
        labels = {"app": "myapp"}
        resource = {
            "metadata": {"labels": labels},
            "spec": {"selector": {"matchLabels": labels}, "template": {"metadata": {"labels": labels}}},
        }
        rendered = to_yaml(resource)
        assert "&id" not in rendered
        assert "*id" not in rendered
        assert rendered.count("app: myapp") == 3


class TestGenerateRoute:
    def test_valid_deployment_submission_returns_yaml(self, generator_client):
        response = generator_client.post(
            "/yaml-generator/generate/deployment",
            data={
                "name": "myapp",
                "namespace": "default",
                "replicas": "1",
                "image": "myrepo/myapp:1.0.0",
                "labels-0-key": "app",
                "labels-0-value": "myapp",
            },
        )
        assert response.status_code == 200
        assert "kind: Deployment" in response.json["yaml"]

    def test_missing_required_field_returns_400_with_errors(self, generator_client):
        response = generator_client.post(
            "/yaml-generator/generate/deployment",
            data={"namespace": "default"},  # missing name/image
        )
        assert response.status_code == 400
        assert "name" in response.json["errors"]
        assert "image" in response.json["errors"]

    def test_unknown_kind_returns_400(self, generator_client):
        response = generator_client.post("/yaml-generator/generate/bogus", data={})
        assert response.status_code == 400

    def test_blank_optional_integer_field_does_not_error(self, generator_client):
        """Regression coverage for the WTForms blank-optional-IntegerField
        gotcha (see OptionalIntegerField in forms.py) — container_port left
        blank must not surface a "Not a valid integer value" error.
        """
        response = generator_client.post(
            "/yaml-generator/generate/deployment",
            data={
                "name": "myapp",
                "image": "myrepo/myapp:1.0.0",
                "container_port": "",
                "labels-0-key": "app",
                "labels-0-value": "myapp",
            },
        )
        assert response.status_code == 200
        assert "containerPort" not in response.json["yaml"]


class TestPermissionGating:
    def test_index_requires_login(self, client):
        response = client.get("/yaml-generator/")
        assert response.status_code in (302, 401)

    def test_index_requires_yaml_generator_view(self, view_only_client):
        response = view_only_client.get("/yaml-generator/")
        assert response.status_code == 403

    def test_generate_requires_yaml_generator_view(self, view_only_client):
        response = view_only_client.post("/yaml-generator/generate/deployment", data={})
        assert response.status_code == 403


class TestSaveAsManifestHandoff:
    def test_redirects_and_prefills_the_create_manifest_modal(self, generator_client):
        response = generator_client.post(
            "/yaml-generator/save-as-manifest",
            data={"name": "my-generated-manifest", "yaml_content": "kind: Deployment\nmetadata:\n  name: myapp\n"},
        )
        assert response.status_code == 302
        assert response.headers["Location"] == "/deployment-manifests/?open=create"

        follow = generator_client.get(response.headers["Location"])
        html = follow.get_data(as_text=True)
        assert "my-generated-manifest" in html
        assert "kind: Deployment" in html

    def test_prefill_is_one_shot(self, generator_client):
        generator_client.post(
            "/yaml-generator/save-as-manifest",
            data={"name": "my-generated-manifest", "yaml_content": "kind: Deployment\n"},
        )
        generator_client.get("/deployment-manifests/?open=create")  # consumes the session prefill

        second = generator_client.get("/deployment-manifests/")
        html = second.get_data(as_text=True)
        assert "my-generated-manifest" not in html
