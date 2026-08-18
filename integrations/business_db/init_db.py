import argparse
import sqlite3
from pathlib import Path

from .db import DEFAULT_DB_PATH


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = ROOT / "data" / "business" / "schema.sql"
DEFAULT_SEED_PATH = ROOT / "data" / "business" / "seed.sql"
ECOMMERCE_V2_SCHEMA_PATH = ROOT / "data" / "business" / "schema_v2.sql"
ECOMMERCE_V2_SEED_PATH = ROOT / "data" / "business" / "seed_v2.sql"
ECOMMERCE_V2_DB_PATH = ROOT / "data" / "business" / "echomind_ecommerce_v2.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize EchoMind business SQLite database.")
    parser.add_argument(
        "--profile",
        choices=["base", "ecommerce_v2"],
        default="base",
        help="Use built-in schema/seed presets. `base` is the current minimal business DB; `ecommerce_v2` is the Phase 1 after-sales business DB.",
    )
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Path to SQLite DB file.")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH), help="Path to schema.sql.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED_PATH), help="Path to seed.sql.")
    parser.add_argument("--skip-seed", action="store_true", help="Create schema only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.profile == "ecommerce_v2":
        default_db_path = ECOMMERCE_V2_DB_PATH
        default_schema_path = ECOMMERCE_V2_SCHEMA_PATH
        default_seed_path = ECOMMERCE_V2_SEED_PATH
    else:
        default_db_path = DEFAULT_DB_PATH
        default_schema_path = DEFAULT_SCHEMA_PATH
        default_seed_path = DEFAULT_SEED_PATH

    # 允许用户继续通过 --db-path / --schema / --seed 覆盖 profile 默认值。
    db_path = Path(args.db_path if args.db_path != str(DEFAULT_DB_PATH) else default_db_path).resolve()
    schema_path = Path(args.schema if args.schema != str(DEFAULT_SCHEMA_PATH) else default_schema_path).resolve()
    seed_path = Path(args.seed if args.seed != str(DEFAULT_SEED_PATH) else default_seed_path).resolve()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = schema_path.read_text(encoding="utf-8")
    seed_sql = "" if args.skip_seed else seed_path.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
        if seed_sql:
            conn.executescript(seed_sql)
        conn.commit()

    print(f"Initialized business database at {db_path}")
    if not args.skip_seed:
        print(f"Seeded data from {seed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
