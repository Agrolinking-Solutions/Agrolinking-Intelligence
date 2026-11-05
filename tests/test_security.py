"""
Security tests - pinned installs, the uptime check and the database check.
Run with the rest: pytest tests/ -v
"""

import os, re, sys, glob, importlib.util
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "pipeline", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Pinned installs (Nov 2026: every package was "this version or newer") ────

def test_every_package_and_action_is_pinned():
    req = [l.split("#")[0].strip() for l in open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8")]
    loose = [l for l in req if l and "==" not in l]
    assert not loose, f"requirements.txt entries without an exact version: {loose}"
    pinned = {l.split("==")[0].lower().replace("_", "-") for l in open(os.path.join(ROOT, "constraints.txt"))
              if "==" in l and not l.startswith("#")}
    missing = [l.split("==")[0] for l in req if l and l.split("==")[0].lower().replace("_", "-") not in pinned]
    assert not missing, f"in requirements.txt but not constraints.txt: {missing}"
    for wf in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")):
        text = open(wf, encoding="utf-8").read()
        for line in re.findall(r"pip install[^\n]*", text):
            assert "-c constraints.txt" in line, f"{os.path.basename(wf)}: unpinned install: {line}"
        for ref in re.findall(r"uses:\s*(\S+)", text):
            assert re.search(r"@[0-9a-f]{40}$", ref), f"{os.path.basename(wf)}: action not pinned to a commit: {ref}"


# ── Uptime check ─────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)


def fake_get(health=(200, '{"status": "operational"}'), site=(200, "<html>"), as_of="2026-11-04"):
    def get(url):
        if url.endswith("/health"):
            return health[0], health[1], None if health[0] else "ConnectionError"
        if url.endswith("/commodities"):
            return 200, '{"as_of": "%s"}' % as_of, None
        return site[0], site[1], None if site[0] else "ConnectTimeout"
    return get


def test_uptime_all_well_and_api_down():
    u = load("uptime_check")
    run = lambda: ("2026-11-04", NOW.timestamp() - 3 * 3600)
    ok = u.run_checks(NOW, fake_get(), run)
    assert all(r[1] for r in ok) and u.plan(ok, None, NOW) is None
    down = u.run_checks(NOW, fake_get(health=(None, "")), run)
    assert not down[0][1]
    act = u.plan(down, None, NOW)
    assert act["do"] == "open" and "FAILING" in act["body"]
    issue = {"number": 3, "body": act["body"]}
    assert u.plan(down, issue, NOW.replace(hour=12)) is None, "same failures: no rewrite, no email"
    assert u.plan(ok, issue, NOW)["do"] == "close"


def test_uptime_catches_a_server_stuck_on_old_data():
    """The server pulls the repository on every push; a broken deploy freezes it on old forecasts."""
    u = load("uptime_check")
    stale = u.run_checks(NOW, fake_get(as_of="2026-11-03"), lambda: ("2026-11-04", NOW.timestamp() - 3 * 3600))
    assert not stale[2][1] and "isn't picking up" in stale[2][2]
    fresh_run = u.run_checks(NOW, fake_get(as_of="2026-11-03"), lambda: ("2026-11-04", NOW.timestamp() - 600))
    assert fresh_run[2][1], "a run committed 10 minutes ago may not be deployed yet"


# ── Database check ───────────────────────────────────────────────────────────

def db_report(**over):
    r = {"encrypted": True, "tls": "TLSv1.3 TLS_AES_256_GCM_SHA384", "ssl_setting": "require", "ssl_required": True,
         "login": {"superuser": False, "can_create_roles": False, "can_create_databases": False,
                   "bypasses_row_security": False, "replication": False},
         "password_storage": "scram-sha-256",
         "tables": {"prices": {"owner": False, "rights": ["SELECT", "INSERT"]}, "users": {"owner": False, "rights": []}}}
    r.update(over)
    return r


def test_db_check_flags_what_matters():
    d = load("db_security_check")
    assert d.findings(db_report()) == []
    f = d.findings(db_report(encrypted=False, login={**db_report()["login"], "superuser": True}))
    assert "NOT encrypted" in f[0] and "SUPERUSER" in f[1]
    assert "would accept an unencrypted" in d.findings(db_report(ssl_required=False, ssl_setting="prefer"))[0]
    user_data = db_report(tables={"users": {"owner": False, "rights": ["SELECT"]}})
    assert any("user data" in x for x in d.findings(user_data))


def test_db_check_never_reveals_the_connection(monkeypatch):
    d = load("db_security_check")
    monkeypatch.setenv("TSDB_URL", "postgresql://admin:s3cret@db.example.com:5432/pis?sslmode=require")
    monkeypatch.delenv("PGSSLMODE", raising=False)
    mode, required = d.ssl_required_by_setting()
    assert (mode, required) == ("require", True)
    src = open(os.path.join(ROOT, "pipeline", "db_security_check.py"), encoding="utf-8").read()
    assert "inet_server_addr()" in src and "current_user" in src
    assert not re.search(r"logger\.\w+\(.*TSDB_URL", src), "never log the connection string"


def test_monitored_routes_answer_head():
    """UptimeRobot's HTTP check sends HEAD; a GET-only route answered 405 and showed as down (Nov 2026)."""
    from fastapi.testclient import TestClient
    import api
    c = TestClient(api.app)
    for path in ("/", "/health", "/commodities"):
        r = c.head(path)
        assert r.status_code == 200 and r.content == b"", path
