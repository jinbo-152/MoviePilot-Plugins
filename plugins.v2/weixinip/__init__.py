import re
import os
import sys
import time
import requests
import io
import subprocess
import importlib
from datetime import datetime, timedelta
import pytz
from typing import Optional, Tuple, List, Dict, Any

from app.core.event import eventmanager, Event
from app.schemas.types import EventType, MessageChannel, NotificationType
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.log import logger
from app.plugins import _PluginBase
from app.core.config import settings
from app.helper.cookiecloud import CookieCloudHelper

# ================= 自动环境修复机制 =================
def _ensure_package(package_name, import_name=None):
    if import_name is None: import_name = package_name
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        logger.info(f"环境中未找到 {import_name}，正在尝试自动安装 {package_name} ...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package_name, "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "-q"])
            logger.info(f"✅ {package_name} 自动安装成功！")
            return True
        except Exception as e:
            logger.error(f"❌ 自动安装 {package_name} 失败: {e}")
            return False

_HAS_DRISSION = _ensure_package("DrissionPage")
if _HAS_DRISSION:
    from DrissionPage import ChromiumPage, ChromiumOptions
else:
    ChromiumPage = None
    ChromiumOptions = None

# 尝试导入辅助类，防止因缺少文件导致插件崩溃
try:
    from app.plugins.dynamicwechat.update_help import PyCookieCloud
except ImportError:
    PyCookieCloud = None
# ====================================================

