"""Serve the Footprints UI alongside the local Forkit Core API."""

import argparse
import re
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import Body, Request
from fastapi.responses import FileResponse, JSONResponse
from forkit.server import ServerSettings, create_app
from starlette.middleware.trustedhost import TrustedHostMiddleware

from collector import collect, requested_names
from repo_passports import PassportRequestError, create_repo_agent_passport, list_passports
from repos import scan_repositories
from tool_passports import create_tool_passport


UI_PATHS = {"/footprints/", "/footprints/discovery.js", "/footprints/passport.js", "/footprints/repositories.js", "/footprints/ui.js", "/footprints/passports.js", "/readyz"}
MAX_REQUESTED_NAMES = 200
# Browsers must check for a newer page and scripts on every load; otherwise they
# can keep showing an old version of the UI after an update.
NO_CACHE = {"Cache-Control": "no-cache"}


def _requested(scan_request: dict, key: str) -> list:
    values = scan_request.get(key)
    return requested_names(values[:MAX_REQUESTED_NAMES]) if isinstance(values, list) else []


async def restrict_core_requests(request: Request, call_next):
    path = request.url.path
    if request.method == "GET" and (
        path in UI_PATHS or re.fullmatch(r"/passports/[0-9a-f]{64}", path)
    ):
        response = await call_next(request)
        if path.startswith("/passports/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    if request.method != "POST" or (
        path not in ("/models", "/discovery/scan", "/repos/scan", "/passports/repo-agent", "/passports/list", "/passports/tool")
        and not re.fullmatch(r"/verify/[0-9a-f]{64}", path)
    ):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
        return JSONResponse({"detail": "Use the local Footprints page"}, status_code=403)

    # Requiring JSON means another website cannot trigger these with a plain form post.
    if path in ("/models", "/discovery/scan", "/repos/scan", "/passports/repo-agent", "/passports/list", "/passports/tool"):
        what = "model record" if path == "/models" else "request"
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return JSONResponse({"detail": f"Expected a JSON {what}"}, status_code=415)
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"detail": f"Invalid JSON {what}"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"detail": f"Expected a JSON {what} object"}, status_code=422)
        if path == "/models" and "id" in payload:
            return JSONResponse({"detail": "Core assigns the passport ID"}, status_code=422)

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def create_footprints_app(registry_root: Path, collect_evidence=collect, scan_repos=None):
    app = create_app(ServerSettings(registry_root=registry_root))
    app.middleware("http")(restrict_core_requests)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    ui_root = Path(__file__).resolve().parent

    @app.get("/footprints/", include_in_schema=False)
    def footprints_index():
        return FileResponse(ui_root / "index.html", headers={
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            ),
            **NO_CACHE,
        })

    @app.get("/footprints/discovery.js", include_in_schema=False)
    def discovery_script():
        return FileResponse(ui_root / "discovery.js", media_type="text/javascript", headers=NO_CACHE)

    @app.get("/footprints/passport.js", include_in_schema=False)
    def passport_script():
        return FileResponse(ui_root / "passport.js", media_type="text/javascript", headers=NO_CACHE)

    @app.get("/footprints/passports.js", include_in_schema=False)
    def passports_script():
        return FileResponse(ui_root / "passports.js", media_type="text/javascript", headers=NO_CACHE)

    @app.get("/footprints/ui.js", include_in_schema=False)
    def ui_script():
        return FileResponse(ui_root / "ui.js", media_type="text/javascript", headers=NO_CACHE)

    @app.get("/footprints/repositories.js", include_in_schema=False)
    def repositories_script():
        return FileResponse(ui_root / "repositories.js", media_type="text/javascript", headers=NO_CACHE)

    # Runs only when the user presses "Scan this device". Read-only, local, and
    # the response holds identifiers only (see collector.py for the allowlist).
    # The page sends the command and package names its catalog knows; nothing else
    # is looked up in those places.
    @app.post("/discovery/scan", include_in_schema=False)
    def discovery_scan(scan_request: dict = Body(default_factory=dict)):
        return collect_evidence(cli_names=_requested(scan_request, "cli"),
                                package_names=_requested(scan_request, "packages"))

    # Runs only when the user presses "Scan repositories". Saves the result to a
    # local history file in the Footprints registry folder; nothing leaves the device.
    history_path = registry_root / "repos-history.json"
    scan = scan_repos or (lambda: scan_repositories(history_path))

    @app.post("/repos/scan", include_in_schema=False)
    def repositories_scan(_scan_request: dict = Body(default_factory=dict)):
        return scan()

    # Agent passports for a repository's agent setup (see repo_passports.py).
    @app.post("/passports/repo-agent", include_in_schema=False)
    def repo_agent_passport(passport_request: dict = Body(default_factory=dict)):
        try:
            return create_repo_agent_passport(registry_root, history_path, passport_request)
        except PassportRequestError as error:
            return JSONResponse({"detail": str(error), **error.extra}, status_code=error.status)

    @app.post("/passports/tool", include_in_schema=False)
    def tool_passport(passport_request: dict = Body(default_factory=dict)):
        try:
            return create_tool_passport(registry_root, passport_request)
        except PassportRequestError as error:
            return JSONResponse({"detail": str(error), **error.extra}, status_code=error.status)

    @app.post("/passports/list", include_in_schema=False)
    def passports_list(_list_request: dict = Body(default_factory=dict)):
        return list_passports(registry_root)

    return app


class BrowserServer(uvicorn.Server):
    """Open Footprints after the server has bound its loopback address."""

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if not self.started:
            return
        port = self.config.port or self.servers[0].sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/footprints/"
        try:
            opened = webbrowser.open(url)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            print(f"Open {url} in your browser.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="Open Footprints after startup.")
    parser.add_argument(
        "--registry-root", type=Path, default=Path.home() / ".forkit" / "footprints"
    )
    options = parser.parse_args()
    config = uvicorn.Config(
        create_footprints_app(options.registry_root.expanduser().resolve()),
        host="127.0.0.1",
        port=options.port,
    )
    server = BrowserServer(config) if options.open_browser else uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
