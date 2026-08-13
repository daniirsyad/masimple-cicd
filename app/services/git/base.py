import fnmatch
import os
from abc import ABC, abstractmethod


class GitProvider(ABC):
    """Provider-agnostic interface for registering and syncing git repositories.

    Every method after registration is stateless: it takes the repository's
    already-cloned `local_path` explicitly rather than relying on a prior
    `clone_repo()` call setting instance state, since one provider instance
    now operates on many persistently-cloned repos rather than one ephemeral
    per-build clone.
    """

    @abstractmethod
    def list_repos(self):
        """Return the repos this connection's token can access, via the remote
        API — the only method that runs *before* a repo is registered/cloned.

        Each entry: {"full_name": ..., "default_branch": ..., "private": ...}.
        """

    @abstractmethod
    def clone_repo(self, repo_name, local_path):
        """Full clone of `repo_name` into `local_path`, once, at registration
        time. Returns the repo's default branch name (as checked out).
        """

    @abstractmethod
    def sync_repo(self, local_path, branch, repo_name=None):
        """Fetch + checkout + pull `branch` in the already-cloned repo at
        `local_path`. Called at build time instead of cloning fresh.

        If `repo_name` is given and `local_path` turns out to be missing or
        not a valid git repo (e.g. no persistent storage was ever mounted
        onto it, so a registered repo's clone didn't survive a container
        restart), self-heals by re-cloning `repo_name` into `local_path`
        before syncing, rather than failing the sync/build outright.
        """

    @abstractmethod
    def list_branches(self, local_path):
        """Return remote branch names for the repo cloned at `local_path`."""

    @abstractmethod
    def list_tags(self, local_path):
        """Return tag names for the repo cloned at `local_path`."""

    @abstractmethod
    def get_latest_commit(self, local_path, branch):
        """Return the full commit SHA for `branch` in the repo at `local_path`."""

    @abstractmethod
    def get_commits(self, local_path, since_ref=None, until_ref=None):
        """Return commits between `since_ref` (exclusive) and `until_ref`
        (inclusive, defaults to HEAD) in the repo at `local_path`, oldest first,
        as a list of dicts: {"sha", "author_name", "author_email", "message",
        "committed_at"}. If `since_ref` is None (e.g. the first build on a
        branch, with no prior build to diff against), returns a bounded recent
        history instead of the entire repo log.
        """

    def get_commit_messages(self, local_path, since_ref=None, until_ref=None):
        """Same range/fallback semantics as `get_commits`, but just the message
        text — identical for every provider, so it's implemented once here
        (like `list_files`) rather than duplicated per-subclass.
        """
        return [commit["message"] for commit in self.get_commits(local_path, since_ref, until_ref)]

    def list_files(self, local_path):
        """Walk the repo at `local_path` and return relative paths of every
        Dockerfile-like file (`Dockerfile`, `Dockerfile.*`, `*.dockerfile`,
        case-insensitive, any depth) — powers the Dockerfile picker.

        Pure filesystem logic, identical for every provider, so it's
        implemented once here rather than duplicated per-subclass.
        """
        matches = []
        for root, dirs, files in os.walk(local_path):
            dirs[:] = [d for d in dirs if d != ".git"]
            for filename in files:
                lower = filename.lower()
                if (
                    lower == "dockerfile"
                    or fnmatch.fnmatch(lower, "dockerfile.*")
                    or fnmatch.fnmatch(lower, "*.dockerfile")
                ):
                    rel_path = os.path.relpath(os.path.join(root, filename), local_path)
                    matches.append(rel_path.replace(os.sep, "/"))
        return sorted(matches)
