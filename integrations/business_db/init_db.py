import argparse  # 命令行参数解析
import sqlite3  # SQLite 连接
from pathlib import Path  # 路径对象

from .db import DEFAULT_DB_PATH  # 默认数据库路径


ROOT = Path(__file__).resolve().parents[2]  # 项目根目录
DEFAULT_SCHEMA_PATH = ROOT / "data" / "business" / "schema.sql"  # base 建表脚本路径
DEFAULT_SEED_PATH = ROOT / "data" / "business" / "seed.sql"  # base 种子数据路径
ECOMMERCE_V2_SCHEMA_PATH = ROOT / "data" / "business" / "schema_v2.sql"  # ecommerce_v2 建表脚本路径
ECOMMERCE_V2_SEED_PATH = ROOT / "data" / "business" / "seed_v2.sql"  # ecommerce_v2 种子数据路径
ECOMMERCE_V2_DB_PATH = ROOT / "data" / "business" / "echomind_ecommerce_v2.db"  # ecommerce_v2 库文件路径


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    参数：
        无

    返回：
        解析后的参数对象（argparse.Namespace）
    """
    # 创建参数解析器
    parser = argparse.ArgumentParser(description="Initialize EchoMind business SQLite database.")
    # 预设档位：base 最小业务库 / ecommerce_v2 售后业务库
    parser.add_argument(
        "--profile",
        choices=["base", "ecommerce_v2"],  # 可选档位
        default="base",  # 默认 base
        help="Use built-in schema/seed presets. `base` is the current minimal business DB; `ecommerce_v2` is the Phase 1 after-sales business DB.",
    )
    # 数据库文件路径
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Path to SQLite DB file.")
    # 建表脚本路径
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH), help="Path to schema.sql.")
    # 种子数据路径
    parser.add_argument("--seed", default=str(DEFAULT_SEED_PATH), help="Path to seed.sql.")
    # 仅建表不灌数据
    parser.add_argument("--skip-seed", action="store_true", help="Create schema only.")
    # 解析并返回参数
    return parser.parse_args()


def main() -> int:
    """
    程序入口：按档位初始化数据库并灌入种子数据。

    参数：
        无

    返回：
        进程退出码（0 成功）
    """
    # 解析命令行参数
    args = parse_args()

    # 按档位选择默认路径
    if args.profile == "ecommerce_v2":
        # ecommerce_v2 档位的库文件路径
        default_db_path = ECOMMERCE_V2_DB_PATH
        # ecommerce_v2 档位的建表脚本
        default_schema_path = ECOMMERCE_V2_SCHEMA_PATH
        # ecommerce_v2 档位的种子数据
        default_seed_path = ECOMMERCE_V2_SEED_PATH
    else:
        # base 档位的库文件路径
        default_db_path = DEFAULT_DB_PATH
        # base 档位的建表脚本
        default_schema_path = DEFAULT_SCHEMA_PATH
        # base 档位的种子数据
        default_seed_path = DEFAULT_SEED_PATH

    # 允许用户继续通过 --db-path / --schema / --seed 覆盖 profile 默认值。
    # 用户显式传了对应的默认值时用档位默认值，否则用用户传的值
    db_path = Path(args.db_path if args.db_path != str(DEFAULT_DB_PATH) else default_db_path).resolve()
    # 建表脚本路径（同上覆盖逻辑）
    schema_path = Path(args.schema if args.schema != str(DEFAULT_SCHEMA_PATH) else default_schema_path).resolve()
    # 种子数据路径（同上覆盖逻辑）
    seed_path = Path(args.seed if args.seed != str(DEFAULT_SEED_PATH) else default_seed_path).resolve()

    # 确保数据库目录存在
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 读取建表脚本文本
    schema_sql = schema_path.read_text(encoding="utf-8")
    # 跳过种子时为空串，否则读取种子数据文本
    seed_sql = "" if args.skip_seed else seed_path.read_text(encoding="utf-8")

    # 在事务内顺序执行建表和灌数据
    with sqlite3.connect(db_path) as conn:
        # 执行建表脚本
        conn.executescript(schema_sql)
        # 有种子数据时执行种子脚本
        if seed_sql:
            conn.executescript(seed_sql)
        # 提交事务
        conn.commit()

    # 打印初始化完成的库文件位置
    print(f"Initialized business database at {db_path}")
    # 未跳过种子数据时打印数据来源
    if not args.skip_seed:
        print(f"Seeded data from {seed_path}")
    # 成功退出
    return 0


if __name__ == "__main__":
    # 直接运行脚本时以返回码退出
    raise SystemExit(main())