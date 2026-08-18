import pytest

from app.services.build.engine import DockerBuildEngine
from app.services.build.factory import get_build_engine
from app.utils.system_config import get_system_config


class TestGetBuildEngine:
    def test_defaults_to_docker(self, app):
        with app.app_context():
            engine = get_build_engine()
            assert isinstance(engine, DockerBuildEngine)

    def test_raises_for_kaniko_currently_disabled(self, app):
        """"kaniko" is deliberately left out of _ENGINES — see its comment
        in app/services/build/factory.py: running kaniko-executor as a bare
        subprocess of this app (no isolated container of its own) let a
        build corrupt the live app container's own filesystem. Disabled
        until it's rewritten to run kaniko in its own throwaway container.
        """
        with app.app_context():
            from app.extensions import db

            config = get_system_config()
            config.build_engine = "kaniko"
            db.session.commit()

            with pytest.raises(ValueError):
                get_build_engine()

    def test_raises_for_unknown_engine(self, app):
        with app.app_context():
            from app.extensions import db

            config = get_system_config()
            config.build_engine = "buildah"
            db.session.commit()

            with pytest.raises(ValueError):
                get_build_engine()
