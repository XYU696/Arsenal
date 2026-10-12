# 业务数据库包：对外只暴露访问类与默认路径
from .db import BusinessDB, DEFAULT_DB_PATH

# 限制星号导入可用的符号
__all__ = ["BusinessDB", "DEFAULT_DB_PATH"]