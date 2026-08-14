import base64

import pytest
import yaml
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Permission, Role, User
from app.services.yaml_generator import configmap, deployment, ingress, network_policy, secret, service
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

    def test_multiple_paths_all_land_under_the_one_host_rule(self):
        # The `paths` list is the deployment_pods Ingress CRUD's own input
        # shape (one host, one-or-more paths) — a second, shared consumer
        # of this same builder alongside the flat single-path fields above.
        fields = {
            "name": "myingress",
            "namespace": "prod",
            "host": "example.com",
            "paths": [
                {"path": "/api", "path_type": "Prefix", "backend_service_name": "api-svc", "backend_service_port": 8080},
                {"path": "/app", "path_type": "Exact", "backend_service_name": "app-svc", "backend_service_port": 80},
            ],
        }
        resource = ingress.build(fields)

        rule = resource["spec"]["rules"][0]
        assert rule["host"] == "example.com"
        paths = rule["http"]["paths"]
        assert len(paths) == 2
        assert paths[0]["path"] == "/api"
        assert paths[0]["backend"]["service"] == {"name": "api-svc", "port": {"number": 8080}}
        assert paths[1]["path"] == "/app"
        assert paths[1]["pathType"] == "Exact"

    def test_ingress_class_name_set_only_when_provided(self):
        fields = {
            "name": "myingress",
            "host": "example.com",
            "backend_service_name": "myapp",
            "backend_service_port": 80,
            "ingress_class_name": "nginx",
        }
        resource = ingress.build(fields)
        assert resource["spec"]["ingressClassName"] == "nginx"

        fields.pop("ingress_class_name")
        resource = ingress.build(fields)
        assert "ingressClassName" not in resource["spec"]


class TestNetworkPolicyBuild:
    def test_ingress_only_with_pod_and_ip_block_peers(self):
        # pod_selector as a raw {key: value} dict (deployment_pods' own
        # shape) — the alternate FieldList-rows shape is covered by
        # test_pod_selector_accepts_raw_fieldlist_rows below.
        fields = {
            "name": "backend-policy",
            "namespace": "prod",
            "pod_selector": {"app": "backend"},
            "enable_ingress_rules": True,
            "ingress_peers": [
                {"peer_type": "pod", "value": "app=frontend"},
                {"peer_type": "ip_block", "value": "10.0.0.0/8"},
            ],
            "ingress_ports": [{"protocol": "TCP", "port": 8080}],
        }
        resource = network_policy.build(fields)

        assert resource["kind"] == "NetworkPolicy"
        spec = resource["spec"]
        assert spec["podSelector"]["matchLabels"] == {"app": "backend"}
        assert spec["policyTypes"] == ["Ingress"]
        assert spec["ingress"][0]["from"] == [
            {"podSelector": {"matchLabels": {"app": "frontend"}}},
            {"ipBlock": {"cidr": "10.0.0.0/8"}},
        ]
        assert spec["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 8080}]
        assert "egress" not in spec

    def test_egress_only_with_namespace_selector_peer(self):
        fields = {
            "name": "egress-policy",
            "pod_selector": {},
            "enable_egress_rules": True,
            "egress_peers": [{"peer_type": "namespace", "value": "team=platform"}],
            "egress_ports": [{"protocol": "UDP", "port": 53}],
        }
        resource = network_policy.build(fields)

        spec = resource["spec"]
        assert spec["podSelector"] == {}
        assert spec["policyTypes"] == ["Egress"]
        assert spec["egress"][0]["to"] == [{"namespaceSelector": {"matchLabels": {"team": "platform"}}}]
        assert "ingress" not in spec

    def test_empty_rule_means_allow_all_for_that_direction(self):
        # No peers/ports at all for Ingress — a deliberate "allow all
        # ingress traffic to these pods" policy, valid Kubernetes.
        fields = {"name": "allow-all-ingress", "pod_selector": {"app": "x"}, "enable_ingress_rules": True}
        resource = network_policy.build(fields)
        assert resource["spec"]["ingress"] == [{}]

    def test_blank_peer_value_is_dropped(self):
        fields = {
            "name": "backend-policy",
            "pod_selector": {},
            "enable_ingress_rules": True,
            "ingress_peers": [{"peer_type": "pod", "value": ""}],
            "ingress_ports": [],
        }
        resource = network_policy.build(fields)
        assert resource["spec"]["ingress"] == [{}]

    def test_pod_selector_accepts_raw_fieldlist_rows(self):
        # The YAML Generator page's own shape: FieldList(FormField(KeyValueRowForm)).data
        fields = {
            "name": "backend-policy",
            "pod_selector": [{"key": "app", "value": "backend"}, {"key": "", "value": ""}],
            "enable_ingress_rules": True,
        }
        resource = network_policy.build(fields)
        assert resource["spec"]["podSelector"]["matchLabels"] == {"app": "backend"}

    def test_no_direction_enabled_omits_policy_types_and_rules(self):
        fields = {"name": "noop-policy", "pod_selector": {}}
        resource = network_policy.build(fields)
        spec = resource["spec"]
        assert "policyTypes" not in spec
        assert "ingress" not in spec
        assert "egress" not in spec


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

    def test_valid_network_policy_submission_returns_yaml(self, generator_client):
        response = generator_client.post(
            "/yaml-generator/generate/network_policy",
            data={
                "name": "backend-policy",
                "namespace": "default",
                "pod_selector-0-key": "app",
                "pod_selector-0-value": "backend",
                "enable_ingress_rules": "y",
                "ingress_peers-0-peer_type": "pod",
                "ingress_peers-0-value": "app=frontend",
                "ingress_ports-0-protocol": "TCP",
                "ingress_ports-0-port": "8080",
            },
        )
        assert response.status_code == 200
        assert "kind: NetworkPolicy" in response.json["yaml"]
        assert "podSelector" in response.json["yaml"]

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


class TestIndexPage:
    def test_renders_including_the_network_policy_panel(self, generator_client):
        response = generator_client.get("/yaml-generator/")
        assert response.status_code == 200
        assert b"NetworkPolicy" in response.data
        assert b'data-kind="network_policy"' in response.data


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
