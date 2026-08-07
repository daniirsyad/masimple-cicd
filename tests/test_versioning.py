from datetime import datetime

import pytest

from app.extensions import db
from app.models import Version, VersionType
from app.services.build.versioning import (
    build_full_version_string,
    bump_version,
    compute_next_version,
    format_datetime_string,
)


class TestComputeNextVersion:
    def test_patch_bump_only_increments_patch(self):
        assert compute_next_version(1, 2, 3, "patch") == (1, 2, 4)

    def test_minor_bump_increments_minor_and_resets_patch(self):
        assert compute_next_version(1, 2, 3, "minor") == (1, 3, 0)

    def test_major_bump_increments_major_and_resets_minor_and_patch(self):
        assert compute_next_version(1, 2, 3, "major") == (2, 0, 0)

    def test_starts_from_zero(self):
        assert compute_next_version(0, 0, 0, "patch") == (0, 0, 1)

    def test_unknown_bump_type_raises(self):
        with pytest.raises(ValueError):
            compute_next_version(0, 0, 0, "sideways")


class TestFormatDatetimeString:
    def test_matches_spec_example(self):
        dt = datetime(2026, 7, 22, 10, 54, 33)
        assert format_datetime_string(dt) == "220726105433"

    def test_zero_pads_all_components(self):
        dt = datetime(2026, 1, 2, 3, 4, 5)
        assert format_datetime_string(dt) == "020126030405"

    def test_always_twelve_characters(self):
        dt = datetime(2026, 12, 31, 23, 59, 59)
        assert len(format_datetime_string(dt)) == 12


class TestBuildFullVersionString:
    def test_matches_spec_example(self):
        assert build_full_version_string("DEV", 7, 14, 27, "220726105433") == "DEV.7.14.27.220726105433"

    def test_format_is_dot_separated_five_parts(self):
        result = build_full_version_string("PROD", 1, 0, 0, "010101010101")
        assert result.split(".") == ["PROD", "1", "0", "0", "010101010101"]


class TestBumpVersion:
    def test_mutates_the_version_in_place_and_returns_the_full_string(self, app):
        with app.app_context():
            version_type = VersionType(name="DEV")
            db.session.add(version_type)
            db.session.flush()
            version = Version(name="svc", version_type_id=version_type.id, major=1, minor=2, patch=3)
            db.session.add(version)
            db.session.commit()

            full_version_string = bump_version(version, "minor", now=datetime(2026, 7, 22, 10, 54, 33))

            assert (version.major, version.minor, version.patch) == (1, 3, 0)
            assert full_version_string == "DEV.1.3.0.220726105433"

    def test_reads_only_the_versions_own_state_no_build_history_lookup(self, app):
        """Unlike the old spec's generate_version() (which queried the last
        BuildVersion of a type), bump_version needs nothing but the Version
        row it's given — confirmed here by there being zero BuildBatch/
        ImageBuild rows in existence at all.
        """
        with app.app_context():
            version_type = VersionType(name="STAGING")
            db.session.add(version_type)
            db.session.flush()
            version = Version(name="svc2", version_type_id=version_type.id)
            db.session.add(version)
            db.session.commit()

            full_version_string = bump_version(version, "patch", now=datetime(2026, 1, 1, 0, 0, 0))
            assert full_version_string == "STAGING.0.0.1.010126000000"

    def test_major_bump_resets_minor_and_patch(self, app):
        with app.app_context():
            version_type = VersionType(name="PROD")
            db.session.add(version_type)
            db.session.flush()
            version = Version(name="svc3", version_type_id=version_type.id, major=4, minor=9, patch=2)
            db.session.add(version)
            db.session.commit()

            bump_version(version, "major", now=datetime(2026, 3, 3, 3, 3, 3))
            assert (version.major, version.minor, version.patch) == (5, 0, 0)
