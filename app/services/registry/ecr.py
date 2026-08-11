import base64
import re

import boto3
import botocore.exceptions

from app.services.registry.base import RegistryProvider

_HOST_PATTERN = re.compile(r"^(?P<account_id>\d+)\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com$")


class ECRProvider(RegistryProvider):
    """AWS Elastic Container Registry — the one provider family member that
    doesn't fit the Docker Registry HTTP API v2 bearer-challenge shape
    GHCRProvider/HarborProvider/DockerHubProvider share (see dockerhub.py's
    docstring): ECR needs AWS SigV4-signed API calls, which boto3 already
    handles, so this uses `boto3`'s own ECR client rather than hand-rolling
    that signing against the generic bearer flow.

    `RegistryTarget`'s existing three credential fields are reinterpreted
    for this one provider type rather than adding AWS-specific columns:
    `username`/`password` (this class's `username`/`password` params) hold
    the AWS access key ID / secret access key, and `registry_url` holds the
    account's ECR host (`<account-id>.dkr.ecr.<region>.amazonaws.com`), the
    same "registry_url is this provider's own host" role HarborProvider
    already gives that field — the region is parsed back out of that host
    rather than needing its own field.
    """

    def __init__(self, username=None, password=None, registry_url=None):
        if not registry_url:
            raise ValueError("ECRProvider requires a registry_url (the account's ECR host).")
        self._registry_host = registry_url.replace("https://", "").replace("http://", "").rstrip("/")

        match = _HOST_PATTERN.match(self._registry_host)
        if not match:
            raise ValueError(
                f"registry_url {self._registry_host!r} doesn't look like an ECR host "
                "(expected '<account-id>.dkr.ecr.<region>.amazonaws.com')."
            )
        self._region = match.group("region")

        self.access_key_id = username
        self.secret_access_key = password
        self._client = boto3.client(
            "ecr",
            region_name=self._region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )

    @property
    def registry_host(self):
        return self._registry_host

    def full_repository_name(self, repository):
        # No "prepend an owner/project" step like GHCR/Harbor — the AWS
        # account is already encoded in registry_host itself, not the path.
        if repository.startswith(f"{self.registry_host}/"):
            return repository
        return f"{self.registry_host}/{repository}"

    def _repository_path(self, repository):
        """The bare repository name ECR's own API wants (no host prefix) —
        the inverse of full_repository_name.
        """
        full_repository = self.full_repository_name(repository)
        return full_repository[len(f"{self.registry_host}/") :]

    def authenticate(self, docker_client):
        # Checked explicitly (matching DockerHubProvider/GHCRProvider's own
        # "raise if not configured" pattern) rather than letting boto3
        # silently fall back to any ambient AWS credentials on the host
        # (env vars, an EC2 instance role, ...) if these are missing — that
        # fallback exists for boto3's normal use cases, but here it would
        # mean authenticating as some other identity instead of failing.
        if not self.access_key_id or not self.secret_access_key:
            raise RuntimeError("ECR credentials are not configured (AWS access key ID/secret access key).")
        try:
            response = self._client.get_authorization_token()
        except botocore.exceptions.ClientError as exc:
            raise RuntimeError(f"Could not obtain an ECR authorization token: {exc}") from None

        auth_data = response["authorizationData"][0]
        # authorizationToken is base64("AWS:<short-lived password>") — not a
        # long-lived secret, safe to decode per-call rather than caching it.
        username, _, password = base64.b64decode(auth_data["authorizationToken"]).decode().partition(":")
        return docker_client.login(username=username, password=password, registry=self.registry_host)

    def push_image(self, docker_client, repository, tag):
        self.authenticate(docker_client)
        full_repository = self.full_repository_name(repository)
        events = list(docker_client.images.push(full_repository, tag=tag, stream=True, decode=True))

        # Same "docker-py's push stream never raises on its own" caveat as
        # DockerHubProvider.push_image — see there for why this check exists.
        for event in events:
            if "error" in event:
                raise RuntimeError(f"ECR push failed for {full_repository}:{tag}: {event['error']}")

        return events

    def list_tags(self, repository):
        repository_name = self._repository_path(repository)
        tags = []
        try:
            paginator = self._client.get_paginator("list_images")
            for page in paginator.paginate(repositoryName=repository_name, filter={"tagStatus": "TAGGED"}):
                tags.extend(image_id["imageTag"] for image_id in page["imageIds"] if "imageTag" in image_id)
        except botocore.exceptions.ClientError as exc:
            raise RuntimeError(f"Could not list ECR tags for {repository_name!r}: {exc}") from None
        return tags

    def validate_credentials(self):
        # get_authorization_token needs no specific repository to scope a
        # check against — same "obtain a real token" approach as the other
        # providers' validate_credentials, just via boto3 instead of a raw
        # bearer-challenge HTTP call.
        if not self.access_key_id or not self.secret_access_key:
            raise RuntimeError("ECR credentials are not configured (AWS access key ID/secret access key).")
        try:
            self._client.get_authorization_token()
        except botocore.exceptions.ClientError as exc:
            raise RuntimeError(f"ECR authentication failed: {exc}") from None
        return True
