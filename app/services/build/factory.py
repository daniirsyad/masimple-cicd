from app.services.build.engine import DockerBuildEngine, KanikoBuildEngine
from app.utils.system_config import get_system_config

_ENGINES = {
    "docker": DockerBuildEngine,
    "kaniko": KanikoBuildEngine,
}


def get_build_engine():
    """Build a BuildEngine for the configured `SystemConfig.build_engine`."""
    config = get_system_config()
    engine_type = config.build_engine
    try:
        engine_cls = _ENGINES[engine_type]
    except KeyError:
        raise ValueError(
            f"Build engine '{engine_type}' is not implemented. "
            f"Supported engines: {', '.join(_ENGINES)}."
        ) from None
    if engine_cls is DockerBuildEngine:
        return DockerBuildEngine(docker_host=config.docker_host)
    return engine_cls()
