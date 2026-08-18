from app.services.build.engine import DockerBuildEngine
from app.utils.system_config import get_system_config

# "kaniko" is intentionally not registered here — KanikoBuildEngine
# (app/services/build/engine.py) runs kaniko-executor as a raw subprocess of
# this app, which shares this app's own root filesystem with it (kaniko has
# no daemon/chroot of its own and extracts each FROM image's layers directly
# onto whatever filesystem the executor process is running in). In practice
# this corrupted a live app container mid-build (overwrote /etc/os-release,
# dropped Alpine binaries into it). Disabled here and in
# app/blueprints/system_config/forms.py's BUILD_ENGINE_CHOICES until it's
# rewritten to run kaniko in its own throwaway container instead (e.g. via
# `docker run` over the already-available docker.sock, matching how
# DockerBuildEngine itself shells out safely).
_ENGINES = {
    "docker": DockerBuildEngine,
}


def get_build_engine():
    """Build a BuildEngine for the configured `SystemConfig.build_engine`."""
    engine_type = get_system_config().build_engine
    try:
        engine_cls = _ENGINES[engine_type]
    except KeyError:
        raise ValueError(
            f"Build engine '{engine_type}' is not implemented. "
            f"Supported engines: {', '.join(_ENGINES)}."
        ) from None
    return engine_cls()
