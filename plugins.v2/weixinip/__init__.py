import re
import os
import time
from urllib.parse import urljoin
import requests
from datetime import datetime, timedelta
import pytz
from typing import Any, List, Dict, Tuple, Optional
from app.core.event import eventmanager, Event
from app.schemas.types import EventType, MessageChannel, NotificationType
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.log import logger
from app.plugins import _PluginBase
from app.core.config import settings
from app.helper.cookiecloud import CookieCloudHelper

# 使用 MoviePilot 官方内置的 DrissionPage
from DrissionPage import ChromiumPage, ChromiumOptions

class WeWorkIPPW(_PluginBase):
    # 插件名称
    plugin_name = "企微配置IPpw版"
    # 插件描述
    plugin_desc = "!!docker用户用这个版本!!定时获取最新动态公网IP，配置到企业微信应用的可信IP列表里。(已适配官方DrissionPage内核)"
    # 插件图标
    plugin_icon = "https://github.com/suraxiuxiu/MoviePilot-Plugins/blob/main/icons/micon.png?raw=true"
    # 插件版本
    plugin_version = "2.4.4"  
    # 插件作者
    plugin_author = "suraxiuxiu"
    # 作者主页
    author_url = "https://github.com/suraxiuxiu/MoviePilot-Plugins"
    # 插件配置项ID前缀
    plugin_config_prefix = "weworkippw_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 2

    script_path = os.path.abspath(__file__)
    script_dir = os.path.dirname(script_path)
    qr_path = 'QR.png'
    qr_path = os.path.join(script_dir, qr_path)
    if os.path.exists(qr_path):
        os.remove(qr_path)
        
    _ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    _ip_urls = ["https://myip.ipip.net", "https://ddns.oray.com/checkip", "https://ip.3322.net", "https://4.ipw.cn"]
    _current_ip_address = '192.168.1.1'
    _wechatUrl = f'https://work.weixin.qq.com/wework_admin/frame#/apps/modApiApp/00000000000'
    _urls = []
    _cookie_header = ""
    _cookie_from_CC = ""
    _qr_send_users = ""
    _overwrite = True
    _use_cookiecloud = True
    _cookie_valid = False
    _ip_changed = False
    _refresh_cron = "*/5 * * * *"
    _status_cron = "0 * * * *"
    _check_cron = "*/11 * * * *"
    _enabled = False
    _onlyonce = False
    _cookiecloud = CookieCloudHelper()
    _code = 0
    _pattern = r"^#\d{6}$"
    _schedule_login = False
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        self._wechatUrl = ''
        self._cookie_header = ""
        self._qr_send_users = ""
        self._cookie_from_CC = ""
        self._overwrite = True
        self._use_cookiecloud = True
        self._cookie_valid = False
        self._ip_changed = True
        self._urls = []
        if config:
            self._enabled = config.get("enabled")
            self._check_cron = config.get("cron")
            self._status_cron = config.get("status_cron")
            self._onlyonce = config.get("onlyonce")
            self._wechatUrl = config.get("wechatUrl")
            self._cookie_header = config.get("cookie_header")
            self._qr_send_users = config.get("qr_send_users")
            self._cookie_from_CC = config.get("cookie_from_CC")
            self._overwrite = config.get("overwrite")
            self._current_ip_address = config.get("current_ip_address")
            self._use_cookiecloud = config.get("use_cookiecloud")
            self._schedule_login = config.get("schedule_login")
            self._cookie_valid = config.get("cookie_valid")
            self._ip_changed = config.get("ip_changed")
            
        self._urls = self._wechatUrl.split(',')
        if self._ip_changed is None: self._ip_changed = True
        if self._cookie_valid is None: self._cookie_valid = False
        if self._use_cookiecloud is None: self._use_cookiecloud = True
        if self._overwrite is None: self._overwrite = True
        if self._schedule_login is None: self._schedule_login = False
        if self._status_cron is None: self._status_cron = "0 * * * *"
        if self._check_cron is None: self._check_cron = "*/11 * * * *"
        
        self.stop_service()

        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)       
            if self._onlyonce:
                logger.info("立即检测公网IP")
                self._scheduler.add_job(
                    func=self.check,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="检测公网IP",
                )
                self._onlyonce = False

            if not self._cookie_valid:
                self._scheduler.add_job(
                    func=self.refresh_cookie,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=1),
                    name="插件初始化检测到缓存失效"
                )
            else:
                self.create_refresh_job()

            if not self._schedule_login:
                self._scheduler.add_job(
                    func=self.send_cookie_status,
                    trigger=CronTrigger.from_crontab(self._status_cron),
                    name="cookie失效通知",
                    id="send_status"
                )
                if not self._cookie_valid:
                    self._scheduler.add_job(
                        func=self.send_cookie_status,
                        trigger="date",
                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                        name="初始化检测失效通知",
                    )
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()
                
        self.__update_config()

    def _get_page(self):
        """初始化并返回 DrissionPage 浏览器实例"""
        co = ChromiumOptions()
        co.headless()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1920,1080')
        co.set_argument('--ignore-certificate-errors')
        try:
            return ChromiumPage(co)
        except Exception as e:
            logger.error(f"启动浏览器失败: {e}")
            return None

    @eventmanager.register(EventType.PluginAction)
    def check(self, event: Event = None):
        if not self._enabled:
            logger.error("插件未开启")
            return

        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "weworkippw":
                return
            logger.info("收到命令，开始检测公网IP ...")
            self.post_message(channel=event.event_data.get("channel"),
                              title="开始检测公网IP ...",
                              userid=event.event_data.get("user"))

        logger.info("开始检测公网IP")
        if self.CheckIP():
            self.ChangeIP()
            self.__update_config()

        logger.info("检测公网IP完毕")
        if event:
            self.post_message(channel=event.event_data.get("channel"),
                              title="检测公网IP完毕",
                              userid=event.event_data.get("user"))
        
    def CheckIP(self):
        if not self._cookie_valid:
            logger.error("cookie以过期,跳过IP检测")
            return False
        if not self._ip_changed:
            return True
            
        ip_address = "获取IP失败"
        for url in self._ip_urls:
            ip_address = self.get_ip_from_url(url)
            if ip_address != "获取IP失败":
                logger.info(f"IP获取成功: {url}: {ip_address}")
                break
            else:
                logger.error(f"请求网址失败: {url}")
                
        if ip_address == "获取IP失败":
            logger.error("获取IP失败") 
            return False      
            
        if ip_address != self._current_ip_address:
            logger.info("检测到IP变化")
            self._current_ip_address = ip_address
            self._ip_changed = False
            return True
        return False

    def get_ip_from_url(self, url):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                ip_address = re.search(self._ip_pattern, response.text)
                if ip_address:
                    return ip_address.group()
            return "获取IP失败"
        except Exception as e:
            logger.warning(f"{url}获取IP失败,Error: {e}")
            return "获取IP失败"
            
    def ChangeIP(self):
        logger.info("开始请求企业微信管理更改可信IP")
        if not self.check_connect():
            logger.error("网络连接失败,跳过本次更改IP")
            return
            
        page = self._get_page()
        if not page:
            return
            
        try:    
            cookie = self.get_cookie()
            if not cookie:
                logger.error('cookie为空,请检查CC配置和插件手动填写项')
                self._cookie_valid = False
                return
                
            page.get(self._urls[0])
            for c in cookie:
                page.set.cookies(c)
            
            page.get(self._urls[0])
            time.sleep(2)
            
            login_ele = page.ele('.login_stage_title_text', timeout=3)
            if login_ele:
                logger.info("cookie失效,请重新获取")
                self._cookie_valid = False
                return
            else:
                logger.info("加载企微管理界面成功")
                self._cookie_valid = True

            for index, url in enumerate(self._urls):           
                logger.info(f"正在更改第{index+1}个应用的可信IP")
                page.get(url)
                
                btn = page.ele('.app_card_operate.js_show_ipConfig_dialog', timeout=10)
                if not btn:
                    logger.error("未找到配置IP按钮")
                    continue
                btn.click()
                
                input_area = page.ele('textarea.js_ipConfig_textarea', timeout=10)
                if not input_area:
                    logger.error("未找到IP输入框")
                    continue
                
                existing_ip = input_area.attr('value') or ""
                input_area.clear()
                if self._overwrite:
                    input_area.input(self._current_ip_address)
                else:
                    input_area.input(f'{existing_ip};{self._current_ip_address}')
                
                confirm_btn = page.ele('.js_ipConfig_confirmBtn', timeout=5)
                if confirm_btn:
                    confirm_btn.click()
                    time.sleep(1)
                    logger.info(f"更改第{index+1}个应用的可信IP成功")
                
            self._ip_changed = True
            
        except Exception as e:
            logger.error(f"更改可信IP失败:{e}")
        finally:
            if page:
                page.quit()
    
    def refresh_cookie(self, _login=True):
        logger.info("开始刷新企业微信缓存")
        if not self.check_connect():
            logger.error("网络连接失败,跳过本次缓存保活")
            return
            
        page = self._get_page()
        if not page:
            return
            
        try:    
            cookie = self.get_cookie()
            if not cookie:
                logger.error('cookie为空,请检查CC配置和插件手动填写项')
                self._cookie_valid = False
                self._handle_cookie_invalid(_login)
                return
                
            page.get(self._urls[0])
            for c in cookie:
                page.set.cookies(c)
                
            page.get(self._urls[0])
            time.sleep(2)
            
            login_ele = page.ele('.login_stage_title_text', timeout=3)
            if login_ele:
                logger.info("cookie失效,请重新获取")
                self._cookie_valid = False
                self._handle_cookie_invalid(_login)
            else:
                logger.info("cookie有效校验成功")
                self._cookie_valid = True
                
            self.__update_config()
        except Exception as e:
            logger.error(f"cookie校验失败:{e}") 
            self._cookie_valid = False
            self.__update_config()   
        finally:
            if page:
                page.quit()

    def _handle_cookie_invalid(self, _login):
        if self._schedule_login:
            if self._scheduler.get_job("refresh_cookie"):
                self._scheduler.remove_job("refresh_cookie")
            if not self._scheduler.get_job("wwlogin") and _login:
                self.create_login_job()
        else:
            if not self._scheduler.get_job("refresh_cookie"):
                self.create_refresh_job()

    def parse_cookie_header(self, cookie_header):
        try:
            cookies = []
            for cookie in cookie_header.split(';'):
                if '=' in cookie:
                    name, value = cookie.strip().split('=', 1)
                    cookies.append({
                        'name': name,
                        'value': value,
                        'domain': '.work.weixin.qq.com',
                        'path': '/'
                    })
            return cookies
        except Exception as e:
            logger.error(f"cookie转换失败,可能格式错误:{e}") 
            self._cookie_valid = False
            return ''
    
    def get_cookie(self):
        cookie_header = ''
        try:
            if self._cookie_valid:
                return self._cookie_from_CC
            if self._use_cookiecloud:
                logger.info("尝试从CookieCloud同步企微cookie ...")
                cookies, msg = self._cookiecloud.download()
                if not cookies:
                    logger.error(f"CookieCloud获取cookie失败,将使用手动配置cookie,失败原因：{msg}")
                    cookie_header = self._cookie_header
                else:
                    for domain, cookie in cookies.items():
                        if domain == ".work.weixin.qq.com":
                            cookie_header = cookie
                            break
                    if cookie_header == '':
                        cookie_header = self._cookie_header
            else:                
                cookie_header = self._cookie_header
                
            if not cookie_header:
                logger.error("未获取到任何cookie")
                return ''
                
            cookie = self.parse_cookie_header(cookie_header)
            if not cookie:
                return ''
                
            self._cookie_from_CC = cookie
            self.__update_config()
            return cookie
        except Exception as e:
            logger.error(f"获取cookie失败:{e}") 
            return ''

    def login(self):
        logger.info("开始登录企业微信")
        self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="开始登录企业微信", userid=self._qr_send_users)
        self.refresh_cookie(_login=False)
        if self._cookie_valid:
            return

        page = self._get_page()
        if not page:
            return
            
        try:
            page.get(self._urls[0])
            
            iframe_ele = page.ele('iframe[src*="login_qrcode"]', timeout=10)
            if not iframe_ele:
                raise ValueError("未找到登录二维码iframe")
                
            frame = iframe_ele.get_frame()
            qr_img = frame.ele('.qrcode_login_img', timeout=10)
            if not qr_img:
                raise ValueError("未找到二维码图片元素")
                
            qr_img_relative_url = qr_img.attr('src')
            base_url = page.url
            absolute_url = urljoin(base_url, qr_img_relative_url)
            
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="点击扫描二维码登录企业微信", image=absolute_url, link=absolute_url, userid=self._qr_send_users)
            
            response = requests.get(absolute_url)
            if response.status_code == 200:
                with open(self.qr_path, "wb") as file:
                    file.write(response.content)
                logger.info("二维码已保存")
                
            wait_time = 0
            new_url = False
            while wait_time < 60:
                time.sleep(1)
                wait_time += 1
                current_url = page.url
                if 'work.weixin.qq.com' in current_url and 'login' not in current_url and 'qrcode' not in current_url:
                    new_url = True
                    break
                    
            if not new_url:
                raise ValueError("等待扫描超时")
                
            if 'mobile_confirm' in page.url:
                self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="检测到登录验证，请以 #123456 的格式回复验证码，两分钟后超时", userid=self._qr_send_users)
                
                wait_code_time = 0
                while 'mobile_confirm' in page.url:
                    self._code = 0
                    wait_code_time_inner = 0
                    while self._code == 0:
                        time.sleep(2)
                        wait_code_time += 2
                        if wait_code_time > 120:
                            raise ValueError("验证超时,终止本次登录")
                            
                    input_element = page.ele('.inner_input', timeout=5)
                    if input_element:
                        input_element.clear()
                        input_element.input(self._code)
                    
                    for _ in range(5):
                        time.sleep(1)
                        if 'mobile_confirm' not in page.url:
                            break
                            
            cookies = page.cookies()
            cookies2 = ';'.join([f"{c['name']}={c['value']}" for c in cookies])
            self._cookie_from_CC = self.parse_cookie_header(cookies2)
            self._cookie_valid = True
            
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="登录企业微信成功", userid=self._qr_send_users)
            logger.info("登录企业微信成功")
            
            if not self._scheduler.get_job("refresh_cookie"):
                self.create_refresh_job()
            if self._scheduler.get_job("wwlogin"):
                self._scheduler.remove_job("wwlogin")
                
        except Exception as e:
            logger.error(f"登录失败:{e}")
            self.login_fail()
        finally:
            if page:
                page.quit()
            self.__update_config()
            if os.path.exists(self.qr_path):
                os.remove(self.qr_path)
    
    def create_refresh_job(self):
        try:
            self._scheduler.add_job(
                func=self.refresh_cookie,
                trigger=CronTrigger.from_crontab(self._refresh_cron),
                name="延续企业微信cookie有效时间",
                id="refresh_cookie"
            )
        except Exception as err:
            logger.error(f"定时刷新企业微信缓存任务配置错误：{err}")
        
    def create_login_job(self):
        try:
            self._scheduler.add_job(
                func=self.login,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5),
                name="唤起企业微信登录"
            )
        except Exception as err:
            logger.error(f"定时唤起企业登录任务配置错误：{err}")

    def login_fail(self):
        self._cookie_valid = False
        if self._schedule_login:
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="登录失败", text="已开启自动登录，即将开始下一轮登录。", userid=self._qr_send_users)
            self.create_login_job()
        else:
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="登录失败", text="如需再次登录，请回复\n#登录企业微信", userid=self._qr_send_users)
            
    def check_connect(self):
        try:
            response = requests.get(self._urls[0], timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False
                
    def __update_config(self):
        self.update_config(
            {
                "enabled": self._enabled, "onlyonce": self._onlyonce, "cron": self._check_cron,
                "wechatUrl": self._wechatUrl, "cookie_header": self._cookie_header, "qr_send_users": self._qr_send_users,
                "cookie_from_CC": self._cookie_from_CC, "overwrite": self._overwrite, "current_ip_address": self._current_ip_address,
                "use_cookiecloud": self._use_cookiecloud, "cookie_valid": self._cookie_valid, "ip_changed": self._ip_changed,
                "schedule_login": self._schedule_login, "status_cron": self._status_cron
            }
        )

    @eventmanager.register(EventType.UserMessage)
    def receive_message(self, event: Event):
        if not self._enabled: return
        text = event.event_data.get("text")
        if re.match(self._pattern, text):
            self._code = text[1:]
            return
        if text == "#登录企业微信":
            if self._cookie_valid:
                self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="缓存有效，无需登录", userid=self._qr_send_users)
                return
            self._scheduler.add_job(func=self.login, trigger="date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3), name="登录企业微信")
    
    def send_cookie_status(self):
        if not self._cookie_valid:
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="企业微信Cookie失效", text="回复下述指令唤起一次登录\n#登录企业微信", userid=self._qr_send_users)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [{"cmd": "/weworkippw", "event": EventType.PluginAction, "desc": "微信应用检测动态IP", "category": "", "data": {"action": "weworkippw"}}]

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._check_cron:
            return [{"id": "WeWorkIPPW", "name": "微信应用自动配置动态公网IP", "trigger": CronTrigger.from_crontab(self._check_cron), "func": self.check, "kwargs": {}}]
        return []
            
    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即检测一次IP"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "overwrite", "label": "覆盖模式"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "use_cookiecloud", "label": "使用CookieCloud获取cookie"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "schedule_login", "label": "自动登录"}}]}
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "cron", "label": "检测IP周期", "placeholder": "*/11 * * * *"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "status_cron", "label": "Cookie失效通知周期", "placeholder": "0 * * * *"}}]}
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "cookie_header", "label": "非必填项:COOKIE", "rows": 1, "placeholder": "手动提取HeaderString格式的Cookie"}}]}
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "wechatUrl", "label": "企微应用管理地址", "rows": 2, "placeholder": "多个地址用英文逗号分隔"}}]}
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "qr_send_users", "label": "接收通知的用户ID", "placeholder": "留空则发送给全部成员"}}]}
                    ]}
                ],
            }
        ], {
            "enabled": False, "onlyonce": False, "overwrite": True, "use_cookiecloud": True, "schedule_login": False,
            "cron": "*/11 * * * *", "status_cron": "0 * * * *", "cookie_header": "", "wechatUrl": "", "qr_send_users": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
