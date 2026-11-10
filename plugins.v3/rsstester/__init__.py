"""RSS 测试插件：批量检查已启用站点的 RSS 订阅源是否可用。"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.oper.site import SiteOper
from app.sdk.logging import logger
from app.sdk.network import RequestUtils, RssHelper, UrlUtils
from app.sdk.plugin import _PluginBase

# 单个站点的检测结果状态
STATUS_VALID = "valid"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"

# RSS 服务返回的过期提示原文
_EXPIRED_TEXTS = (
    "RSS 链接已过期, 您需要获得一个新的!",
    "RSS Link has expired, You need to get a new one!",
    "RSS Link has expired, You need get new!",
)

# 常见请求异常的简短说明
_EXC_LABELS: Dict[str, str] = {
    "ConnectTimeout": "连接超时",
    "ReadTimeout": "读取超时",
    "Timeout": "请求超时",
    "ConnectionError": "连接失败",
    "SSLError": "SSL 证书校验失败",
    "TooManyRedirects": "重定向次数过多",
    "InvalidURL": "地址无效",
    "MissingSchema": "地址缺少协议",
}

# 插件默认配置
_DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "concurrency": 8,
    "timeout": 0,
    "only_active": True,
    "site_ids": [],
    "notify": False,
    "trigger": False,
}


class RssTester(_PluginBase):
    """检查已启用站点 RSS 订阅源有效性的本地插件。"""

    plugin_name = "RSS 测试"
    plugin_desc = "按设置中选择的站点批量检查 RSS 订阅源，按有效、链接过期、请求失败三类展示结果，并可按需推送通知。"
    plugin_version = "1.0.6"
    plugin_author = "local"
    plugin_label = "工具"
    plugin_config_prefix = "rsstester_"
    plugin_order = 90
    auth_level = 1

    def __init__(self) -> None:
        """初始化插件配置默认值与检测任务状态。"""
        super().__init__()
        self._enabled: bool = True
        self._concurrency: int = 8
        self._timeout: int = 0
        self._only_active: bool = True
        self._site_ids: List[str] = []
        self._notify: bool = False
        self._trigger: bool = False
        self._check_lock = threading.Lock()
        self._check_state: Dict[str, Any] = self._idle_state()

    def init_plugin(self, config: Optional[dict] = None) -> None:
        """读取并校验插件配置；仅在设置中显式开启一次性检测时才发起检测。"""
        merged: Dict[str, Any] = dict(_DEFAULT_CONFIG)
        if isinstance(config, dict):
            for key in _DEFAULT_CONFIG:
                if key in config:
                    merged[key] = config[key]
        self._enabled = bool(merged.get("enabled", True))
        self._concurrency = self._coerce_int(merged.get("concurrency"), 8, 1, 16)
        self._timeout = self._coerce_int(merged.get("timeout"), 0, 0, 120)
        self._only_active = bool(merged.get("only_active", True))
        self._site_ids = self._normalize_site_ids(merged.get("site_ids"))
        self._notify = bool(merged.get("notify", False))
        self._trigger = bool(merged.get("trigger", False))
        # 一次性开关：读入后立即复位并写回，避免下次重载重复检测
        if self._trigger:
            self._trigger = False
            self._persist_config()
            if self._enabled:
                logger.info("RSS 测试：已按设置中的一次性开关发起检测")
                self._start_check()
            else:
                logger.warning("RSS 测试：插件未启用，一次性检测请求已忽略")

    def get_state(self) -> bool:
        """返回插件当前是否启用。"""
        return bool(self._enabled)

    @staticmethod
    def get_render_mode() -> Tuple[str, str]:
        """声明使用 Vue 联邦组件并返回构建产物目录。"""
        return "vue", "dist/assets"

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """插件不提供远程命令。"""
        return []

    def get_sidebar_nav(self) -> Optional[List[Dict[str, Any]]]:
        """声明主界面侧栏全页入口，前端跳转 #/plugin-app/RssTester/main。"""
        if not self._enabled:
            return None
        return [
            {
                "nav_key": "main",
                "title": "RSS 测试",
                "icon": "mdi-rss",
                "section": "system",
                "permission": "manage",
                "order": 90,
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """声明 RSS 检测、状态读取、配置读取与候选站点读取接口。"""
        return [
            {
                "path": "/status",
                "endpoint": self._status,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "只读取当前检测进度与结果快照，不发起检测",
            },
            {
                "path": "/check",
                "endpoint": self._check_sites,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "按设置批量检测站点 RSS 是否有效",
            },
            {
                "path": "/config",
                "endpoint": self._read_config,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "读取当前插件配置与可检测站点数量",
            },
            {
                "path": "/sites",
                "endpoint": self._site_options,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "读取可用于站点多选的私有站点列表",
            },
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """返回 Vue 配置组件使用的默认配置模型。"""
        return None, self._current_config()

    def get_page(self) -> None:
        """不提供 Vuetify 详情页，全部功能由完整页面承担。"""
        return None

    def stop_service(self) -> None:
        """插件不持有后台服务，停止时无需额外清理。"""
        return None

    @staticmethod
    def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        """把任意输入转换为指定范围内的整数，失败时返回默认值。"""
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, result))

    @staticmethod
    def _normalize_site_ids(value: Any) -> List[str]:
        """把配置中的站点选择转换为去重后的标识列表，兼容数组与逗号分隔字符串。"""
        if isinstance(value, str):
            raw = [item.strip() for item in value.split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw = [str(item).strip() for item in value]
        else:
            raw = []
        result: List[str] = []
        for item in raw:
            if item and item not in result:
                result.append(item)
        return result

    @staticmethod
    def _as_bool(value: Any) -> bool:
        """把数据库中的 0/1 或字符串转换为布尔值。"""
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _current_config(self) -> Dict[str, Any]:
        """汇总当前配置为可持久化字典。"""
        return {
            "enabled": self._enabled,
            "concurrency": self._concurrency,
            "timeout": self._timeout,
            "only_active": self._only_active,
            "site_ids": list(self._site_ids),
            "notify": self._notify,
            "trigger": False,
        }

    def _persist_config(self) -> None:
        """把当前配置写回插件实例，用于复位一次性检测开关。"""
        try:
            self.update_config(self._current_config())
        except Exception as exc:  # noqa: BLE001 - 写回失败不应阻断检测
            logger.error(f"RSS 测试：写回配置失败 - {exc}")

    def _site_rows(self) -> List[Dict[str, Any]]:
        """直接从站点表读取私有站点，避免依赖登录态并在后台线程中同样可用。"""
        try:
            oper = SiteOper()
            sites = oper.list_active() if self._only_active else oper.list()
        except Exception as exc:  # noqa: BLE001 - 站点读取失败不应使接口崩溃
            logger.error(f"RSS 测试：读取站点列表失败 - {exc}")
            return []
        rows: List[Dict[str, Any]] = []
        for site in sites or []:
            if self._as_bool(getattr(site, "public", False)):
                continue
            rows.append(
                {
                    "name": str(getattr(site, "name", "") or ""),
                    "domain": str(getattr(site, "domain", "") or ""),
                    "rss": str(getattr(site, "rss", "") or ""),
                    "timeout": getattr(site, "timeout", None),
                    "proxy": self._as_bool(getattr(site, "proxy", 0)),
                    "ua": str(getattr(site, "ua", "") or ""),
                    "is_active": self._as_bool(getattr(site, "is_active", True)),
                }
            )
        return rows

    def _collect_sites(self) -> List[Dict[str, Any]]:
        """按启用状态、站点选择与名称过滤并排序可检测站点。"""
        selected = {item.lower() for item in self._site_ids}
        collected: List[Dict[str, Any]] = []
        for site in self._site_rows():
            if self._only_active and site.get("is_active") is False:
                continue
            if selected:
                name = str(site.get("name") or "").strip().lower()
                domain = str(site.get("domain") or "").strip().lower()
                if name not in selected and domain not in selected:
                    continue
            collected.append(site)
        collected.sort(key=lambda item: str(item.get("name") or item.get("domain") or ""))
        return collected

    def _read_config(self) -> Dict[str, Any]:
        """返回当前配置、可检测站点数量与本次生效的站点选择。"""
        options = self._site_options()
        return {
            "config": self._current_config(),
            "site_count": len(self._collect_sites()),
            "site_total": int(options.get("total") or 0),
            "selected_count": len(self._site_ids),
            "status": self._check_state.get("status", "idle"),
        }

    def _site_options(self) -> Dict[str, Any]:
        """返回站点多选候选列表，只暴露名称、域名与是否配置 RSS，不返回 RSS 地址。"""
        options: List[Dict[str, Any]] = []
        for site in self._site_rows():
            name = str(site.get("name") or site.get("domain") or "").strip()
            if not name:
                continue
            options.append(
                {
                    "name": name,
                    "domain": str(site.get("domain") or ""),
                    "has_rss": bool(str(site.get("rss") or "").strip()),
                    "active": site.get("is_active") is not False,
                }
            )
        options.sort(key=lambda item: str(item.get("name") or ""))
        return {
            "sites": options,
            "total": len(options),
            "selected": list(self._site_ids),
            "only_active": self._only_active,
        }

    @staticmethod
    def _idle_state() -> Dict[str, Any]:
        """返回一次尚未开始的检测任务状态。"""
        return {
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "done": 0,
            "elapsed_ms": 0,
            "error": None,
            "items": [],
        }

    def _disabled_payload(self) -> Dict[str, Any]:
        """返回插件未启用时的明确状态，避免前端误判为正在检测。"""
        return {
            "status": "disabled",
            "started_at": None,
            "finished_at": None,
            "checked_at": None,
            "total": 0,
            "done": 0,
            "elapsed_ms": 0,
            "valid": 0,
            "expired": 0,
            "failed": 0,
            "config": self._current_config(),
            "error": "插件未启用：请在插件设置中开启“启用检测”并保存后再检测。",
            "items": [],
        }

    def _status(self) -> Dict[str, Any]:
        """只读返回当前进度快照，不触发任何检测。"""
        if not self._enabled:
            return self._disabled_payload()
        return self._check_payload()

    def _check_sites(self) -> Dict[str, Any]:
        """启动后台检测任务并立即返回进度快照；插件未启用时直接返回 disabled。"""
        if not self._enabled:
            return self._disabled_payload()
        return self._start_check()

    def _start_check(self) -> Dict[str, Any]:
        """在后台线程启动一轮检测；已有检测运行时直接返回当前快照。"""
        with self._check_lock:
            if self._check_state.get("status") == "running":
                return self._check_payload()
            self._check_state = self._idle_state()
            self._check_state["status"] = "running"
            self._check_state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        threading.Thread(
            target=self._run_check,
            name="rsstester-check",
            daemon=True,
        ).start()
        with self._check_lock:
            return self._check_payload()

    def _run_check(self) -> None:
        """在后台线程中并发检测站点 RSS，持续刷新共享进度，并在结束后按需通知。"""
        started = time.monotonic()
        try:
            sites = self._collect_sites()
            with self._check_lock:
                self._check_state["total"] = len(sites)
            if sites:
                workers = max(1, min(self._concurrency, len(sites)))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(self._check_one, site) for site in sites]
                    for future in as_completed(futures):
                        try:
                            item = future.result()
                        except Exception as exc:  # noqa: BLE001 - 单个站点异常不影响整轮检测
                            item = {
                                "name": "未知站点",
                                "domain": "",
                                "status": STATUS_FAILED,
                                "count": 0,
                                "elapsed_ms": 0,
                                "message": f"检测异常：{type(exc).__name__}",
                            }
                        with self._check_lock:
                            self._check_state["items"].append(item)
                            self._check_state["done"] = len(self._check_state["items"])
                            self._check_state["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            with self._check_lock:
                self._check_state["items"].sort(key=lambda it: str(it.get("name") or ""))
                self._check_state["status"] = "done"
                self._check_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._check_state["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        except Exception as exc:  # noqa: BLE001 - 整轮失败也要让前端拿到原因
            logger.error(f"RSS 测试：检测任务失败 - {exc}")
            with self._check_lock:
                self._check_state["status"] = "error"
                self._check_state["error"] = str(exc)
                self._check_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._check_state["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        self._send_notification()

    def _send_notification(self) -> None:
        """按通知开关推送本轮检测汇总，包含插件名、检测时间与各异常站点的具体原因。"""
        if not self._notify:
            return
        try:
            payload = self._check_payload()
            total = int(payload.get("total") or 0)
            valid = int(payload.get("valid") or 0)
            expired = int(payload.get("expired") or 0)
            failed = int(payload.get("failed") or 0)
            elapsed = round(int(payload.get("elapsed_ms") or 0) / 1000, 1)
            checked_at = str(payload.get("checked_at") or "").strip() or datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            abnormal = [
                (
                    str(item.get("name") or item.get("domain") or "未知站点"),
                    str(item.get("message") or "").strip(),
                )
                for item in payload.get("items") or []
                if str(item.get("status") or "") in {STATUS_EXPIRED, STATUS_FAILED}
            ]
            title = f"【{self.plugin_name}】检测完成"
            if abnormal:
                title = f"{title}：{len(abnormal)} 个站点异常"
            lines = [
                f"插件：{self.plugin_name}",
                f"检测时间：{checked_at}",
                f"耗时：{elapsed} 秒",
                f"站点：共 {total} 个，有效 {valid}，已过期 {expired}，失败 {failed}",
            ]
            if abnormal:
                lines.append("")
                lines.append("异常站点：")
                for name, message in abnormal[:10]:
                    lines.append(f"· {name}：{message or '未记录具体原因'}")
                if len(abnormal) > 10:
                    lines.append(f"· 其余 {len(abnormal) - 10} 个异常站点请在“{self.plugin_name}”页面查看")
            self.post_message(title=title, text="\n".join(lines))
            logger.info(f"RSS 测试：检测结果通知已提交（{total} 个站点，异常 {len(abnormal)} 个）")
        except Exception as exc:  # noqa: BLE001 - 通知失败不影响检测结果
            logger.error(f"RSS 测试：发送通知失败 - {exc}")

    def _check_payload(self) -> Dict[str, Any]:
        """构造当前进度与汇总结果快照。"""
        state = self._check_state
        items = list(state.get("items") or [])
        counters = {STATUS_VALID: 0, STATUS_EXPIRED: 0, STATUS_FAILED: 0}
        for item in items:
            status = str(item.get("status") or STATUS_FAILED)
            counters[status] = counters.get(status, 0) + 1
        return {
            "status": state.get("status", "idle"),
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "checked_at": state.get("finished_at") or state.get("started_at"),
            "total": int(state.get("total") or 0),
            "done": int(state.get("done") or 0),
            "elapsed_ms": int(state.get("elapsed_ms") or 0),
            "valid": counters[STATUS_VALID],
            "expired": counters[STATUS_EXPIRED],
            "failed": counters[STATUS_FAILED],
            "config": self._current_config(),
            "error": state.get("error"),
            "items": items,
        }

    @staticmethod
    def _mask_url(text: str) -> str:
        """隐藏文本中的链接与密钥参数，避免把带 passkey 的地址写入结果或通知。"""
        if not text:
            return ""
        masked = re.sub(r"https?://\S+", "<链接已隐藏>", text)
        return re.sub(
            r"(?i)\b(passkey|pass_key|rsskey|authkey|apikey|api_key|token|key)=([^&\s\"'>]+)",
            r"\1=***",
            masked,
        )

    @staticmethod
    def _exc_label(exc: Exception) -> str:
        """把请求异常归类为可读原因，超时细分到连接超时与读取超时。"""
        text = str(exc).lower()
        if "read timed out" in text or "readtimeout" in text:
            return "读取超时"
        if "connecttimeout" in text or "connect timed out" in text:
            return "连接超时"
        if "timed out" in text or "timeout" in text:
            return "请求超时"
        if "certificate" in text or "ssl" in text:
            return "SSL 证书校验失败"
        if "max retries exceeded" in text:
            return "连接失败"
        return _EXC_LABELS.get(type(exc).__name__) or "请求异常"

    def _diagnose(self, rss_url: str, site: Dict[str, Any], timeout: int) -> str:
        """再次请求一次 RSS 地址，用于给出比“请求失败”更具体的原因。"""
        proxies = None
        if site.get("proxy"):
            try:
                from app.application.configuration import get_chain_runtime_config_snapshot

                proxies = get_chain_runtime_config_snapshot().proxy
            except Exception:  # noqa: BLE001 - 代理配置不可用时退回直连诊断
                proxies = None
        try:
            client = RequestUtils(ua=site.get("ua") or None, proxies=proxies, timeout=timeout)
            response = client.get_res(rss_url, timeout=timeout, raise_exception=True)
        except Exception as exc:  # noqa: BLE001 - 诊断异常本身就是失败原因
            label = self._exc_label(exc)
            detail = self._mask_url(str(exc)).strip().splitlines()
            detail_text = detail[0][:120] if detail else ""
            return f"{label}：{detail_text}" if detail_text else label
        if response is None:
            return "无响应"
        if response.status_code != 200:
            return f"HTTP 状态码 {response.status_code}"
        text = (response.text or "").strip()
        if not text:
            return "响应内容为空"
        if text in _EXPIRED_TEXTS:
            return "RSS 链接已过期，需要重新获取链接"
        if not text.startswith("<"):
            return "响应不是 RSS/XML，可能是登录页或错误页"
        size = len(response.content or b"")
        if size > RssHelper.MAX_RSS_SIZE:
            return f"RSS 文件过大（{size / 1024 / 1024:.1f}MB）"
        return "RSS 内容解析失败，没有可用条目"

    def _check_one(self, site: Dict[str, Any]) -> Dict[str, Any]:
        """检测单个站点的 RSS，并归类为有效、已过期或请求失败。"""
        name = str(site.get("name") or site.get("domain") or "未知站点")
        domain = str(site.get("domain") or "")
        item: Dict[str, Any] = {
            "name": name,
            "domain": domain,
            "status": STATUS_FAILED,
            "count": 0,
            "elapsed_ms": 0,
            "message": "",
        }
        raw_rss = site.get("rss") or ""
        try:
            rss_url = UrlUtils.normalize_http_url(raw_rss) if raw_rss else ""
        except Exception:  # noqa: BLE001 - 非法地址按未配置处理
            rss_url = str(raw_rss).strip()
        if not rss_url:
            item["message"] = "站点未配置 RSS 地址"
            return item
        timeout = self._timeout or self._coerce_int(site.get("timeout"), 30, 1, 120)
        started = time.monotonic()
        try:
            parsed = RssHelper().parse(
                rss_url,
                proxy=bool(site.get("proxy")),
                timeout=int(timeout),
                ua=site.get("ua") or None,
            )
        except Exception as exc:  # noqa: BLE001 - 单站点异常不影响其他站点
            item["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            item["message"] = f"检测异常：{type(exc).__name__}"
            return item
        if isinstance(parsed, list):
            item["status"] = STATUS_VALID
            item["count"] = len(parsed)
            item["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            return item
        if parsed is None:
            item["status"] = STATUS_EXPIRED
        # 失败或过期都补一次诊断，落实到具体原因
        item["message"] = self._diagnose(rss_url, site, int(timeout))
        item["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return item