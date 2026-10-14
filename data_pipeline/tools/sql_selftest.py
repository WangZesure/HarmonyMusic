"""Run db/schema_v1.sql + db/seed_v1.sql + db/check_v1.sql against a TEMP database.

Never touches `mymusic`. Creates `<name>_selftest`, imports, runs the checks,
prints each result, then drops it.  between12.md step 4.

    python data_pipeline/tools/sql_selftest.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

import pymysql  # noqa: E402
from pymysql.constants import CLIENT  # noqa: E402

TEMP = "mymusic_selftest"
FLAG = CLIENT.MULTI_STATEMENTS


def connect(database=None):
    kw = dict(config.db_config())
    kw.pop("database", None)
    if database:
        kw["database"] = database
    return pymysql.connect(**kw, client_flag=FLAG)


def run_script(conn, sql: str, collect=False):
    results = []
    with conn.cursor() as cur:
        cur.execute(sql)
        while True:
            if collect and cur.description:
                cols = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    results.append(dict(zip(cols, row)))
            if not cur.nextset():
                break
    return results


def main() -> int:
    schema = (config.SQL_DIR / "schema_v1.sql").read_text(encoding="utf-8").replace("`mymusic`", f"`{TEMP}`")
    seed = (config.SQL_DIR / "seed_v1.sql").read_text(encoding="utf-8")
    check = (config.SQL_DIR / "check_v1.sql").read_text(encoding="utf-8")
    check = re.sub(r"^\s*USE\s+`[^`]+`;", "", check, flags=re.M)

    conn = connect()
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{TEMP}`")
    conn.commit()

    print("importing schema..."); run_script(conn, schema); conn.commit()
    print("importing seed...");  run_script(conn, seed); conn.commit()
    conn.close()

    conn = connect(TEMP)
    rows = run_script(conn, check, collect=True)
    conn.close()

    print("\n=== check_v1 results ===")
    ok = True
    for r in rows:
        name, val = next(iter(r.items()))
        want = name.split("expect_")[-1]
        good = (val >= 1) if want == "ge1" else (val == int(want))
        ok = ok and good
        print(f"  {'OK ' if good else 'BAD'} {name:38} = {val}")

    conn = connect()
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{TEMP}`")
    conn.commit(); conn.close()
    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'} (temp db {TEMP} dropped)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
