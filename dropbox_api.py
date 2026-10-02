def _log_request(request):
    LOGGER.info("--> %s %s headers=%s body=%s", request.method, request.url, dict(request.headers), request.content)

with httpx.Client(..., event_hooks={"request": [_log_request]}) as client:
