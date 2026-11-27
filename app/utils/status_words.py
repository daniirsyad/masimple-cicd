# Run/build/deploy status codes in the words people read. Twin of
# STATUS_WORDS in static/js/workflows.js (the run page re-renders by JS).
STATUS_WORDS = {
    "success": "Succeeded",
    "failed": "Failed",
    "partial_failure": "Partly failed",
    "completed_with_failures": "Finished with failures",
    "running": "Running",
    "queued": "Queued",
    "skipped": "Skipped",
    "awaiting_review": "Waiting for review",
}


def status_word(status):
    """Jinja filter: `{{ run.status|status_word }}`; unknown codes pass through."""
    return STATUS_WORDS.get(status, status)
