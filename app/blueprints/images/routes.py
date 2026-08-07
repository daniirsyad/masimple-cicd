from flask import jsonify, render_template, request

from app.blueprints.images import images_bp
from app.models import BuildBatch, ImageBuild
from app.services.build.worker import get_batch_progress, get_engine_status
from app.utils.decorators import permission_required

PER_PAGE = 20
LOG_TAIL_LINES = 100

BATCH_STATUS_CHOICES = ("queued", "running", "success", "partial_failure", "failed")


def _human_size(num_bytes):
    if not num_bytes:
        return "—"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _registry_link(image):
    if image.registry_name == "dockerhub" and image.image_tag:
        repository = image.image_tag.rsplit(":", 1)[0]
        return f"https://hub.docker.com/r/{repository}"
    return None


def _log_tail(log_text, max_lines=LOG_TAIL_LINES):
    if not log_text:
        return ""
    return "\n".join(log_text.splitlines()[-max_lines:])


@images_bp.route("/")
@permission_required("image.view")
def list_images():
    query = BuildBatch.query

    status = request.args.get("status") or ""
    if status:
        query = query.filter(BuildBatch.status == status)

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(BuildBatch.created_at.desc()).paginate(
        page=page, per_page=PER_PAGE, error_out=False
    )

    human_sizes = {}
    registry_links = {}
    for batch in pagination.items:
        for image in batch.image_builds:
            human_sizes[image.id] = _human_size(image.image_size)
            registry_links[image.id] = _registry_link(image)

    return render_template(
        "images/list.html",
        pagination=pagination,
        batches=pagination.items,
        selected_status=status,
        status_choices=BATCH_STATUS_CHOICES,
        human_sizes=human_sizes,
        registry_links=registry_links,
    )


@images_bp.route("/status")
@permission_required("image.view")
def status():
    engine_status = get_engine_status()
    running = engine_status["running"]

    running_payload = None
    if running is not None:
        running_payload = {
            "batch_id": str(running.batch_id),
            "full_version_string": running.batch.full_version_string,
            "builder": running.builder.name,
            "log_tail": _log_tail(running.build_log),
            "progress": get_batch_progress(running.batch_id),
        }

    queue = [
        {
            "batch_id": str(build.batch_id),
            # Not yet assigned — the Version bump only happens once the
            # worker actually claims a batch's first image (see worker.py).
            "full_version_string": build.batch.full_version_string or "pending",
            "builder": build.builder.name,
            "position": index + 1,
        }
        for index, build in enumerate(engine_status["queued"])
    ]

    return jsonify({"busy": engine_status["busy"], "running": running_payload, "queue": queue})
