"""
EchoMind Skill 加载器。

Skill 是一段可热加载的业务能力说明，用来补充 Agent 的 system prompt。
它适合放置企业话术、处理流程、合规边界、排障 SOP 等需要运营侧快速调整的规则。
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """
    单个 Skill 的标准化表示，屏蔽 Markdown/JSON 等不同文件格式差异。

    无论底层文件是哪种格式，都统一归一化为 name/content/keywords/agents 等字段，
    供 SkillManager 加载、匹配与注入。
    """
    name: str  # Skill 名称（注入 prompt 时作为标题显示）
    description: str  # 一句话说明（注入时展示在标题下）
    content: str  # 具体业务规则 / 话术 / SOP 正文
    path: str  # 源文件路径（用于排障与定位）
    keywords: List[str] = field(default_factory=list)  # 命中关键词（小写匹配；空则全局注入）
    agents: List[str] = field(default_factory=list)  # 适用 Agent 类型白名单（空则适用全部）
    enabled: bool = True  # 是否启用（禁用则完全不注入）

    def matches(self, message: str, agent_type: Optional[str] = None) -> bool:
        """
        判断当前请求是否应该注入这个 Skill。

        - agents 为空：适用于所有 Agent；否则只匹配指定 Agent。
        - keywords 为空：作为全局 Skill 注入；否则只有命中关键词才注入。

        参数：
            message: 用户当前消息
            agent_type: 当前处理请求的 Agent 类型（可选）

        返回：
            True 表示应注入；False 表示应跳过
        """
        # 禁用的 Skill 直接不注入
        if not self.enabled:
            return False

        # 指定了 Agent 白名单但不包含当前 Agent 时不注入
        if self.agents and agent_type and agent_type.lower() not in self.agents:
            return False

        # 无关键词时作为全局 Skill 注入
        if not self.keywords:
            return True

        # 消息转小写便于匹配
        lowered = (message or "").lower()
        # 任一关键词（小写）命中消息即注入
        return any(keyword.lower() in lowered for keyword in self.keywords)

    def to_prompt_block(self, max_chars: int = 3200) -> str:
        """
        格式化为可直接拼入 system prompt 的文本块，并限制单个 Skill 长度。

        参数：
            max_chars: 单个 Skill 注入内容的最大字符数（默认 3200）

        返回：
            Markdown 格式的 prompt 段落
        """
        # 去除正文首尾空白
        body = self.content.strip()
        # 超长时截断并追加省略标记
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n..."  # 截断 + 尾部省略
        # 有描述时追加说明行
        description = f"\n说明: {self.description}" if self.description else ""
        # 拼成标题 + 说明 + 正文的段落
        return f"### {self.name}{description}\n{body}"

    def to_summary(self) -> Dict[str, Any]:
        """
        返回 API 可序列化摘要，避免把完整长文本默认暴露给健康检查。

        返回：
            序列化摘要字典（不含完整正文，仅含字符数）
        """
        return {
            "name": self.name,  # 名称
            "description": self.description,  # 说明
            "path": self.path,  # 源文件路径
            "keywords": self.keywords,  # 关键词
            "agents": self.agents,  # 适用 Agent
            "enabled": self.enabled,  # 启用标记
            "content_chars": len(self.content),  # 正文长度（不暴露正文内容）
        }


class SkillManager:
    """
    从目录中发现、解析并管理 Skills。

    支持两种常用结构：
      1. skills/refund/SKILL.md（目录规范文件，含 front matter）
      2. skills/refund.json / skills/refund.md / skills/refund.txt（单文件 Skill）
    """

    SUPPORTED_SUFFIXES = {".md", ".txt", ".json"}  # 支持的单文件后缀集合

    def __init__(self, root_dir: str, max_prompt_chars: int = 5000):
        """
        初始化 Skill 管理器。

        参数：
            root_dir: Skill 文件所在根目录
            max_prompt_chars: 构建 prompt 时的总长度上限（默认 5000）
        """
        # 规范化根目录路径（展开 ~ 并解析为绝对路径）
        self.root_dir = Path(root_dir).expanduser().resolve()
        # 保存 prompt 总长度上限
        self.max_prompt_chars = max_prompt_chars
        # 已加载的 Skill 列表
        self._skills: List[Skill] = []
        # 加载过程中的错误列表
        self._errors: List[str] = []

    @property
    def skills(self) -> List[Skill]:
        """返回已加载 Skill 的副本（防止外部直接修改内部列表）。"""
        # 复制列表返回
        return list(self._skills)

    @property
    def errors(self) -> List[str]:
        """返回加载错误列表的副本。"""
        # 复制列表返回
        return list(self._errors)

    def load(self) -> List[Skill]:
        """重新扫描目录并加载 Skills；单个文件失败不会影响其他 Skill 生效。"""
        # 初始化本次加载结果
        loaded: List[Skill] = []
        # 初始化本次加载错误
        errors: List[str] = []

        # 根目录不存在时跳过加载
        if not self.root_dir.exists():
            # 记录跳过日志
            logger.info(f"Skill 目录不存在，跳过加载: {self.root_dir}")
            # 清空内部状态
            self._skills = []  # 清空已加载列表
            self._errors = []  # 清空错误列表
            return []

        # 遍历发现的文件逐个加载
        for path in self._discover_files(self.root_dir):
            try:
                # 加载单个文件
                skill = self._load_file(path)
                # 成功且非空时记录
                if skill is not None:
                    loaded.append(skill)
            except Exception as ex:
                # 单个文件失败不影响其它 Skill
                msg = f"{path}: {ex}"  # 组装错误信息
                errors.append(msg)  # 记录错误
                logger.warning(f"Skill 加载失败: {msg}")

        # 更新内部状态
        self._skills = loaded  # 保存本次加载结果
        self._errors = errors  # 保存本次加载错误
        # 控制台输出加载结果
        self._log_loaded_skills()
        # 返回加载到的 Skill 列表
        return self.skills

    def reload(self) -> List[Skill]:
        """运行时热加载入口，供 API 调用。"""
        # 重新执行完整加载流程
        return self.load()

    def prompt_for(self, message: str, agent_type: Optional[str] = None) -> str:
        """
        为当前用户请求构建 Skill prompt。

        只注入匹配的 Skill，并按总长度截断，避免挤占主对话上下文。

        参数：
            message: 用户当前消息（用于关键词匹配）
            agent_type: 当前 Agent 类型（用于 Agent 白名单匹配）

        返回：
            拼好的 Skill prompt 文本；无命中时返回空字符串

        # 1. 遍历所有 Skill，调用 matches() 过滤
        #    - enabled=False → 跳过
        #    - agents 白名单不匹配 → 跳过
        #    - keywords 非空且消息不含任何关键词 → 跳过
        #    - keywords 为空 → 全局 Skill（每次都注入）
        # 2. 每个匹配的 Skill 调 to_prompt_block() 格式化
        # 3. 累计长度不超过 max_prompt_chars（默认 5000），超限截断
        # 4. 拼接前缀说明 + 各段落，返回完整 prompt 文本
        """
        # 已注入的 prompt 段落列表
        blocks: List[str] = []
        # 命中的 Skill 及对应关键词列表
        matched: List[tuple[Skill, List[str]]] = []
        # 剩余可用长度（从总上限开始扣减）
        remaining = self.max_prompt_chars
        # 消息转小写用于关键词匹配
        lowered_message = (message or "").lower()

        # 遍历已加载的全部 Skill
        for skill in self._skills:
            # 不匹配当前请求的跳过
            if not skill.matches(message, agent_type):
                continue
            # 收集该 Skill 命中的关键词（用于日志展示）
            matched_keywords = [
                keyword for keyword in skill.keywords
                if keyword.lower() in lowered_message
            ]
            # 生成单个 Skill 的 prompt 段落
            block = skill.to_prompt_block()  #name，description，body
            # 段落超出剩余长度时截断
            if len(block) > remaining:
                block = block[:remaining].rstrip() + "\n..."  # 截断 + 省略标记
            # 记录段落
            blocks.append(block)
            # 记录命中信息
            matched.append((skill, matched_keywords))
            # 扣减剩余长度
            remaining -= len(block)
            # 剩余空间不足时停止注入
            if remaining <= 0:
                break

        # 无任何命中时返回空字符串
        if not blocks:
            # 记录未命中调试日志
            logger.debug(
                "Skills 未命中: agent=%s message=%r",  # 日志格式：Agent + 消息前 80 字符
                agent_type or "all",  # Agent 类型（空用 all）
                (message or "")[:80],  # 消息截断
            )
            return ""

        # 组装命中明细用于日志
        detail = "; ".join(
            f"{skill.name}(keywords={', '.join(keywords) if keywords else 'all'})"  # Skill 名 + 命中关键词
            for skill, keywords in matched
        )
        # 记录注入日志
        logger.info(
            "Skills 已注入: agent=%s matched=%s message=%r",  # 日志格式
            agent_type or "all",  # Agent 类型
            detail,  # 命中明细
            (message or "")[:80],  # 消息截断
        )

        # 拼接所有段落成完整 prompt（含使用说明前缀）
        return (
            "以下是当前请求可用的 EchoMind Skills。"  # 说明前缀
            "请优先遵循这些业务规则；如果与系统角色冲突，以系统角色和安全边界为准。\n\n"  # 优先级说明
            + "\n\n".join(blocks)  # 用空行拼接各 Skill 段落
        )

    def summary(self) -> Dict[str, Any]:
        """返回 Skill 管理器状态，用于 /skills 接口和排障。"""
        # 组装管理器状态摘要
        return {
            "root_dir": str(self.root_dir),  # 根目录
            "count": len(self._skills),  # Skill 数量
            "skills": [skill.to_summary() for skill in self._skills],  # 各 Skill 摘要
            "errors": self.errors,  # 加载错误列表
        }

    def _log_loaded_skills(self) -> None:
        """在控制台输出醒目的 Skill 加载结果，方便启动和热加载时确认生效状态。"""
        # 初始化日志行（标题 + 目录 + 数量）
        lines = [
            "",
            "================ EchoMind Skills Loaded ================",
            f"目录: {self.root_dir}",  # 根目录
            f"数量: {len(self._skills)}",  # Skill 数量
        ]

        # 有加载结果时逐条输出
        if self._skills:
            # 遍历每个 Skill（序号从 1 开始）
            for index, skill in enumerate(self._skills, start=1):
                # 适用 Agent（空则 all）
                agents = ", ".join(skill.agents) if skill.agents else "all"
                # 关键词前 8 个（空则 all）
                keywords = ", ".join(skill.keywords[:8]) if skill.keywords else "all"
                # 关键词超过 8 个时追加省略标记
                if len(skill.keywords) > 8:
                    keywords += ", ..."
                # 追加该 Skill 的日志行
                lines.extend([
                    f"{index}. {skill.name}",  # 序号 + 名称
                    f"   agents: {agents}",  # 适用 Agent
                    f"   keywords: {keywords}",  # 关键词
                    f"   path: {skill.path}",  # 源文件路径
                ])
        else:
            # 无 Skill 时输出提示
            lines.append("未加载任何 Skill。")

        # 有错误时输出错误明细
        if self._errors:
            lines.append("解析错误:")  # 错误标题
            # 逐条追加错误
            lines.extend(f"  - {error}" for error in self._errors)

        # 追加结尾分隔线
        lines.append("========================================================")
        # 输出完整日志
        logger.info("\n".join(lines))

    def _discover_files(self, root_dir: Path) -> Iterable[Path]:
        """
        发现可加载文件，优先读取目录规范文件 SKILL.md。

        参数：
            root_dir: 扫描的根目录

        返回：
            可加载文件路径的迭代器
        """
        # 递归找出所有 SKILL.md（目录规范文件，优先加载）
        skill_md_files = sorted(root_dir.rglob("SKILL.md"))
        # 记录已产出文件的绝对路径，避免后续重复
        yielded = {path.resolve() for path in skill_md_files}
        # 逐个产出 SKILL.md
        for path in skill_md_files:
            yield path

        # 遍历目录下所有文件，补足单文件 Skill
        for path in sorted(root_dir.rglob("*")):
            resolved = path.resolve()  # 绝对路径
            # 已产出或不是文件时跳过
            if resolved in yielded or not path.is_file():
                continue
            # 跳过隐藏文件和 README.md
            if path.name.startswith(".") or path.name.upper() == "README.MD":
                continue
            # 后缀受支持时产出
            if path.suffix.lower() in self.SUPPORTED_SUFFIXES:
                yield path

    def _load_file(self, path: Path) -> Optional[Skill]:
        """
        按文件后缀分发加载逻辑。

        参数：
            path: 文件路径

        返回：
            加载出的 Skill；加载失败返回 None
        """
        # JSON 走结构化解析
        if path.suffix.lower() == ".json":
            return self._load_json(path)
        # 其余按文本解析
        return self._load_text(path)

    def _load_json(self, path: Path) -> Optional[Skill]:
        """
        解析 JSON 格式 Skill 文件。

        参数：
            path: JSON 文件路径

        返回：
            解析出的 Skill 实例
        """
        # 读取并解析 JSON
        raw = json.loads(path.read_text(encoding="utf-8"))
        # 非对象格式直接报错
        if not isinstance(raw, dict):
            raise ValueError("JSON Skill 必须是对象格式")

        # 取正文（content 或 instructions 字段）
        content = str(raw.get("content") or raw.get("instructions") or "").strip()
        # 正文为空时报错
        if not content:
            raise ValueError("缺少 content 或 instructions")

        # 构造 Skill 实例
        return Skill(
            name=str(raw.get("name") or path.stem),  # 名称（缺省用文件名）
            description=str(raw.get("description") or ""),  # 说明
            content=content,  # 正文
            path=str(path),  # 源文件路径
            keywords=self._as_list(raw.get("keywords")),  # 关键词
            agents=[item.lower() for item in self._as_list(raw.get("agents"))],  # 适用 Agent（统一小写）
            enabled=self._as_bool(raw.get("enabled"), default=True),  # 启用标记（默认启用）
        )

    def _load_text(self, path: Path) -> Optional[Skill]:
        """
        解析 Markdown/TXT 格式 Skill 文件（支持 front matter）。

        参数：
            path: 文本文件路径

        返回：
            解析出的 Skill 实例；正文为空返回 None
        """
        # 读取文件全文
        raw = path.read_text(encoding="utf-8")
        # 拆分 front matter 元数据与正文
        meta, body = self._split_front_matter(raw)
        # 去除正文首尾空白
        body = body.strip()
        # 正文为空时视为无效 Skill
        if not body:
            return None

        # 默认名称：SKILL.md 用父目录名，其它用文件名主干
        default_name = path.parent.name if path.name == "SKILL.md" else path.stem
        # 名称优先级：front matter name > 首行标题 > 默认名称
        name = str(meta.get("name") or self._first_heading(body) or default_name)

        # 如果首行标题只是 Skill 名称，注入 prompt 时去掉它，减少重复噪音。
        body = self._strip_first_heading(body, name)

        # 构造 Skill 实例
        return Skill(
            name=name,  # 名称
            description=str(meta.get("description") or ""),  # 说明
            content=body,  # 正文
            path=str(path),  # 源文件路径
            keywords=self._as_list(meta.get("keywords")),  # 关键词
            agents=[item.lower() for item in self._as_list(meta.get("agents"))],  # 适用 Agent（统一小写）
            enabled=self._as_bool(meta.get("enabled"), default=True),  # 启用标记（默认启用）
        )

    def _split_front_matter(self, raw: str) -> tuple[Dict[str, Any], str]:
        """
        解析 Markdown 顶部的简单 front matter。

        格式：
        ---
        name: xxx
        description: xxx
        ---
        正文...

        这里刻意不用 PyYAML，避免为一个轻量配置格式新增运行时依赖。

        参数：
            raw: 原始文件文本

        返回：
            (元数据字典, 正文文本)
        """
        # 去掉前导空白
        text = raw.lstrip()
        # 不以 --- 开头则没有 front matter
        if not text.startswith("---"):
            return {}, raw

        # 按行拆分
        lines = text.splitlines()
        # 首行必须是 ---（排除 "--- xxx" 等干扰）
        if not lines or lines[0].strip() != "---":
            return {}, raw

        # 元数据字典
        meta: Dict[str, Any] = {}
        # 结尾 --- 所在行号
        end_idx: Optional[int] = None
        # 从第二行开始找结束标记
        for idx, line in enumerate(lines[1:], start=1):
            # 结束标记为独立的 ---
            if line.strip() == "---":
                end_idx = idx  # 记录结束行号
                break
            # 非 key: value 格式的行跳过
            if ":" not in line:
                continue
            # 按第一个冒号拆出键值
            key, value = line.split(":", 1)
            # 存入元数据（去空白、去引号）
            meta[key.strip()] = value.strip().strip("\"'")

        # 没找到结束标记视为没有 front matter
        if end_idx is None:
            return {}, raw
        # 返回元数据与正文（结束标记之后的内容）
        return meta, "\n".join(lines[end_idx + 1:])

    @staticmethod
    def _first_heading(body: str) -> Optional[str]:
        """
        提取正文首个 Markdown 标题文本。

        参数：
            body: 正文文本

        返回：
            首个标题的文本（无标题返回 None）
        """
        # 逐行查找标题
        for line in body.splitlines():
            stripped = line.strip()  # 去掉首尾空白
            # 以 # 开头视为标题
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or None  # 去掉 # 号并去空白
        return None

    @staticmethod
    def _strip_first_heading(body: str, name: str) -> str:
        """
        若首行标题恰是 Skill 名称，则去掉它避免 prompt 中重复。

        参数：
            body: 正文文本
            name: Skill 名称

        返回：
            处理后的正文
        """
        lines = body.splitlines()
        # 空正文原样返回
        if not lines:
            return body
        first = lines[0].strip()  # 首行内容
        # 首行是标题且与名称一致时去掉首行
        if first.startswith("#") and first.lstrip("#").strip() == name:
            return "\n".join(lines[1:]).strip()
        # 否则原样返回
        return body

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        """
        把字段值统一转成字符串列表（兼容 JSON 数组与逗号分隔两种写法）。

        参数：
            value: 原始字段值

        返回：
            去空白的字符串列表
        """
        # 空值返回空列表
        if value is None or value == "":
            return []
        # 列表类型逐项转字符串并去空
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        # 字符串按逗号切分
        return [item.strip() for item in str(value).split(",") if item.strip()]

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        """
        把字段值转成布尔（兼容 0/false/no/off/disabled 等否定写法）。

        参数：
            value: 原始字段值
            default: 空值时的默认结果（默认 False）

        返回：
            转换后的布尔值
        """
        # 空值返回默认值
        if value is None or value == "":
            return default
        # 已是布尔则直接返回
        if isinstance(value, bool):
            return value
        # 字符串转小写后判断是否在否定集合中
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}
