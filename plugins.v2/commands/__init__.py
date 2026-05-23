"""
命令管理插件 - 适配 MoviePilot V2 最新版
功能：客户端命令过滤、自定义命令、权限拦截、菜单自定义
"""
import json
from typing import Dict, Any
from app.plugins import PluginBase
from app.core.event import EventManager
from app.utils import logger
from app.schemas.types import EventType

__plugin_name__ = "命令管理"
__plugin_version__ = "2.0.0"
__plugin_author__ = "jinbo"
__plugin_desc__ = "管理各消息服务注册的命令，支持自定义、过滤、权限控制"


class Plugin(PluginBase):
    """
    命令管理插件
    """
    def __init__(self, plugin_id: str):
        super().__init__(plugin_id)
        self.event_manager = EventManager()
        self.service_infos: Dict[str, Any] = {}
        self.custom_commands: Dict[str, Any] = {}

    def init(self, config: dict = None):
        """
        插件初始化
        """
        if not config:
            config = {}
        
        # 加载配置
        self.service_infos = config.get("service_infos", {})
        try:
            custom_conf = config.get("custom_commands", "{}")
            self.custom_commands = json.loads(custom_conf) if isinstance(custom_conf, str) else custom_conf
        except Exception as e:
            logger.error(f"【命令管理】配置解析失败：{str(e)}")
            self.custom_commands = {}

        # 注册新版命令钩子
        self.event_manager.register(
            event_type=EventType.CommandList,
            callback=self.process_commands
        )
        logger.info("【命令管理】插件初始化完成")

    def process_commands(self, service: str, commands: Dict[str, Dict], **kwargs):
        """
        处理命令列表：过滤 + 自定义
        """
        if not self.service_infos:
            return commands

        # 1. 拦截未授权服务
        if service not in self.service_infos.keys():
            logger.info(f"【命令管理】已拦截未授权服务：{service}")
            return {}

        # 2. 获取自定义配置
        service_custom = self.custom_commands.get(service, {})
        if not service_custom:
            return commands

        # 3. 过滤并修改命令
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

        logger.info(f"【命令管理】{service} 处理完成，显示 {len(processed)} 条命令")
        return processed

    def get_page(self, config: dict = None):
        """
        插件配置页面
        """
        if not config:
            config = {}
        return {
            "title": "命令管理",
            "config": [
                {
                    "type": "title",
                    "text": "启用服务配置"
                },
                {
                    "type": "input",
                    "label": "启用服务（JSON格式）",
                    "name": "service_infos",
                    "default": config.get("service_infos", {"WeChat": True, "Telegram": True}),
                    "rows": 5,
                    "hint": "示例：{\"WeChat\": true, \"Telegram\": true}"
                },
                {
                    "type": "title",
                    "text": "自定义命令配置"
                },
                {
                    "type": "input",
                    "label": "自定义命令（JSON格式）",
                    "name": "custom_commands",
                    "default": config.get("custom_commands", "{}"),
                    "rows": 15,
                    "hint": "按客户端配置允许显示的命令"
                }
            ]
        }

    def stop(self):
        logger.info("【命令管理】插件已停止")