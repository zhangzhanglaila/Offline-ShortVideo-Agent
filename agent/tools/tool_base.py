# -*- coding: utf-8 -*-
"""
工具基类 - 定义Function Calling接口
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from enum import Enum
import json


class ToolCategory(Enum):
    """工具分类"""
    MATERIAL = "素材管理"
    TOPIC = "选题推荐"
    SCRIPT = "脚本生成"
    VIDEO = "视频剪辑"
    SUBTITLE = "字幕生成"
    PLATFORM = "多平台适配"


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str  # "str", "int", "float", "bool", "list", "dict"
    description: str
    required: bool = True
    default: Any = None
    enum_values: Optional[List[str]] = None


@dataclass
class ToolDefinition:
    """工具定义（用于Prompt生成）"""
    name: str
    category: ToolCategory
    description: str
    parameters: List[ToolParameter]

    def to_openai_format(self) -> Dict:
        """转换为OpenAI格式（用于模型解析）"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    p.name: {
                        "type": p.type,
                        "description": p.description,
                        "enum": p.enum_values
                    } for p in self.parameters
                },
                "required": [p.name for p in self.parameters if p.required]
            }
        }

    def to_markdown(self) -> str:
        """转换为Markdown格式（用于Prompt）"""
        params_md = []
        for p in self.parameters:
            required_mark = "**必需**" if p.required else "可选"
            enum_md = f", 枚举值: {p.enum_values}" if p.enum_values else ""
            params_md.append(f"- `{p.name}` ({p.type}) {required_mark}: {p.description}{enum_md}")

        return f"""### {self.name}

**分类**: {self.category.value}

**功能**: {self.description}

**参数**:
{chr(10).join(params_md)}
"""


@dataclass
class ToolResult:
    """工具执行结果"""
    tool_name: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    metadata: Dict = field(default_factory=dict)

    def to_observation(self) -> str:
        """转换为观察结果文本"""
        if self.success:
            if isinstance(self.result, (list, dict)):
                try:
                    return json.dumps(self.result, ensure_ascii=False, indent=2)[:2000]
                except:
                    pass
            return str(self.result)[:2000]
        else:
            return f"执行失败: {self.error}"


class BaseTool(ABC):
    """工具基类"""

    def __init__(self):
        self._definition: Optional[ToolDefinition] = None

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """工具定义"""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """执行工具"""
        pass

    def validate_params(self, params: Dict) -> tuple:
        """验证参数，返回 (是否有效, 错误信息)"""
        for p in self.definition.parameters:
            if p.required and p.name not in params:
                # 检查是否有默认值
                if p.default is None:
                    return False, f"缺少必需参数: {p.name}"
            if p.name in params:
                value = params[p.name]
                if p.enum_values and value not in p.enum_values:
                    return False, f"参数 {p.name} 值必须是 {p.enum_values} 之一"
                if p.type == "int":
                    try:
                        int(value)
                    except:
                        return False, f"参数 {p.name} 必须是整数"
                elif p.type == "float":
                    try:
                        float(value)
                    except:
                        return False, f"参数 {p.name} 必须是数字"
        return True, None
