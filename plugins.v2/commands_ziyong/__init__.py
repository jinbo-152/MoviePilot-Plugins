import json
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import ServiceInfo
from app.schemas.event import CommandRegisterEventData
from app.schemas.types import ChainEventType

lock = threading.Lock()

class CommandsZiyong(_PluginBase):  # 类名修改为 CommandsZiyong
    # 插件名称 (界面显示名称)
    plugin_name = "命令管理(自用版)"
    # 插件描述
    plugin_desc = "实现微信、Telegram等客户端的命令管理 (自用独立版)。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/commands.png"
    # 插件版本
    plugin_version = "2.0.1"  
    # 插件作者
    plugin_author = "InfinityPacer & You"
    # 作者主页
    author_url = "https://github.com/InfinityPacer"
    # 插件配置项ID前缀 (必须修改，防止与原版配置冲突！)
    plugin_config_prefix = "commands_ziyong_"
    # 加载顺序
    plugin_order = 43
    # 可使用的用户级别
    auth_level = 1

    # region 私有属性
    # 是否开启
    _enabled = False
    # 通知客户端
    _notify_clients = None
    # 自定义指令
    _custom_commands = None
    # endregion

    def init_plugin(self, config: dict = None):
        if not config:
            return

        self._enabled = config.get("enabled") or False
        self._notify_clients = config.get("notify_clients") or []
        try:
            self._custom_commands = json.loads(config.get("custom_commands")) or {}
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 自定义命令格式错误，请检查，{e}")
            self._custom_commands = {}

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        获取通知服务信息 (V2 标准方式)
        """
        if not self._notify_clients:
            logger.warning(f"[{self.plugin_name}] 尚未配置通知客户端，请检查配置")
            return None

        # V2 中通过基类方法获取指定类型的服务实例
        services = self.get_services(type="notification", name_filters=self._notify_clients)
        if not services:
            logger.warning(f"[{self.plugin_name}] 获取通知客户端实例失败，请检查配置")
            return None

        return services

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        """
        # V2 中获取通知渠道列表的兼容方案
        notify_channels = ["WeChat", "Telegram", "SynologyChat", "Slack", "VoceChat", "WebPush"]
        
        try:
            active_services = self.get_services(type="notification")
            if active_services:
                for name in active_services.keys():
                    if name not in notify_channels:
                        notify_channels.append(name)
        except Exception:
            pass

        notify_items = [{"title": name, "value": name} for name in notify_channels]

        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                            'hint': '开启后插件将处于激活状态',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'notify_clients',
                                            'label': '启用命令菜单的通知客户端',
                                            'hint': '选择启用命令菜单的通知客户端 (V2中通常为 WeChat, Telegram 等)',
                                            'persistent-hint': True,
                                            'items': notify_items
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VTabs',
                        'props': {
                            'model': '_tabs',
                            'style': {'margin-top': '8px', 'margin-bottom': '16px'},
                            'stacked': False,
                            'fixed-tabs': False
                        },
                        'content': [
                            {'component': 'VTab', 'props': {'value': 'preset_tab'}, 'text': '系统预置'},
                            {'component': 'VTab', 'props': {'value': 'custom_tab'}, 'text': '自定义'}
                        ]
                    },
                    {
                        'component': 'VWindow',
                        'props': {'model': '_tabs'},
                        'content': [
                            {
                                'component': 'VWindowItem',
                                'props': {'value': 'preset_tab'},
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {"cols": 12},
                                                'content': [
                                                    {
                                                        'component': 'VAceEditor',
                                                        'props': {
                                                            'modelvalue': 'preset_commands',
                                                            'lang': 'json',
                                                            'theme': 'monokai',
                                                            'style': 'height: 35rem; font-size: 14px',
                                                            'readonly': True
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'VWindowItem',
                                'props': {'value': 'custom_tab'},
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {"cols": 12},
                                                'content': [
                                                    {
                                                        'component': 'VAceEditor',
                                                        'props': {
                                                            'modelvalue': 'custom_commands',
                                                            'lang': 'json',
                                                            'theme': 'monokai',
                                                            'style': 'height: 35rem; font-size: 14px'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'style': {'margin-top': '12px'}},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '注意：企业微信目前仅支持3个一级菜单和5个二级菜单。V2版本中，请确保通知客户端名称与系统服务名称一致（如 WeChat, Telegram）。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "custom_commands": self.__get_default_commands()
        }

    def get_page(self) -> List[dict]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        pass

    @eventmanager.register(ChainEventType.CommandRegister)
    def handle_command_register(self, event: Event):
        """
        处理 CommandRegister 事件 (V2 适配版)
        """
        if not event or not event.event_data:
            return

        event_data: CommandRegisterEventData = event.event_data
        logger.debug(f"[{self.plugin_name}] 处理命令注册事件 - source: {event_data.source}, service: {event_data.service}")

        if event_data.cancel:
            logger.debug(f"[{self.plugin_name}] 该事件已被其他事件处理器处理，跳过后续操作")
            return

        # V2 中，系统预置命令收集阶段 source 通常为 CommandChain
        if event_data.source == "CommandChain":
            config = self.get_config()
            config["preset_commands"] = json.dumps(event_data.commands, indent=4, ensure_ascii=False)
            self.update_config(config=config)
            return

        # V2 中，具体通知渠道触发时，service 字段包含渠道名称
        if event_data.service not in ["WeChat", "Telegram", "SynologyChat", "Slack"]:
            logger.debug(f"[{self.plugin_name}] 尚未支持的事件服务: {event_data.service}，跳过拦截")
            return

        # 如果不在选择的服务实例中，则直接拦截
        event_data.source = self.plugin_name  # 标记来源为自用版
        if not self.service_infos or event_data.service not in self.service_infos.keys():
            event_data.cancel = True
            logger.warning(f"[{self.plugin_name}] 命令注册被拦截，service: {event_data.service}")
            return
        else:
            event_data.cancel = False
            custom_commands = self._custom_commands.get(event_data.service) or {}
            if not custom_commands:
                logger.info(f"[{self.plugin_name}] 未能获取到 {event_data.service} 相关的自定义命令，跳过处理")
                return
            else:
                # 遍历并更新 event_data.commands
                commands = event_data.commands
                for cmd_key in list(commands.keys()):
                    if cmd_key in custom_commands:
                        category = commands[cmd_key].get("category", "")
                        description = commands[cmd_key].get("description", "")
                        commands[cmd_key]["category"] = custom_commands[cmd_key].get("category", category)
                        commands[cmd_key]["description"] = custom_commands[cmd_key].get("description", description)
                    else:
                        # 如果命令不在自定义命令中，则从 event_data.commands 中移除
                        del event_data.commands[cmd_key]
                logger.debug(f"[{self.plugin_name}] Final commands after processing for {event_data.service}: {event_data.commands}")

    @staticmethod
    def __get_default_commands():
        """
        获取自定义默认值指令
        """
        return """{
    "WeChat": {
        "/cookiecloud": {
            "type": "preset",
            "description": "同步站点",
            "category": "站点"
        },
        "/sites": {
            "type": "preset",
            "description": "查询站点",
            "category": "站点"
        }
    },
    "Telegram": {
        "/restart": {
            "type": "preset",
            "description": "重启系统",
            "category": "管理"
        },
        "/version": {
            "type": "preset",
            "description": "当前版本",
            "category": "管理"
        }
    }
}"""
