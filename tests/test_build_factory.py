import pytest

from app.services.build.engine import DockerBuildEngine, KanikoBuildEngine
from app.services.build.factory import get_build_engine
from app.utils.system_config import get_system_config


class TestGetBuildEngine:
    def test_defaults_to_docker(self, app):
        with app.app_context():
            engine = get_build_engine()
            assert isinstance(engine, DockerBuildEngine)

    def test_kaniko_is_supported(self, app):
        """"kaniko" now runs kaniko-executor as its own Kubernetes Job (see
        KanikoBuildEngine) rather than a bare subprocess of this app, so it
        no longer risks corrupting the live app container's own filesystem
        the way an earlier version did — see app/services/build/engine.py.
        """
        with app.app_context():
            from app.extensions import db

            config = get_system_config()
            config.build_engine = "kaniko"
            db.session.commit()

            engine = get_build_engine()
            assert isinstance(engine, KanikoBuildEngine)

    def test_raises_for_unknown_engine(self, app):
        with app.app_context():
            from app.extensions import db

            config = get_system_config()
            config.build_engine = "buildah"
            db.session.commit()

            with pytest.raises(ValueError):
                get_build_engine()