class WeWorkIPPW(_PluginBase):
    plugin_name = "企微配置IP(融合版)"
    plugin_desc = "支持PushPlus+图床推送二维码，支持短信验证码自动输入。定时获取动态公网IP配置到企微可信IP。(已适配官方DrissionPage内核)"
    plugin_icon = "Wecom_A.png"
    plugin_version = "2.5.0"
    plugin_author = "RamenRa & suraxiuxiu"
    author_url = "https://github.com/RamenRa/MoviePilot-Plugins"
    plugin_config_prefix = "weworkippw_"
    plugin_order = 47
    auth_level = 2

    _enabled = False
    _cron = None
    _onlyonce = False
    _ip_changed = False
    _forced_update = False
    _cc_server = None

    _ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    _ip_urls = ["https://myip.ipip.net", "https://ddns.oray.com/checkip", "https://ip.3322.net", "https://4.ipw.cn"]
    _current_ip_address = '0.0.0.0'
    _wechatUrl = 'https://work.weixin.qq.com/wework_admin/loginpage_wx?from=myhome'
    _refresh_cron = '*/20 * * * *'
    
    _input_id_list = ''
    _helloimg_s_token = ""
    _pushplus_token = ""
    _qr_code_image = None
    text = ""  # 用于存储接收到的验证码
    
    _use_cookiecloud = True
    _cookie_from_CC = ""
    _cookie_header = ""
    _server = f'http://localhost:{settings.NGINX_PORT}/cookiecloud'
    _cookiecloud = CookieCloudHelper()
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        self._server = f'http://localhost:{settings.NGINX_PORT}/cookiecloud'
        self._helloimg_s_token = ''
        self._pushplus_token = ''
        self._ip_changed = True
        self._forced_update = False
        self._use_cookiecloud = True
        self._input_id_list = ''
        self._cookie_header = ""
        self._cookie_from_CC = ""
        
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce")
            self._input_id_list = config.get("input_id_list")
            self._current_ip_address = config.get("current_ip_address") or self.get_ip_from_url(self._ip_urls[0])
            self._pushplus_token = config.get("pushplus_token")
            self._helloimg_s_token = config.get("helloimg_s_token")
            self._cookie_from_CC = config.get("cookie_from_CC")
            self._forced_update = config.get("forced_update")
            self._use_cookiecloud = config.get("use_cookiecloud")
            self._cookie_header = config.get("cookie_header")
            self._ip_changed = config.get("ip_changed")

        if self._use_cookiecloud and PyCookieCloud:
            self._cc_server = PyCookieCloud(url=self._server, uuid=settings.COOKIECLOUD_KEY, password=settings.COOKIECLOUD_PASSWORD)

        self.stop_service()
        
        if not _HAS_DRISSION:
            logger.error("⚠️ 浏览器核心库缺失，插件已暂停运行。请查看日志中的手动安装提示！")
            self._enabled = False
            self.__update_config()
            return

        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            if self._onlyonce or self._forced_update:
                logger.info("立即检测公网IP")
                self._scheduler.add_job(func=self.check, trigger='date', run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3), name="检测公网IP")
                self._onlyonce = False

            try:
                self._scheduler.add_job(func=self.refresh_cookie, trigger=CronTrigger.from_crontab(self._refresh_cron), name="延续企业微信cookie有效时间")
            except Exception as err:
                logger.error(f"定时任务配置错误：{err}")

            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()
                if self._forced_update:
                    time.sleep(4)
                    self._forced_update = False
        self.__update_config()

    def _get_page(self):
        if not _HAS_DRISSION: return None
        co = ChromiumOptions()
        co.headless()
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1920,1080')
        co.set_argument('--lang=zh-CN')
        try:
            return ChromiumPage(co)
        except Exception as e:
            logger.error(f"启动浏览器失败: {e}")
            return None

    @eventmanager.register(EventType.PluginAction)
    def check(self, event: Event = None):
        if not self._enabled: return
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "weworkippw": return
            self.post_message(channel=event_data.get("channel"), title="开始检测公网IP ...", userid=event_data.get("user"))

        logger.info("开始检测公网IP")
        if self.CheckIP():
            self.ChangeIP()
            self.__update_config()
        logger.info("----------------------本次任务结束----------------------")
        
        if event:
            self.post_message(channel=event.event_data.get("channel"), title="检测公网IP完毕", userid=event.event_data.get("user"))

    def CheckIP(self):
        ip_address = "获取IP失败"
        for url in self._ip_urls:
            ip_address = self.get_ip_from_url(url)
            if ip_address != "获取IP失败" and ip_address:
                logger.info(f"IP获取成功: {url}: {ip_address}")
                break

        if ip_address == "获取IP失败" or not ip_address:
            logger.error("获取IP失败 不操作IP")
            return False

        if self._forced_update:
            logger.info("强制更新IP")
            self._current_ip_address = ip_address
            return True
        elif not self._ip_changed:
            logger.info("上次IP修改没有成功 继续尝试修改IP")
            self._current_ip_address = ip_address
            return True

        if ip_address != self._current_ip_address:
            logger.info("检测到IP变化")
            self._current_ip_address = ip_address
            return True
        return False

    def get_ip_from_url(self, url):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                ip_address = re.search(self._ip_pattern, response.text)
                if ip_address: return ip_address.group()
            return "获取IP失败"
        except Exception:
            return "获取IP失败"

    def find_qrc(self, page):
        try:
            iframe_ele = page.ele('tag:iframe', timeout=5)
            if not iframe_ele: return False
            frame = iframe_ele.get_frame()
            qr_img = frame.ele('.qrcode_login_img', timeout=5)
            if qr_img:
                qr_code_url = qr_img.attr('src')
                if qr_code_url.startswith("/"):
                    qr_code_url = "https://work.weixin.qq.com" + qr_code_url
                qr_code_data = requests.get(qr_code_url).content
                self._qr_code_image = io.BytesIO(qr_code_data)
                return True
            return False
        except Exception:
            return False

    def remote_push_qr(self, page):
        try:
            if self.find_qrc(page):
                if self._pushplus_token and self._helloimg_s_token:
                    img_src, refuse_time = self.upload_image(self._qr_code_image)
                    if img_src:
                        self.send_pushplus_message(refuse_time, f"企业微信登录二维码<br/><img src='{img_src}' />")
                        logger.info("二维码已经发送，等待用户 90 秒内扫码登录")
                        logger.info("如收到短信验证码请以？结束，发送到<企业微信应用> 如： 110301？")
                        time.sleep(90)
                        if self.check_login_status(page):
                            self._update_cookie(page)
                            self.click_app_management_buttons(page)
                else:
                    logger.warning("未配置 PushPlus 或 HelloImg Token，无法推送二维码")
            else:
                logger.warning("远程推送任务 未找到二维码")
        except Exception as e:
            logger.error(f"远程推送任务 推送二维码失败: {e}")

    def ChangeIP(self):
        logger.info("开始请求企业微信管理更改可信IP")
        page = self._get_page()
        if not page: return

        try:
            cookie = self.get_cookie()
            if cookie:
                page.get(self._wechatUrl)
                for c in cookie: page.set.cookies(c)
            
            page.get(self._wechatUrl)
            time.sleep(3)
            
            if self.find_qrc(page):
                logger.info("Cookie失效，准备推送二维码")
                self.remote_push_qr(page)
            else:
                logger.info("尝试Cookie登录")
                if self.check_login_status(page):
                    self.click_app_management_buttons(page)
                else:
                    self._ip_changed = False
        except Exception as e:
            logger.error(f"更改可信IP失败: {e}")
        finally:
            page.quit()

    def _update_cookie(self, page):
        if self._use_cookiecloud and self._cc_server:
            logger.info("使用二维码登录成功，开始刷新cookie")
            try:
                if self._cc_server.check_connection():
                    current_url = page.url
                    current_cookies = page.cookies(as_dict=False, all_info=True)
                    formatted_cookies = {}
                    for cookie in current_cookies:
                        domain = cookie.get('domain', '')
                        if domain not in formatted_cookies: formatted_cookies[domain] = []
                        formatted_cookies[domain].append(cookie)
                    
                    if self._cc_server.update_cookie({'cookie_data': formatted_cookies}):
                        logger.info("更新CookieCloud成功")
                    else:
                        logger.error("更新CookieCloud失败")
            except Exception as e:
                logger.error(f"更新cookie发生错误: {e}")

    def get_cookie(self):
        try:
            cookie_header = ''
            if self._use_cookiecloud:
                cookies, msg = self._cookiecloud.download()
                if not cookies:
                    logger.error(f"CookieCloud获取cookie失败: {msg}")
                    cookie_header = self._cookie_header
                else:
                    for domain, cookie in cookies.items():
                        if domain == ".work.weixin.qq.com":
                            cookie_header = cookie
                            break
                    if not cookie_header: cookie_header = self._cookie_header
            else:
                cookie_header = self._cookie_header

            if not cookie_header: return None
            return self.parse_cookie_header(cookie_header)
        except Exception as e:
            logger.error(f"获取cookie错误: {e}")
            return None

    def parse_cookie_header(self, cookie_header):
        cookies = []
        for cookie in cookie_header.split(';'):
            if '=' in cookie:
                name, value = cookie.strip().split('=', 1)
                cookies.append({'name': name, 'value': value, 'domain': '.work.weixin.qq.com', 'path': '/'})
        return cookies

    def refresh_cookie(self):
        page = self._get_page()
        if not page: return
        try:
            cookie = self.get_cookie()
            if cookie:
                page.get(self._wechatUrl)
                for c in cookie: page.set.cookies(c)
                page.get(self._wechatUrl)
                time.sleep(3)
                if self.check_login_status(page):
                    logger.info("延长cookie任务成功")
                else:
                    logger.info("cookie已失效，下次IP变动推送二维码")
        except Exception as e:
            logger.error(f"cookie校验失败: {e}")
        finally:
            page.quit()

    def check_login_status(self, page):
        time.sleep(3)
        logger.info("检查登录状态...")
        try:
            if page.ele('#check_corp_info', timeout=5):
                logger.info("登录成功！")
                return True
        except Exception:
            pass

        try:
            captcha_panel = page.ele('.receive_captcha_panel', timeout=5)
            if captcha_panel:
                time.sleep(30)  # 等待用户发送验证码
                if self.text and len(self.text) >= 6:
                    logger.info(f"需要短信验证 收到的短信验证码：{self.text[:6]}")
                    # 使用 actions 模拟键盘输入
                    page.actions.type(self.text[:6])
                    time.sleep(1)
                    confirm_btn = page.ele('.confirm_btn', timeout=5)
                    if confirm_btn:
                        confirm_btn.click()
                        time.sleep(3)
                        if page.ele('#check_corp_info', timeout=10):
                            logger.info("验证码登录成功！")
                            return True
                else:
                    logger.error("未收到短信验证码")
                    return False
        except Exception:
            pass
            
        if self.find_qrc(page):
            logger.error("用户没有扫描二维码")
        return False

    def click_app_management_buttons(self, page):
        bash_url = "https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp/"
        xpath = "//div[contains(@class, 'js_show_ipConfig_dialog')]//a[contains(@class, '_mod_card_operationLink') and text()='配置']"
        
        if not self._input_id_list:
            logger.error("未找到应用ID，修改IP失败")
            return

        id_list = self._input_id_list.split(",")
        for app_id in id_list:
            app_url = f"{bash_url}{app_id.strip()}"
            page.get(app_url)
            time.sleep(2)
            try:
                btn = page.ele(xpath, timeout=5)
                if btn:
                    btn.click()
                    input_area = page.ele('textarea.js_ipConfig_textarea', timeout=5)
                    if input_area:
                        input_area.clear()
                        input_area.input(self._current_ip_address)
                        logger.info(f"已输入公网IP：{self._current_ip_address}")
                        page.ele('.js_ipConfig_confirmBtn').click()
                        time.sleep(3)
                        self._ip_changed = True
            except Exception as e:
                logger.error(f"未能打开 {app_url} 或点击按钮异常: {e}")
                self._ip_changed = False

    def send_pushplus_message(self, title, content):
        pushplus_url = f"http://www.pushplus.plus/send/{self._pushplus_token}"
        pushplus_data = {"title": title, "content": content, "template": "html"}
        try:
            requests.post(pushplus_url, json=pushplus_data)
        except Exception as e:
            logger.error(f"PushPlus 发送失败: {e}")

    def upload_image(self, file_obj, permission=1, strategy_id=1, album_id=1):
        helloimg_token = "Bearer " + self._helloimg_s_token
        helloimg_url = "https://www.helloimg.com/api/v1/upload"
        headers = {"Authorization": helloimg_token, "Accept": "application/json"}
        files = {"file": ('qr_code.png', file_obj, 'image/png')}
        expired_at = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        helloimg_data = {"permission": permission, "strategy_id": strategy_id, "album_id": album_id, "expired_at": expired_at}
        refuse_time = (datetime.now() + timedelta(seconds=110)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            response = requests.post(helloimg_url, headers=headers, files=files, data=helloimg_data)
            response_data = response.json()
            if not response_data.get('status'):
                logger.error(f"上传到图床失败: {response_data.get('message')}")
                return None, refuse_time
            img_src = response_data['data']['links']['html']
            return img_src.split('"')[1], refuse_time
        except Exception as e:
            logger.error(f"上传图片时解析响应失败: {e}")
            return None, refuse_time

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled, "onlyonce": self._onlyonce, "cron": self._cron,
            "current_ip_address": self._current_ip_address, "ip_changed": self._ip_changed,
            "forced_update": self._forced_update, "helloimg_s_token": self._helloimg_s_token,
            "pushplus_token": self._pushplus_token, "input_id_list": self._input_id_list,
            "cookie_from_CC": self._cookie_from_CC, "cookie_header": self._cookie_header,
            "use_cookiecloud": self._use_cookiecloud
        })

    @eventmanager.register(EventType.UserMessage)
    def receive_message(self, event: Event):
        if not self._enabled: return
        text = event.event_data.get("text")
        # 匹配 6位数字 + ？或? 结尾
        match = re.match(r'^(\d{6})[？?]$', text)
        if match:
            self.text = match.group(1)
            logger.info(f"从消息中收到验证码：{self.text}")

    def get_state(self) -> bool: return self._enabled

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {'component': 'VForm', 'content': [
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '立即检测一次'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'forced_update', 'label': '强制更新'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'use_cookiecloud', 'label': '使用CookieCloud'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'cron', 'label': '检测周期', 'placeholder': '0 * * * *'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextarea', 'props': {'model': 'cookie_header', 'label': 'COOKIE', 'rows': 1, 'placeholder': '手动填写cookie'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12}, 'content': [{'component': 'VTextarea', 'props': {'model': 'input_id_list', 'label': '应用ID', 'rows': 1, 'placeholder': '输入应用ID，多个ID用英文逗号分隔。在企业微信应用页面URL末尾获取'}}]}
                ]},
                {'component': 'VRow', 'content': [
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'pushplus_token', 'label': 'PushPlus Token', 'placeholder': '用于推送二维码'}}]},
                    {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'helloimg_s_token', 'label': 'HelloImg Token', 'placeholder': '用于上传二维码到图床'}}]}
                ]}
            ]}
        ], {
            "enabled": False, "onlyonce": False, "forced_update": False, "use_cookiecloud": True,
            "cron": "0 * * * *", "cookie_header": "", "input_id_list": "", 
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
