"""
命令管理插件 - MoviePilot v2.10+ 适配版
修复原插件失效问题，支持命令过滤、自定义、权限控制
"""
import json
from typing import Dict, Any
from app.core.event import EventManager
from app.plugins import PluginBase
from app.schemas import ServiceInfo
from app.utils import logger


class CommandsPlugin(PluginBase):
    # 插件名称
    plugin_name = "命令管理"
    # 插件描述
    plugin_desc = "实现微信、Telegram等客户端的命令管理。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/shilinliu-jinbo/MoviePilot-Plugins/main/icons/commands.png"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "InfinityPacer"
    # 作者主页
    author_url = "https://github.com/shilinliu-jinbo"
    # 插件配置项ID前缀
    plugin_config_prefix = "commands_"
    # 加载顺序
    plugin_order = 42
    # 可使用的用户级别
    auth_level = 1

    # region 私有属性
    notify_helper = None
    # 是否开启
    _enabled = False
    # 通知客户端
    _notify_clients = None
    # 自定义指令
    _custom_commands = None
    # 配置项
    _config: dict = {
        "enable": True,
        # 允许的客户端
        "allow_services": ["WeChat", "Telegram", "Web"],
        # 自定义命令配置
        "custom_commands": {}
    }

    # 配置页面
    _config_title = "命令管理"
    _config_items = [
        {
            "title": "插件开关",
            "name": "enable",
            "type": "switch",
            "default": True,
            "required": True
        },
        {
            "title": "允许的客户端",
            "name": "allow_services",
            "type": "select",
            "options": ["WeChat", "Telegram", "Web"],
            "multiple": True,
            "default": ["WeChat", "Telegram"],
            "required": True,
            "description": "只对选中的客户端生效"
        },
        {
            "title": "自定义命令配置 (JSON)",
            "name": "custom_commands",
            "type": "textarea",
            "default": "{}",
            "required": False,
            "description": "格式：{\"客户端名\": {\"/命令\": {\"description\": \"描述\", \"category\": \"分类\"}}}"
        }
    ]

    def __init__(self, plugin_id: str):
        super().__init__(plugin_id)
        self.event_manager = EventManager()
        # 加载配置
        self.enable = self._config.get("enable", True)
        self.allow_services = self._config.get("allow_services", [])
        self.custom_commands = {}
        # 解析JSON配置
        try:
            custom_conf = self._config.get("custom_commands", "{}")
            self.custom_commands = json.loads(custom_conf) if isinstance(custom_conf, str) else custom_conf
        except Exception as e:
            logger.error(f"【命令管理】配置解析失败：{str(e)}")
            self.custom_commands = {}

    def init(self):
        """
        插件初始化 - 新版MP：通过钩子替代旧CommandRegister事件
        """
        if not self.enable:
            return
        # 注册命令处理钩子（新版核心）
        self.event_manager.register(
            "system.command.list",
            self.filter_commands
        )
        logger.info("【命令管理】插件初始化成功（新版适配）")

    def filter_commands(self, service_name: str, commands: Dict[str, Any], **kwargs):
        """
        新版核心：过滤/修改命令列表
        :param service_name: 客户端名称 WeChat/Telegram/Web
        :param commands: 原始命令字典
        :return: 处理后的命令
        """
        if not self.enable:
            return commands

        # 1. 拦截非允许客户端
        if service_name not in self.allow_services:
            logger.info(f"【命令管理】已拦截未授权客户端：{service_name}")
            return {}

        # 2. 获取该客户端的自定义配置
        service_config = self.custom_commands.get(service_name, {})
        if not service_config:
            return commands

        # 3. 只保留配置中允许的命令
        new_commands = {}
        for cmd_key, cmd_info in commands.items():
            # 不在配置里 → 过滤掉
            if cmd_key not in service_config:
                continue

            # 复制原始信息
            new_cmd = cmd_info.copy()
            # 覆盖自定义配置
            custom = service_config[cmd_key]
            if custom.get("description"):
                new_cmd["name"] = custom["description"]
            if custom.get("category"):
                new_cmd["category"] = custom["category"]

            new_commands[cmd_key] = new_cmd

        logger.info(f"【命令管理】{service_name} 处理完成，保留 {len(new_commands)} 条命令")
        return new_commands