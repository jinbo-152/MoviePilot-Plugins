"""
命令管理插件 - 适配 MoviePilot V2 官方标准
"""
import json
from typing import Dict, Any
from app.plugins import PluginBase
from app.core import settings
from app.utils import logger
from app.schemas.types import EventType
from app.core.event import EventManager

__plugin_name__ = "命令管理"
__plugin_version__ = "2.0.0"
__plugin_author__ = "jinbo"
__plugin_desc__ = "管理各消息服务注册的命令，支持自定义、过滤、权限控制"


class Plugin(PluginBase):
    """
    命令管理插件
    """
    def init(self):
        """
        插件初始化
        """
        self.service_infos = self.config.get("service_infos", {})
        try:
            custom_conf = self.config.get("custom_commands", "{}")
            self.custom_commands = json.loads(custom_conf) if isinstance(custom_conf, str) else custom_conf
        except Exception as e:
            logger.error(f"【命令管理】配置解析失败：{str(e)}")
            self.custom_commands = {}

        # 注册事件
        self.register_event(EventType.CommandList, self.process_commands)
        logger.info(f"【命令管理】插件加载完成 v{__plugin_version__}")

    def process_commands(self, service: str, commands: Dict[str, Dict], **kwargs):
        """
        处理命令
        """
        if not self.service_infos:
            return commands

        if service not in self.service_infos:
            return {}

        service_custom = self.custom_commands.get(service, {})
        if not service_custom:
            return commands

        processed = {}
        for cmd_key, cmd_info in commands.items():
            if cmd_key not in service_custom:
                continue
            new_cmd = cmd_info.copy()
            custom = service_custom[cmd_key]
            if custom.get("description"):
                new_cmd["name"] = custom["description"]
            if custom.get("category"):
                new_cmd["category"] = custom["category"]
            processed[cmd_key] = new_cmd

        return processed

    def get_page(self):
        """
        配置页面
        """
        return {
            "title": "命令管理",
            "config": [
                {
                    "type": "title",
                    "text": "启用服务配置"
                },
                {
                    "type": "input",
                    "label": "启用服务（JSON）",
                    "name": "service_infos",
                    "default": self.config.get("service_infos", {"WeChat": True, "Telegram": True}),
                    "rows": 5
                },
                {
                    "type": "title",
                    "text": "自定义命令"
                },
                {
                    "type": "input",
                    "label": "自定义命令（JSON）",
                    "name": "custom_commands",
                    "default": self.config.get("custom_commands", "{}"),
                    "rows": 15
                }
            ]
        }

    def stop(self):
        logger.info("【命令管理】插件已停止")