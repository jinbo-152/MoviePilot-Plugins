"""
命令管理插件 - 适配 MoviePilot 最新版
功能：客户端命令过滤、自定义命令、权限拦截、菜单自定义
"""
import json
from typing import Optional, Dict, Any
from app.plugins import PluginBase
from app.core.event import EventManager
from app.utils import logger
from app.schemas.types import EventType

__plugin_name__ = "命令管理"
__plugin_version__ = "2.0.0"
__plugin_author__ = "InfinityPacer + 新版适配"
__plugin_desc__ = "管理各消息服务注册的命令，支持自定义、过滤、权限控制"


class Plugin(PluginBase):
    """
    命令管理插件
    """
    def __init__(self, plugin_id: str):
        super().__init__(plugin_id)
        self.event_manager = EventManager()
        # 服务配置
        self.service_infos: Dict[str, Any] = {}
        # 自定义命令
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
            logger.error(f"【命令管理】自定义命令配置解析失败：{str(e)}")
            self.custom_commands = {}

        # 注册新版命令钩子（核心修复点）
        self.event_manager.register(
            event_type=EventType.CommandList,
            callback=self.process_commands
        )
        logger.info("【命令管理】插件初始化完成，已接管命令列表")

    def process_commands(self, service: str, commands: Dict[str, Dict], **kwargs):
        """
        新版核心：处理各客户端命令列表
        :param service: 客户端名称 WeChat/Telegram/Web
        :param commands: 原始命令 dict
        :return: 处理后的命令 dict
        """
        if not self.service_infos:
            return commands

        # 1. 未配置的服务直接拦截
        if service not in self.service_infos.keys():
            logger.info(f"【命令管理】已拦截未授权服务：{service}")
            return {}

        # 2. 获取当前服务自定义命令
        service_custom = self.custom_commands.get(service, {})
        if not service_custom:
            return commands

        # 3. 过滤并自定义命令
        processed_commands = {}
        for cmd_key, cmd_info in commands.items():
            # 不在自定义配置中，跳过（隐藏）
            if cmd_key not in service_custom:
                continue

            # 复制原始命令信息
            new_cmd = cmd_info.copy()
            # 覆盖自定义配置
            custom_info = service_custom[cmd_key]
            if custom_info.get("description"):
                new_cmd["name"] = custom_info["description"]
            if custom_info.get("category"):
                new_cmd["category"] = custom_info["category"]

            processed_commands[cmd_key] = new_cmd

        logger.info(f"【命令管理】{service} 处理完成，显示 {len(processed_commands)} 条命令")
        return processed_commands

    def get_page(self, config: dict = None):
        """
        插件配置页面（和原版完全一致）
        """
        if not config:
            config = {}
        return {
            "title": "命令管理",
            "config": [
                {
                    "type": "title",
                    "text": "基础配置"
                },
                {
                    "type": "input",
                    "label": "启用服务",
                    "name": "service_infos",
                    "default": config.get("service_infos", {
                        "WeChat": True,
                        "Telegram": True
                    }),
                    "rows": 5,
                    "hint": "JSON格式，启用的服务：{\"WeChat\": true, \"Telegram\": true}"
                },
                {
                    "type": "title",
                    "text": "自定义命令"
                },
                {
                    "type": "input",
                    "label": "自定义命令配置",
                    "name": "custom_commands",
                    "default": config.get("custom_commands", "{}"),
                    "rows": 15,
                    "hint": "JSON格式，按服务配置命令：{\"WeChat\": {\"/test\": {\"description\": \"测试\", \"category\": \"测试\"}}}"
                }
            ]
        }

    def stop(self):
        """
        插件停止
        """
        logger.info("【命令管理】插件已停止")