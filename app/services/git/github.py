import os
import shutil

import git
import requests

from app.services.git.base import GitProvider

DEFAULT_LOG_LIMIT = 20
GITHUB_API_BASE_URL = "https://api.github.com"


class GitHubProvider(GitProvider):
    """Talks to github.com over HTTPS: GitPython for clone/sync/log against an
    already-registered local clone, the REST API for list_repos() (the one
    method that runs before any local clone exists).
    """

    def __init__(self, token=None):
        self.token = token or os.environ.get("GITHUB_TOKEN")

    def _authenticated_url(self, repo_url):
        if not self.token or not repo_url.startswith("https://"):
            return repo_url
        return repo_url.replace("https://", f"https://x-access-token:{self.token}@", 1)

    def list_repos(self):
        if not self.token:
            raise RuntimeError("No GitHub token configured — cannot list repositories.")
        response = requests.get(
            f"{GITHUB_API_BASE_URL}/user/repos",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"},
            params={"per_page": 100, "sort": "full_name"},
            timeout=10,
        )
        response.raise_for_status()
        return [
            {
                "full_name": repo["full_name"],
                "default_branch": repo["default_branch"],
                "private": repo["private"],
            }
            for repo in response.json()
        ]

    def clone_repo(self, repo_name, local_path):
        clone_url = self._authenticated_url(f"https://github.com/{repo_name}.git")
        try:
            repo = git.Repo.clone_from(clone_url, local_path)
        except git.GitCommandError:
            # GitPython's exception embeds the full command it ran, including
            # the authenticated URL — never let the token reach a log/UI.
            raise RuntimeError(
                f"Failed to clone repository {repo_name!r}. Check the name, token, and network access."
            ) from None
        return repo.active_branch.name

    def sync_repo(self, local_path, branch, repo_name=None):
        if repo_name and not self._repo_exists(local_path):
            # The persistent clone is missing on disk (e.g. no external
            # storage has been mounted onto REPO_CLONE_ROOT yet, so it didn't
            # survive a container restart) — self-heal by re-cloning into the
            # same path rather than failing this sync/build. rmtree first in
            # case local_path exists but is broken/partial (clone_from
            # refuses to clone into a non-empty directory).
            shutil.rmtree(local_path, ignore_errors=True)
            self.clone_repo(repo_name, local_path)

        repo = self._open_repo(local_path)
        try:
            origin = repo.remote()
            origin.fetch()
            # DWIM: `git checkout <branch>` auto-creates a local tracking
            # branch from origin/<branch> the first time it's asked for.
            repo.git.checkout(branch)
            origin.pull(branch)
        except git.GitCommandError:
            raise RuntimeError(
                f"Failed to sync repository at {local_path!r} to branch {branch!r}."
            ) from None

    def list_branches(self, local_path):
        repo = self._open_repo(local_path)
        return sorted(ref.remote_head for ref in repo.remote().refs if ref.remote_head != "HEAD")

    def list_tags(self, local_path):
        repo = self._open_repo(local_path)
        return sorted(tag.name for tag in repo.tags)

    def get_latest_commit(self, local_path, branch):
        repo = self._open_repo(local_path)
        try:
            commit = repo.commit(branch)
        except (git.BadName, ValueError):
            # `branch` may only exist as a remote-tracking ref (origin/<branch>)
            # if it's never been checked out locally yet.
            commit = repo.commit(f"origin/{branch}")
        return commit.hexsha

    def get_commit_messages(self, local_path, since_ref=None, until_ref=None):
        repo = self._open_repo(local_path)
        until_ref = until_ref or "HEAD"
        try:
            if since_ref:
                commits = list(repo.iter_commits(f"{since_ref}..{until_ref}"))
            else:
                commits = list(repo.iter_commits(until_ref, max_count=DEFAULT_LOG_LIMIT))
        except git.GitCommandError:
            # since_ref may no longer be reachable (force-push, rebase, etc.) —
            # fall back to a bounded recent log rather than failing the build.
            commits = list(repo.iter_commits(until_ref, max_count=DEFAULT_LOG_LIMIT))
        return [commit.message.strip() for commit in reversed(commits)]

    @staticmethod
    def _open_repo(local_path):
        try:
            return git.Repo(local_path)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            raise RuntimeError(f"No git repository found at {local_path!r}.") from None

    @staticmethod
    def _repo_exists(local_path):
        try:
            git.Repo(local_path)
            return True
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            return False
