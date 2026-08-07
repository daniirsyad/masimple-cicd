import git
import pytest

from app.services.git.github import GitHubProvider


@pytest.fixture
def local_repo(tmp_path):
    """A real local git repo with 5 sequential commits, no remote — enough for
    get_commit_messages(), which doesn't need one.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")

    commits = []
    file_path = repo_dir / "file.txt"
    for i in range(5):
        file_path.write_text(f"content {i}")
        repo.index.add([str(file_path)])
        commit = repo.index.commit(f"commit message {i}")
        commits.append(commit)

    return str(repo_dir), commits


@pytest.fixture
def cloned_repo(tmp_path):
    """A working local clone with `origin` pointing at a bare 'remote' repo —
    mirrors what a real registered Repository's local_path looks like.
    Built entirely from local git operations (bare clone + file-path clone),
    so no network access is needed to exercise list_branches/list_tags/
    get_latest_commit/sync_repo.
    """
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    seed = git.Repo.init(seed_dir)
    with seed.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")

    default_branch = seed.active_branch.name

    commits = []
    file_path = seed_dir / "file.txt"
    for i in range(5):
        file_path.write_text(f"content {i}")
        seed.index.add([str(file_path)])
        commits.append(seed.index.commit(f"commit message {i}"))
    seed.create_tag("v1.0.0")

    other_branch = seed.create_head("feature-other", commits[-1])
    other_branch.checkout()
    (seed_dir / "other.txt").write_text("other content")
    seed.index.add([str(seed_dir / "other.txt")])
    other_commit = seed.index.commit("commit on other branch")
    seed.heads[default_branch].checkout()

    bare_dir = tmp_path / "remote.git"
    git.Repo.clone_from(str(seed_dir), bare_dir, bare=True)

    work_dir = tmp_path / "work"
    git.Repo.clone_from(str(bare_dir), work_dir)

    return str(work_dir), default_branch, commits, other_commit


class TestGetCommitMessages:
    def test_returns_all_recent_commits_when_since_ref_is_none(self, local_repo):
        local_path, commits = local_repo
        provider = GitHubProvider()

        messages = provider.get_commit_messages(local_path, since_ref=None, until_ref="HEAD")
        assert messages == [f"commit message {i}" for i in range(5)]

    def test_returns_only_commits_after_since_ref(self, local_repo):
        local_path, commits = local_repo
        provider = GitHubProvider()

        since = commits[1].hexsha  # everything strictly after "commit message 1"
        messages = provider.get_commit_messages(local_path, since_ref=since, until_ref="HEAD")
        assert messages == ["commit message 2", "commit message 3", "commit message 4"]

    def test_returns_empty_list_when_since_ref_is_the_latest_commit(self, local_repo):
        local_path, commits = local_repo
        provider = GitHubProvider()

        messages = provider.get_commit_messages(local_path, since_ref=commits[-1].hexsha, until_ref="HEAD")
        assert messages == []

    def test_falls_back_to_recent_log_when_since_ref_is_unreachable(self, local_repo):
        local_path, commits = local_repo
        provider = GitHubProvider()

        bogus_sha = "deadbeef" * 5
        messages = provider.get_commit_messages(local_path, since_ref=bogus_sha, until_ref="HEAD")
        # falls back to the bounded recent log rather than raising
        assert messages == [f"commit message {i}" for i in range(5)]

    def test_respects_default_log_limit_when_no_since_ref(self, local_repo, monkeypatch):
        local_path, commits = local_repo
        monkeypatch.setattr("app.services.git.github.DEFAULT_LOG_LIMIT", 2)
        provider = GitHubProvider()

        messages = provider.get_commit_messages(local_path, since_ref=None, until_ref="HEAD")
        assert messages == ["commit message 3", "commit message 4"]

    def test_raises_for_a_path_that_is_not_a_git_repo(self, tmp_path):
        empty_dir = tmp_path / "not-a-repo"
        empty_dir.mkdir()
        provider = GitHubProvider()

        with pytest.raises(RuntimeError):
            provider.get_commit_messages(str(empty_dir))


class TestListBranches:
    def test_lists_remote_branches_sorted(self, cloned_repo):
        local_path, default_branch, commits, other_commit = cloned_repo
        provider = GitHubProvider()

        assert provider.list_branches(local_path) == sorted([default_branch, "feature-other"])


class TestListTags:
    def test_lists_tags(self, cloned_repo):
        local_path, default_branch, commits, other_commit = cloned_repo
        provider = GitHubProvider()

        assert provider.list_tags(local_path) == ["v1.0.0"]


class TestGetLatestCommit:
    def test_resolves_the_checked_out_branch_to_its_tip_sha(self, cloned_repo):
        local_path, default_branch, commits, other_commit = cloned_repo
        provider = GitHubProvider()

        assert provider.get_latest_commit(local_path, default_branch) == commits[-1].hexsha

    def test_resolves_a_branch_not_checked_out_locally_via_the_remote_ref(self, cloned_repo):
        local_path, default_branch, commits, other_commit = cloned_repo
        provider = GitHubProvider()

        # "feature-other" only exists as origin/feature-other until synced.
        assert provider.get_latest_commit(local_path, "feature-other") == other_commit.hexsha


class TestSyncRepo:
    def test_checks_out_and_updates_to_a_branch_not_yet_local(self, cloned_repo):
        local_path, default_branch, commits, other_commit = cloned_repo
        provider = GitHubProvider()

        provider.sync_repo(local_path, "feature-other")

        repo = git.Repo(local_path)
        assert repo.active_branch.name == "feature-other"
        assert repo.head.commit.hexsha == other_commit.hexsha

    def test_raises_a_clean_error_for_an_unknown_branch(self, cloned_repo):
        local_path, default_branch, commits, other_commit = cloned_repo
        provider = GitHubProvider()

        with pytest.raises(RuntimeError):
            provider.sync_repo(local_path, "does-not-exist")


class TestSyncRepoSelfHeals:
    """sync_repo(local_path, branch, repo_name=...) re-clones when local_path
    is missing/broken, rather than failing — see the NoSuchPathError bug this
    guards against. clone_repo() itself is faked to clone from a local bare
    repo instead of hitting github.com, mirroring how `cloned_repo` above
    avoids network access.
    """

    @staticmethod
    def _make_seed_and_bare(tmp_path):
        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()
        seed = git.Repo.init(seed_dir)
        with seed.config_writer() as writer:
            writer.set_value("user", "name", "Test")
            writer.set_value("user", "email", "test@example.com")
        default_branch = seed.active_branch.name
        (seed_dir / "file.txt").write_text("content")
        seed.index.add([str(seed_dir / "file.txt")])
        seed.index.commit("initial commit")

        bare_dir = tmp_path / "remote.git"
        git.Repo.clone_from(str(seed_dir), bare_dir, bare=True)
        return bare_dir, default_branch

    def test_reclones_when_the_local_path_is_missing(self, tmp_path, monkeypatch):
        bare_dir, default_branch = self._make_seed_and_bare(tmp_path)
        missing_local_path = str(tmp_path / "work")  # never cloned in the first place
        provider = GitHubProvider()
        monkeypatch.setattr(
            provider,
            "clone_repo",
            lambda repo_name, local_path: git.Repo.clone_from(str(bare_dir), local_path).active_branch.name,
        )

        provider.sync_repo(missing_local_path, default_branch, repo_name="org/repo")

        repo = git.Repo(missing_local_path)
        assert repo.head.commit.message == "initial commit"

    def test_reclones_when_the_local_path_exists_but_is_not_a_git_repo(self, tmp_path, monkeypatch):
        broken_dir = tmp_path / "work"
        broken_dir.mkdir()
        (broken_dir / "stray-file.txt").write_text("leftover junk from a partial clone")

        bare_dir, default_branch = self._make_seed_and_bare(tmp_path)
        provider = GitHubProvider()
        monkeypatch.setattr(
            provider,
            "clone_repo",
            lambda repo_name, local_path: git.Repo.clone_from(str(bare_dir), local_path).active_branch.name,
        )

        provider.sync_repo(str(broken_dir), default_branch, repo_name="org/repo")

        repo = git.Repo(str(broken_dir))
        assert repo.head.commit.message == "initial commit"

    def test_does_not_self_heal_without_a_repo_name(self, tmp_path):
        missing_local_path = str(tmp_path / "work")
        provider = GitHubProvider()

        with pytest.raises(RuntimeError):
            provider.sync_repo(missing_local_path, "main")


class TestOpenRepoErrors:
    def test_list_branches_raises_for_a_path_that_is_not_a_git_repo(self, tmp_path):
        empty_dir = tmp_path / "not-a-repo"
        empty_dir.mkdir()
        provider = GitHubProvider()

        with pytest.raises(RuntimeError):
            provider.list_branches(str(empty_dir))

    def test_list_branches_raises_a_clean_error_for_a_path_that_does_not_exist_at_all(self, tmp_path):
        # A registered repo whose clone directory vanished (e.g. no
        # persistent storage mounted onto it) raises git.NoSuchPathError
        # rather than InvalidGitRepositoryError — must be turned into the
        # same clean RuntimeError, not leak the raw GitPython exception.
        provider = GitHubProvider()

        with pytest.raises(RuntimeError):
            provider.list_branches(str(tmp_path / "does-not-exist"))


class TestListFiles:
    """list_files() is implemented once on the GitProvider base class (pure
    filesystem logic, identical for every provider) — exercised here via
    GitHubProvider since it inherits it unchanged.
    """

    def test_finds_dockerfile_variants_at_any_depth_case_insensitively(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch")
        nested = tmp_path / "services" / "api"
        nested.mkdir(parents=True)
        (nested / "dockerfile").write_text("FROM scratch")
        (nested / "Dockerfile.worker").write_text("FROM scratch")
        (tmp_path / "app.dockerfile").write_text("FROM scratch")
        (tmp_path / "README.md").write_text("not a dockerfile")

        provider = GitHubProvider()
        files = provider.list_files(str(tmp_path))

        assert files == sorted(
            [
                "Dockerfile",
                "app.dockerfile",
                "services/api/dockerfile",
                "services/api/Dockerfile.worker",
            ]
        )

    def test_ignores_the_git_directory(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "Dockerfile").write_text("should not be found")
        provider = GitHubProvider()

        assert provider.list_files(str(tmp_path)) == []

    def test_returns_empty_list_when_nothing_matches(self, tmp_path):
        (tmp_path / "README.md").write_text("hello")
        provider = GitHubProvider()

        assert provider.list_files(str(tmp_path)) == []
