from app.services.build.bump_heuristic import classify_commit, suggest_bump_type


class TestClassifyCommit:
    def test_feat_is_minor(self):
        assert classify_commit("feat: add checkout flow") == "minor"

    def test_fix_is_patch(self):
        assert classify_commit("fix: correct off-by-one") == "patch"

    def test_chore_is_patch(self):
        assert classify_commit("chore: bump deps") == "patch"

    def test_scoped_type_is_recognized(self):
        assert classify_commit("feat(auth): add SSO") == "minor"

    def test_bang_marks_a_breaking_change(self):
        assert classify_commit("feat!: drop legacy endpoint") == "major"

    def test_breaking_change_footer_marks_major_regardless_of_first_line(self):
        message = "fix: small tweak\n\nBREAKING CHANGE: removes the old field"
        assert classify_commit(message) == "major"

    def test_non_conventional_message_returns_none(self):
        assert classify_commit("just fixed some stuff") is None

    def test_empty_message_returns_none(self):
        assert classify_commit("") is None


class TestSuggestBumpType:
    def test_defaults_to_patch_when_nothing_is_conventional(self):
        assert suggest_bump_type(["random message", "another one"]) == "patch"

    def test_defaults_to_patch_for_an_empty_list(self):
        assert suggest_bump_type([]) == "patch"

    def test_picks_the_most_severe_signal_across_all_messages(self):
        messages = ["chore: cleanup", "feat: new thing", "fix: bug"]
        assert suggest_bump_type(messages) == "minor"

    def test_major_wins_over_everything_else(self):
        messages = ["fix: bug", "feat: new thing", "feat!: breaking change"]
        assert suggest_bump_type(messages) == "major"

    def test_mix_of_conventional_and_non_conventional_still_classifies(self):
        messages = ["random note", "fix: bug"]
        assert suggest_bump_type(messages) == "patch"
