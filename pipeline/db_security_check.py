"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Database security check - read-only: reports, changes nothing

What the pipeline's Postgres login can do was never written down (Nov 2026
review). This connects the same way the pipeline does and reports:

  encryption   is this connection encrypted (SSL), and does the connection
               setting insist on it (otherwise a misconfigured server would
               silently fall back to plain text)
  rights       is the login a superuser, can it create roles or databases,
               does it own the tables, and what it may do on each table
  passwords    how the server stores passwords

It never prints the server address, user name or password. Output: the run
page summary and outputs/logs/db_security.json. The fix for a too-powerful
login is scripts/migration/least_privilege.sql (run by the database admin).

Skips cleanly when no database is configured. Never fails the run.
Run: python pipeline/db_security_check.py
"""

import os, sys, json
from datetime import date
from urllib.parse import urlparse, parse_qs

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import PATHS

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

OUT_PATH = os.path.join(PATHS["logs_dir"], "db_security.json")
TABLES = ["prices", "forecasts", "commodities", "locations", "intelligence_metrics", "market_index",
          "web_price_estimates", "users", "alerts", "portfolio_holdings"]
RIGHTS = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]
REQUIRED_SSLMODES = {"require", "verify-ca", "verify-full"}


def ssl_required_by_setting():
    """Does the connection setting refuse an unencrypted connection? (PGSSLMODE or sslmode= in TSDB_URL)"""
    mode = os.environ.get("PGSSLMODE")
    url = os.environ.get("TSDB_URL") or ""
    if not mode and url:
        q = parse_qs(urlparse(url).query)
        mode = (q.get("sslmode") or [None])[0]
    return (mode or "prefer (the default)"), (mode in REQUIRED_SSLMODES)


def inspect(cur):
    r = {}
    cur.execute("SHOW server_version")
    r["server_version"] = cur.fetchone()[0]
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    row = cur.fetchone()
    r["timescaledb"] = row[0] if row else None
    cur.execute("SELECT inet_server_addr() IS NULL")
    r["connection"] = "local socket" if cur.fetchone()[0] else "network (TCP)"
    cur.execute("SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
    row = cur.fetchone()
    r["encrypted"] = bool(row and row[0])
    r["tls"] = f"{row[1]} {row[2]}" if row and row[0] else None
    r["ssl_setting"], r["ssl_required"] = ssl_required_by_setting()
    cur.execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication "
                "FROM pg_roles WHERE rolname = current_user")
    sup, crole, cdb, bypass, repl = cur.fetchone()
    r["login"] = {"superuser": sup, "can_create_roles": crole, "can_create_databases": cdb,
                  "bypasses_row_security": bypass, "replication": repl}
    try:
        cur.execute("SHOW password_encryption")
        r["password_storage"] = cur.fetchone()[0]
    except Exception:
        r["password_storage"] = None
    tables = {}
    for t in TABLES:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{t}",))
        if not cur.fetchone()[0]:
            continue
        cur.execute("SELECT pg_get_userbyid(relowner) = current_user FROM pg_class WHERE oid = %s::regclass",
                    (f"public.{t}",))
        owns = cur.fetchone()[0]
        rights = [p for p in RIGHTS if _has(cur, t, p)]
        tables[t] = {"owner": owns, "rights": rights}
    r["tables"] = tables
    return r


def _has(cur, table, right):
    cur.execute("SELECT has_table_privilege(current_user, %s, %s)", (f"public.{table}", right))
    return cur.fetchone()[0]


def findings(r):
    """Plain-language problems, worst first."""
    out = []
    if not r["encrypted"]:
        out.append("The connection is NOT encrypted: prices, user emails and the password travel in plain "
                   "text. Turn on SSL on the database server, then require it (PGSSLMODE=require).")
    elif not r["ssl_required"]:
        out.append(f"The connection is encrypted ({r['tls']}), but the setting ({r['ssl_setting']}) would "
                   "accept an unencrypted one. Set PGSSLMODE=require in the workflows and on the server.")
    lg = r["login"]
    if lg["superuser"]:
        out.append("The pipeline logs in as a SUPERUSER: a leaked password or a bug could read, change or "
                   "drop anything on the server. Give it its own limited login "
                   "(scripts/migration/least_privilege.sql).")
    elif lg["can_create_roles"] or lg["can_create_databases"]:
        out.append("The pipeline's login can create roles or databases, which it never needs.")
    owned = [t for t, v in r["tables"].items() if v["owner"]]
    if owned and not lg["superuser"]:
        out.append(f"The pipeline's login owns {len(owned)} table(s) ({', '.join(owned)}), so it can also "
                   "drop or restructure them.")
    personal = [t for t in ("users", "alerts", "portfolio_holdings") if t in r["tables"] and r["tables"][t]["rights"]]
    if personal:
        out.append(f"The pipeline can read or change user data ({', '.join(personal)}); only the API needs that.")
    if r.get("password_storage") == "md5":
        out.append("Passwords are stored with MD5; scram-sha-256 is the current standard.")
    return out


def run():
    logger.info("=" * 60)
    logger.info("DATABASE SECURITY CHECK (read-only)")
    logger.info("=" * 60)
    if not os.environ.get("TSDB_URL") and not os.environ.get("PGHOST"):
        logger.info("  no database configured - skipped")
        return True
    try:
        import psycopg2
        conn = psycopg2.connect() if os.environ.get("PGHOST") else psycopg2.connect(os.environ["TSDB_URL"])
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            r = inspect(cur)
        conn.close()
    except Exception as e:
        # the message can include the host name, so only its type is shown
        logger.warning(f"  could not check the database this run: {type(e).__name__}")
        return True
    r["checked"] = date.today().isoformat()
    r["findings"] = findings(r)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(r, f, indent=2)
    lg = r["login"]
    lines = [f"Postgres {r['server_version']}" + (f", TimescaleDB {r['timescaledb']}" if r["timescaledb"] else ""),
             f"Encrypted: {'yes, ' + r['tls'] if r['encrypted'] else 'NO'} (setting: {r['ssl_setting']})",
             f"Superuser: {'YES' if lg['superuser'] else 'no'}; can create roles: {lg['can_create_roles']}; "
             f"databases: {lg['can_create_databases']}",
             "Table rights: " + "; ".join(f"{t} {'/'.join(v['rights']) or 'none'}{' (owner)' if v['owner'] else ''}"
                                          for t, v in r["tables"].items())]
    for l in lines:
        logger.info(f"  {l}")
    for f in r["findings"]:
        logger.warning(f"  ! {f}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n### Database security\n" + "\n".join(f"- {l}" for l in lines) + "\n")
            f.write(("\n**To fix:**\n" + "\n".join(f"- {x}" for x in r["findings"]) + "\n") if r["findings"]
                    else "\nNo problems found.\n")
    return True


if __name__ == "__main__":
    run()
    sys.exit(0)
