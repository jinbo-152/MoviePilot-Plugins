import base64
import re
import os
import sys
import time
import io
import requests
from urllib.parse import urljoin
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
from playwright.sync_api import sync_playwright

# 尝试导入 PyCookieCloud (参考 DynamicWeChat)
try:
    from app.plugins.dynamicwechat.update_help import PyCookieCloud
except ImportError:
    PyCookieCloud = None

class WeWorkIPPW(_PluginBase):
    plugin_name = "企微配置IPpw版"
    plugin_desc = "定时获取动态公网IP配置到企微。融合DynamicWeChat的二维码推送与验证码自动输入逻辑。"
    plugin_icon = "https://github.com/suraxiuxiu/MoviePilot-Plugins/blob/main/icons/micon.png?raw=true"
    plugin_version = "2.6.0"
    plugin_author = "suraxiuxiu & RamenRa"
    author_url = "https://github.com/suraxiuxiu/MoviePilot-Plugins"
    plugin_config_prefix = "weworkippw_"
    plugin_order = 20
    auth_level = 2

    script_path = os.path.abspath(__file__)
    script_dir = os.path.dirname(script_path)
    qr_path = os.path.join(script_dir, 'QR.png')
    if os.path.exists(qr_path): os.remove(qr_path)
        
    _ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    _ip_urls = ["https://myip.ipip.net", "https://ddns.oray.com/checkip", "https://ip.3322.net", "https://4.ipw.cn"]
    _current_ip_address = '192.168.1.1'
    _wechatUrl = f'https://work.weixin.qq.com/wework_admin/frame#/apps/modApiApp/00000000000'
    _loginUrl = 'https://work.weixin.qq.com/wework_admin/loginpage_wx?from=myhome'
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
    _schedule_login = False
    
    _cookiecloud = CookieCloudHelper()
    _cc_server = None
    _server = f'http://localhost:{settings.NGINX_PORT}/cookiecloud'
    
    # 融合 DynamicWeChat 的属性
    _helloimg_s_token = ""
    _pushplus_token = ""
    _qr_code_image = None
    text = ""  # 用于存储接收到的验证码

    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        self._server = f'http://localhost:{settings.NGINX_PORT}/cookiecloud'
        self._wechatUrl = ''
        self._cookie_header = ""
        self._qr_send_users = ""
        self._cookie_from_CC = ""
        self._overwrite = True
        self._use_cookiecloud = True
        self._cookie_valid = False
        self._ip_changed = True
        self._urls = []
        self._helloimg_s_token = ""
        self._pushplus_token = ""
        
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
            self._helloimg_s_token = config.get("helloimg_s_token")
            self._pushplus_token = config.get("pushplus_token")
            
        self._urls = self._wechatUrl.split(',') if self._wechatUrl else []
        
        if self._use_cookiecloud and PyCookieCloud:
            self._cc_server = PyCookieCloud(url=self._server, uuid=settings.COOKIECLOUD_KEY, password=settings.COOKIECLOUD_PASSWORD)

        # 默认值处理
        self._ip_changed = self._ip_changed if self._ip_changed is not None else True
        self._cookie_valid = self._cookie_valid if self._cookie_valid is not None else False
        self._use_cookiecloud = self._use_cookiecloud if self._use_cookiecloud is not None else True
        self._overwrite = self._overwrite if self._overwrite is not None else True
        self._schedule_login = self._schedule_login if self._schedule_login is not None else False
        self._status_cron = self._status_cron or "0 * * * *"
        self._check_cron = self._check_cron or "*/11 * * * *"
        
        self.stop_service()

        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)       
            if self._onlyonce:
                self._scheduler.add_job(func=self.check, trigger="date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3), name="检测公网IP")
                self._onlyonce = False

            if not self._cookie_valid:
                self._scheduler.add_job(func=self.refresh_cookie, trigger="date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=1), name="插件初始化检测到缓存失效")
            else:
                self.create_refresh_job()

            if not self._schedule_login:
                self._scheduler.add_job(func=self.send_cookie_status, trigger=CronTrigger.from_crontab(self._status_cron), name="cookie失效通知", id="send_status")
                if not self._cookie_valid:
                    self._scheduler.add_job(func=self.send_cookie_status, trigger="date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3), name="初始化检测失效通知")
                    
            if self._scheduler.get_jobs():
                self._scheduler.start()
        self.__update_config()

    @eventmanager.register(EventType.PluginAction)
    def check(self, event: Event = None):
        if not self._enabled: return
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "weworkippw": return
            self.post_message(channel=event_data.get("channel"), title="开始检测公网IP ...", userid=event_data.get("user"))

        if self.CheckIP():
            self.ChangeIP()
            self.__update_config()

        if event:
            self.post_message(channel=event.event_data.get("channel"), title="检测公网IP完毕", userid=event.event_data.get("user"))
        
    def CheckIP(self):
        if not self._cookie_valid: return False
        if not self._ip_changed: return True
            
        ip_address = "获取IP失败"
        for url in self._ip_urls:
            ip_address = self.get_ip_from_url(url)
            if ip_address != "获取IP失败": break
                
        if ip_address == "获取IP失败": return False      
        if ip_address != self._current_ip_address:
            self._current_ip_address = ip_address
            self._ip_changed = False
            return True
        return False

    def get_ip_from_url(self, url):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                ip_match = re.search(self._ip_pattern, response.text)
                if ip_match: return ip_match.group()
            return "获取IP失败"
        except Exception:
            return "获取IP失败"
            
    def ChangeIP(self):
        if not self.check_connect(): return
        try:    
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                cookie = self.get_cookie()
                if not cookie:
                    self._cookie_valid = False
                    browser.close()
                    return
                    
                context.add_cookies(cookie)
                page = context.new_page()
                page.goto(self._urls[0] if self._urls else self._loginUrl)
                time.sleep(2)
                
                if page.locator('.login_stage_title_text').is_visible():
                    self._cookie_valid = False
                    browser.close()
                    return
                    
                self._cookie_valid = True
                for index, url in enumerate(self._urls):           
                    page.goto(url)
                    page.wait_for_selector('div.app_card_operate.js_show_ipConfig_dialog')
                    page.locator('div.app_card_operate.js_show_ipConfig_dialog').click()
                    page.wait_for_selector('textarea.js_ipConfig_textarea')
                    input_area = page.locator('textarea.js_ipConfig_textarea')
                    existing_ip = input_area.input_value()
                    input_area.fill(self._current_ip_address if self._overwrite else f'{existing_ip};{self._current_ip_address}')
                    page.locator('.js_ipConfig_confirmBtn').click()
                    time.sleep(1)
                self._ip_changed = True
                browser.close() 
        except Exception as e:
            logger.error(f"更改可信IP失败:{e}")
    
    def refresh_cookie(self, _login=True):
        if not self.check_connect(): return
        try:    
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                cookie = self.get_cookie()
                if not cookie:
                    self._cookie_valid = False
                    self._handle_cookie_invalid(_login)
                    browser.close()
                    return
                    
                context.add_cookies(cookie)
                page = context.new_page()
                page.goto(self._urls[0] if self._urls else self._loginUrl)
                time.sleep(2)
                
                if page.locator('.login_stage_title_text').is_visible():
                    self._cookie_valid = False
                    self._handle_cookie_invalid(_login)
                else:
                    self._cookie_valid = True
                browser.close()
            self.__update_config()
        except Exception as e:
            self._cookie_valid = False
            self.__update_config()   

    # ================= 融合 DynamicWeChat 核心逻辑 =================
    def find_qrc(self, page):
        """使用 DynamicWeChat 的方式获取二维码"""
        try:
            page.wait_for_selector("iframe", timeout=5000)
            iframe_element = page.query_selector("iframe")
            frame = iframe_element.content_frame()
            qr_code_element = frame.query_selector("img.qrcode_login_img")
            if qr_code_element:
                qr_code_url = qr_code_element.get_attribute('src')
                if qr_code_url.startswith("/"):
                    qr_code_url = "https://work.weixin.qq.com" + qr_code_url
                qr_code_data = requests.get(qr_code_url).content
                self._qr_code_image = io.BytesIO(qr_code_data)
                return True
            return False
        except Exception:
            return False

    def login(self):
        """融合 DynamicWeChat 的推送与验证码输入逻辑"""
        self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="开始登录企业微信", userid=self._qr_send_users)
        self.refresh_cookie(_login=False)
        if self._cookie_valid: return

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                page.goto(self._loginUrl)
                time.sleep(3)
                
                if self.find_qrc(page):
                    if self._pushplus_token and self._helloimg_s_token:
                        img_src, _ = self.upload_image(self._qr_code_image)
                        if img_src:
                            self.send_pushplus_message("企微登录", f"企业微信登录二维码<br/><img src='{img_src}' />")
                            logger.info("二维码已推送，等待 90 秒内扫码")
                            time.sleep(90)
                    else:
                        # 没配 token 就走 MP 内部消息
                        self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="请扫码登录(未配图床Token)", userid=self._qr_send_users)
                        time.sleep(60)
                        
                    if self.check_login_status(page):
                        self._update_cookie(page, context)
                        self._cookie_valid = True
                        self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="登录企业微信成功", userid=self._qr_send_users)
                        if not self._scheduler.get_job("refresh_cookie"): self.create_refresh_job()
                    else:
                        self.login_fail()
                else:
                    logger.error("未找到二维码")
                    self.login_fail()
                browser.close()
        except Exception as e:
            logger.error(f"登录失败:{e}")
            self.login_fail()
        self.__update_config()

    def check_login_status(self, page):
        """使用 DynamicWeChat 的方式处理验证码"""
        time.sleep(3)
        try:
            if page.wait_for_selector('#check_corp_info', timeout=5000):
                return True
        except Exception: pass

        try:
            captcha_panel = page.wait_for_selector('.receive_captcha_panel', timeout=5000)
            if captcha_panel:
                logger.info("检测到验证码面板，等待用户发送验证码...")
                self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="请回复6位验证码(如: 123456？)", userid=self._qr_send_users)
                time.sleep(30)  # 等待用户回复
                if self.text and len(self.text) >= 6:
                    logger.info(f"收到验证码：{self.text[:6]}，模拟键盘输入")
                    for digit in self.text[:6]:
                        page.keyboard.press(digit)
                        time.sleep(0.3)
                    confirm_button = page.wait_for_selector('.confirm_btn', timeout=5000)
                    if confirm_button: confirm_button.click()
                    time.sleep(3)
                    if page.wait_for_selector('#check_corp_info', timeout=10000):
                        return True
                else:
                    logger.error("未收到验证码或格式错误")
                    return False
        except Exception: pass
        return False

    def _update_cookie(self, page, context):
        """使用 DynamicWeChat 的方式更新 CookieCloud"""
        if self._use_cookiecloud and self._cc_server:
            try:
                if self._cc_server.check_connection():
                    current_url = page.url
                    current_cookies = context.cookies(current_url)
                    formatted_cookies = {}
                    for cookie in current_cookies:
                        domain = cookie['domain']
                        if domain not in formatted_cookies: formatted_cookies[domain] = []
                        formatted_cookies[domain].append(cookie)
                    self._cc_server.update_cookie({'cookie_data': formatted_cookies})
                    logger.info("更新CookieCloud成功")
            except Exception as e:
                logger.error(f"更新cookie发生错误: {e}")

    def send_pushplus_message(self, title, content):
        pushplus_url = f"http://www.pushplus.plus/send/{self._pushplus_token}"
        try:
            requests.post(pushplus_url, json={"title": title, "content": content, "template": "html"})
        except Exception as e:
            logger.error(f"PushPlus 发送失败: {e}")

    def upload_image(self, file_obj):
        helloimg_token = "Bearer " + self._helloimg_s_token
        headers = {"Authorization": helloimg_token, "Accept": "application/json"}
        files = {"file": ('qr_code.png', file_obj, 'image/png')}
        expired_at = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        data = {"permission": 1, "strategy_id": 1, "album_id": 1, "expired_at": expired_at}
        try:
            response = requests.post("https://www.helloimg.com/api/v1/upload", headers=headers, files=files, data=data)
            res_json = response.json()
            if res_json.get('status'):
                img_src = res_json['data']['links']['html']
                return img_src.split('"')[1], None
            return None, None
        except Exception as e:
            logger.error(f"图床上传失败: {e}")
            return None, None
    # ================================================================

    def _handle_cookie_invalid(self, _login):
        if self._schedule_login:
            if self._scheduler.get_job("refresh_cookie"): self._scheduler.remove_job("refresh_cookie")
            if not self._scheduler.get_job("wwlogin") and _login: self.create_login_job()
        else:
            if not self._scheduler.get_job("refresh_cookie"): self.create_refresh_job()

    def parse_cookie_header(self, cookie_header):
        try:
            cookies = []
            for cookie in cookie_header.split(';'):
                if '=' in cookie:
                    name, value = cookie.strip().split('=', 1)
                    cookies.append({'name': name, 'value': value, 'domain': '.work.weixin.qq.com', 'path': '/'})
            return cookies
        except Exception: return ''
    
    def get_cookie(self):
        cookie_header = ''
        try:
            if self._cookie_valid: return self._cookie_from_CC
            if self._use_cookiecloud:
                cookies, _ = self._cookiecloud.download()
                if cookies:
                    for domain, cookie in cookies.items():
                        if domain == ".work.weixin.qq.com": cookie_header = cookie; break
            if not cookie_header: cookie_header = self._cookie_header
            if not cookie_header: return ''
            cookie = self.parse_cookie_header(cookie_header)
            self._cookie_from_CC = cookie
            return cookie
        except Exception: return ''

    def create_refresh_job(self):
        try: self._scheduler.add_job(func=self.refresh_cookie, trigger=CronTrigger.from_crontab(self._refresh_cron), name="延续企微cookie", id="refresh_cookie")
        except Exception as err: logger.error(f"刷新任务配置错误：{err}")
        
    def create_login_job(self):
        try: self._scheduler.add_job(func=self.login, trigger="date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5), name="唤起登录")
        except Exception as err: logger.error(f"登录任务配置错误：{err}")

    def login_fail(self):
        self._cookie_valid = False
        if self._schedule_login: self.create_login_job()
        else: self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="登录失败", text="请回复\n#登录企业微信", userid=self._qr_send_users)
            
    def check_connect(self):
        try: return requests.get(self._urls[0] if self._urls else self._loginUrl, timeout=10).status_code == 200
        except Exception: return False
                
    def __update_config(self):
        self.update_config({
            "enabled": self._enabled, "onlyonce": self._onlyonce, "cron": self._check_cron,
            "wechatUrl": self._wechatUrl, "cookie_header": self._cookie_header, "qr_send_users": self._qr_send_users,
            "cookie_from_CC": self._cookie_from_CC, "overwrite": self._overwrite, "current_ip_address": self._current_ip_address,
            "use_cookiecloud": self._use_cookiecloud, "cookie_valid": self._cookie_valid, "ip_changed": self._ip_changed,
            "schedule_login": self._schedule_login, "status_cron": self._status_cron,
            "pushplus_token": self._pushplus_token, "helloimg_s_token": self._helloimg_s_token
        })

    @eventmanager.register(EventType.UserMessage)
    def receive_message(self, event: Event):
        if not self._enabled: return
        text = event.event_data.get("text")
        # 兼容 DynamicWeChat 的验证码格式 (6位数字+问号)
        match = re.match(r'^(\d{6})[？?]$', text)
        if match:
            self.text = match.group(1)
            logger.info(f"收到验证码：{self.text}")
            return
        if text == "#登录企业微信":
            if self._cookie_valid: return
            self._scheduler.add_job(func=self.login, trigger="date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3), name="登录企业微信")
    
    def send_cookie_status(self):
        if not self._cookie_valid:
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="企微Cookie失效", text="回复 #登录企业微信", userid=self._qr_send_users)

    def get_state(self) -> bool: return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [{"cmd": "/weworkippw", "event": EventType.PluginAction, "desc": "微信应用检测动态IP", "category": "", "data": {"action": "weworkippw"}}]

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._check_cron:
            return [{"id": "WeWorkIPPW", "name": "微信应用自动配置动态公网IP", "trigger": CronTrigger.from_crontab(self._check_cron), "func": self.check, "kwargs": {}}]
        return []
            
    def get_api(self) -> List[Dict[str, Any]]: pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {"component": "VForm", "content": [
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即检测一次IP"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "overwrite", "label": "覆盖模式"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "use_cookiecloud", "label": "使用CookieCloud"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "schedule_login", "label": "自动登录"}}]}
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "cron", "label": "检测IP周期", "placeholder": "*/11 * * * *"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "status_cron", "label": "失效通知周期", "placeholder": "0 * * * *"}}]}
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "cookie_header", "label": "手动COOKIE(选填)", "rows": 1}}]}
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "wechatUrl", "label": "企微应用URL", "rows": 2, "placeholder": "多个用逗号分隔"}}]}
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "pushplus_token", "label": "PushPlus Token", "placeholder": "用于推送二维码"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "helloimg_s_token", "label": "HelloImg Token", "placeholder": "图床Token"}}]}
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "qr_send_users", "label": "通知用户ID", "placeholder": "留空发给全部"}}]}
                ]}
            ]}
        ], {
            "enabled": False, "onlyonce": False, "overwrite": True, "use_cookiecloud": True, "schedule_login": False,
            "cron": "*/11 * * * *", "status_cron": "0 * * * *", "cookie_header": "", "wechatUrl": "", "qr_send_users": "",
            "pushplus_token": "", "helloimg_s_token": ""
        }

    def get_page(self) -> List[dict]: pass

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running: self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
