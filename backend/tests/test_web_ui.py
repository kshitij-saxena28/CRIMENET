"""The web UI is static files served by the API: check serving, CSP, and that the UI only calls real endpoints."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def test_index_and_assets_are_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert '<script type="module" src="/static/js/main.js">' in r.text
    for path in ["/static/js/main.js", "/static/style.css", "/static/vendor/cytoscape.min.js", "/static/vendor/leaflet.js"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/static/../backend/app/main.py").status_code in (400, 404)


def test_csp_forbids_inline_and_remote_scripts(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> would be blocked by CSP"
    assert not re.search(r"\son\w+\s*=", html), "inline event handlers would be blocked by CSP"


def test_ui_only_calls_existing_endpoints(client):
    """Every literal API path used by the JS must exist as a route (catches typos and stale endpoints)."""
    from backend.app.main import app
    routes = [re.sub(r"\{[^}]+\}", "X", p) for p in app.openapi()["paths"]]
    pattern = re.compile(r"""api\(\s*[`'"](/[^`'"?]*)""")

    def matches(path, route):
        a, b = path.split("/"), route.split("/")
        return len(a) == len(b) and all(x == y or "X" in (x, y) for x, y in zip(a, b))

    missing = set()
    for f in (WEB / "js").glob("*.js"):
        for m in pattern.finditer(f.read_text(encoding="utf-8")):
            path = re.sub(r"\$\{[^}]*\}", "X", m.group(1))
            if path.endswith("/"):  # '/x/' + id  (string concatenation)
                path += "X"
            if not any(matches(path, r) for r in routes):
                missing.add((f.name, path))
    assert not missing, missing


def test_ui_never_uses_dangerous_dom_sinks():
    for f in (WEB / "js").glob("*.js"):
        text = f.read_text(encoding="utf-8")
        assert "innerHTML" not in text and "outerHTML" not in text and "document.write" not in text and "eval(" not in text, f.name


def test_every_js_file_parses_as_a_module():
    """A syntax error in one page module only shows up when someone opens that page, so parse them all."""
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node is not installed")
    bad = []
    for f in sorted((WEB / "js").glob("*.js")):
        r = subprocess.run([node, "--input-type=module", "--check"], input=f.read_text(encoding="utf-8"), capture_output=True, text=True)
        if r.returncode:
            bad.append(f"{f.name}: {r.stderr.strip().splitlines()[0] if r.stderr.strip() else 'error'}")
    assert not bad, bad


def test_index_links_only_existing_css():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for href in re.findall(r'href="/static/([^"]+\.css)"', html):
        assert (WEB / href).exists(), href
