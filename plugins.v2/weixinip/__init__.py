import base64
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

# 引入 Selenium 和 undetected_chromedriver (CloakBrowser)
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

class WeWorkIPPW(_PluginBase):
    # 插件名称
    plugin_name = "微信ip"
    # 插件描述
    plugin_desc = "!!docker用户用这个版本!!定时获取最新动态公网IP，配置到企业微信应用的可信IP列表里。(已适配新版CloakBrowser内核)"
    # 插件图标
    plugin_icon = "https://github.com/jinbo-152/MoviePilot-Plugins/blob/main/icons/micon.png?raw=true"
    # 插件版本
    plugin_version = "0.1"  # 版本号微调，标记为新版内核适配版
    # 插件作者
    plugin_author = "suraxiuxiu,jinbo"
    # 作者主页
    author_url = "https://github.com/jinbo-152/MoviePilot-Plugins"
    # 插件配置项ID前缀
    plugin_config_prefix = "weixinip"
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
        
    # 匹配ip地址的正则
    _ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    # 获取ip地址的网址列表
    _ip_urls = ["https://myip.ipip.net", "https://ddns.oray.com/checkip", "https://ip.3322.net", "https://4.ipw.cn"]
    # 当前ip地址
    _current_ip_address = '192.168.1.1'
    # 企业微信应用管理地址
    _wechatUrl = f'https://work.weixin.qq.com/wework_admin/frame#/apps/modApiApp/00000000000'
    _urls = []
    # 登录cookie
    _cookie_header = ""
    # 从CookieCloud或内置登录获取的cookie
    _cookie_from_CC = ""
    # 发送二维码给指定成员,为空则发送给全部成员
    _qr_send_users = ""
    # 覆盖已填写的IP,设置FALSE则添加新IP到已有IP列表里
    _overwrite = True

    # 使用CookieCloud开关
    _use_cookiecloud = True
    # cookie有效检测
    _cookie_valid = False
    # IP更改成功状态
    _ip_changed = False
    # 刷新cookie间隔时间
    _refresh_cron = "*/5 * * * *"
    # 状态通知时间 
    _status_cron = "0 * * * *"
    # 检测IP时间
    _check_cron = "*/11 * * * *"
    _enabled = False
    _onlyonce = False
    _cookiecloud = CookieCloudHelper()
    _code = 0
    _pattern = r"^#\d{6}$"
    # cookie失效后定时唤起登录
    _schedule_login = False
    _driver = None
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 清空配置
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
        
        # 停止现有任务
        self.stop_service()

        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)       
            # 运行一次定时服务
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
            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()
                
        self.__update_config()

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
        else:
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
            
        options = uc.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        
        driver = None
        try:    
            driver = uc.Chrome(options=options)
            cookie = self.get_cookie()
            if not cookie:
                logger.error('cookie为空,请检查CC配置和插件手动填写项')
                self._cookie_valid = False
                return
                
            # Selenium 必须先访问域名才能注入 cookie
            driver.get(self._urls[0])
            for c in cookie:
                driver.add_cookie({
                    'name': c['name'],
                    'value': c['value'],
                    'domain': c.get('domain', '.work.weixin.qq.com'),
                    'path': c.get('path', '/')
                })
            
            # 刷新页面使 cookie 生效
            driver.get(self._urls[0])
            time.sleep(2)
            
            # 检查是否处于登录页面 (cookie失效)
            try:
                login_ele = driver.find_element(By.CSS_SELECTOR, '.login_stage_title_text')
                if login_ele.is_displayed():
                    logger.info("cookie失效,请重新获取")
                    self._cookie_valid = False
                    return
            except NoSuchElementException:
                logger.info("加载企微管理界面成功")
                self._cookie_valid = True

            for index, url in enumerate(self._urls):           
                logger.info(f"正在更改第{index+1}个应用的可信IP")
                driver.get(url)
                
                # 等待并点击配置IP按钮
                btn = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.app_card_operate.js_show_ipConfig_dialog'))
                )
                btn.click()
                
                # 等待输入框出现
                input_area = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'textarea.js_ipConfig_textarea'))
                )
                
                existing_ip = input_area.get_attribute('value') or ""
                
                # 清空并输入新IP
                input_area.clear()
                if self._overwrite:
                    input_area.send_keys(self._current_ip_address)
                else:
                    input_area.send_keys(f'{existing_ip};{self._current_ip_address}')
                
                # 点击确认
                confirm_btn = driver.find_element(By.CSS_SELECTOR, '.js_ipConfig_confirmBtn')
                confirm_btn.click()
                time.sleep(1)
                logger.info(f"更改第{index+1}个应用的可信IP成功")
                
            self._ip_changed = True
            
        except Exception as e:
            logger.error(f"更改可信IP失败:{e}")
        finally:
            if driver:
                driver.quit()
    
    def refresh_cookie(self, _login=True):
        logger.info("开始刷新企业微信缓存")
        if not self.check_connect():
            logger.error("网络连接失败,跳过本次缓存保活")
            return
            
        options = uc.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        driver = None
        try:    
            driver = uc.Chrome(options=options)
            cookie = self.get_cookie()
            if not cookie:
                logger.error('cookie为空,请检查CC配置和插件手动填写项')
                self._cookie_valid = False
                self._handle_cookie_invalid(_login)
                return
                
            driver.get(self._urls[0])
            for c in cookie:
                driver.add_cookie({
                    'name': c['name'], 'value': c['value'],
                    'domain': c.get('domain', '.work.weixin.qq.com'), 'path': c.get('path', '/')
                })
                
            driver.get(self._urls[0])
            time.sleep(2)
            
            try:
                login_ele = driver.find_element(By.CSS_SELECTOR, '.login_stage_title_text')
                if login_ele.is_displayed():
                    logger.info("cookie失效,请重新获取")
                    self._cookie_valid = False
                    self._handle_cookie_invalid(_login)
            except NoSuchElementException:
                logger.info("cookie有效校验成功")
                self._cookie_valid = True
                
            self.__update_config()
        except Exception as e:
            logger.error(f"cookie校验失败:{e}") 
            if "Timeout" in str(e):
                logger.info("检测可能连接超时,跳过本次刷新") 
            else:
                self._cookie_valid = False
            self.__update_config()   
        finally:
            if driver:
                driver.quit()

    def _handle_cookie_invalid(self, _login):
        """处理 cookie 失效后的定时器逻辑"""
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
            logger.error(f"当前cookie:{cookie_header}") 
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
        logger.info("进行一次缓存检测")
        self.refresh_cookie(_login=False)
        if self._cookie_valid:
            logger.info("已使用其他有效缓存,跳过登录")
            if not self._scheduler.get_job("refresh_cookie"):
                self.create_refresh_job()
            if self._scheduler.get_job("wwlogin"):
                self._scheduler.remove_job("wwlogin")
            return

        options = uc.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        driver = None
        try:
            driver = uc.Chrome(options=options)
            self._driver = driver
            driver.get(self._urls[0])
            
            # 等待 iframe 并切换
            iframe = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'iframe[src*="login_qrcode"]'))
            )
            driver.switch_to.frame(iframe)
            
            # 获取二维码图片 URL
            qr_img = WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, '.qrcode_login_img'))
            )
            qr_img_relative_url = qr_img.get_attribute('src')
            base_url = driver.current_url
            absolute_url = urljoin(base_url, qr_img_relative_url)
            
            # 切回主文档
            driver.switch_to.default_content()
            
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="点击扫描二维码登录企业微信", image=absolute_url, link=absolute_url, userid=self._qr_send_users)
            
            response = requests.get(absolute_url)
            if response.status_code == 200:
                with open(self.qr_path, "wb") as file:
                    file.write(response.content)
                logger.info("打开插件详情扫描二维码登录企业微信")
            else:
                logger.info(f"无法下载二维码图片：{response.status_code}")
                
            # 轮询等待扫码后 URL 变化
            wait_time = 0
            new_url = False
            while wait_time < 60:
                time.sleep(1)
                wait_time += 1
                current_url = driver.current_url
                if 'work.weixin.qq.com' in current_url and 'login' not in current_url and 'qrcode' not in current_url:
                    new_url = True
                    break
                    
            if not new_url:
                raise ValueError("等待扫描超时")
                
            # 处理手机验证码验证
            if 'mobile_confirm' in driver.current_url:
                self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="检测到登录验证，请以 #123456 的格式回复验证码，两分钟后超时", userid=self._qr_send_users)
                logger.info("检测到登录验证，进入验证流程")
                
                wait_code_time = 0
                while 'mobile_confirm' in driver.current_url:
                    self._code = 0
                    wait_time_inner = 0
                    while self._code == 0:
                        time.sleep(2)
                        wait_code_time += 2
                        if wait_code_time > 120:
                            raise ValueError("验证超时,终止本次登录")
                            
                    input_element = driver.find_element(By.CSS_SELECTOR, '.inner_input')
                    input_element.clear()
                    input_element.send_keys(self._code)
                    
                    # 等待页面跳转
                    for _ in range(5):
                        time.sleep(1)
                        wait_time_inner += 1
                        if 'mobile_confirm' not in driver.current_url:
                            break
                            
                    if 'mobile_confirm' in driver.current_url:
                        self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="登录失败,请检查验证码并重新发送", userid=self._qr_send_users)
                        logger.info("登录失败,请检查验证码并重新发送")
            
            # 提取 Cookie
            cookies = driver.get_cookies()
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
            if driver:
                driver.quit()
                self._driver = None
            self.__update_config()
            if os.path.exists(self.qr_path):
                os.remove(self.qr_path)
    
    def create_refresh_job(self):
        logger.info("创建定时刷新企业微信缓存任务")
        try:
            self._scheduler.add_job(
                func=self.refresh_cookie,
                trigger=CronTrigger.from_crontab(self._refresh_cron),
                name="延续企业微信cookie有效时间",
                id="refresh_cookie"
            )
        except Exception as err:
            logger.error(f"定时刷新企业微信缓存任务配置错误：{err}")
            self.systemmessage.put(f"定时刷新企业微信缓存任务配置错误：{err}")
        
    def create_login_job(self):
        logger.info("唤起企业微信登录任务")
        try:
            self._scheduler.add_job(
                func=self.login,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5),
                name="唤起企业微信登录"
            )
        except Exception as err:
            logger.error(f"定时唤起企业登录任务配置错误：{err}")
            self.systemmessage.put(f"定时唤起企业登录配置错误：{err}")

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
            if response.status_code == 200:
                return True
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"连接失败: {e}")
            return False
                
    def __update_config(self):
        self.update_config(
            {
                "enabled": self._enabled,
                "onlyonce": self._onlyonce,
                "cron": self._check_cron,
                "wechatUrl": self._wechatUrl,
                "cookie_header": self._cookie_header,
                "qr_send_users": self._qr_send_users,
                "cookie_from_CC": self._cookie_from_CC,
                "overwrite": self._overwrite,
                "current_ip_address": self._current_ip_address,
                "use_cookiecloud": self._use_cookiecloud,
                "cookie_valid": self._cookie_valid,
                "ip_changed": self._ip_changed,
                "schedule_login": self._schedule_login,
                "status_cron": self._status_cron
            }
        )

    @eventmanager.register(EventType.UserMessage)
    def receive_message(self, event: Event):
        if not self._enabled:
            return
        text = event.event_data.get("text")
        if re.match(self._pattern, text):
            self._code = text[1:]
            logger.info(f"从MP应用收到验证码：{self._code}")
            return
        if text == "#登录企业微信":
            if self._cookie_valid:
                self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="缓存有效，无需登录", userid=self._qr_send_users)
                return
            self._scheduler.add_job(
                func=self.login,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="登录企业微信",
            )
    
    def send_cookie_status(self):
        if not self._cookie_valid:
            self.post_message(channel=MessageChannel.Wechat, mtype=NotificationType.Plugin, title="企业微信Cookie失效", text="回复下述指令唤起一次登录\n#登录企业微信", userid=self._qr_send_users)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [{
            "cmd": "/weworkippw",
            "event": EventType.PluginAction,
            "desc": "微信应用检测动态IP",
            "category": "",
            "data": {
                "action": "weworkippw"
            }
        }]

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._check_cron:
            return [{
                "id": "WeWorkIPPW",
                "name": "微信应用自动配置动态公网IP",
                "trigger": CronTrigger.from_crontab(self._check_cron),
                "func": self.check,
                "kwargs": {}
            }]
        return []
            
    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即检测一次IP"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "overwrite", "label": "覆盖模式"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "use_cookiecloud", "label": "使用CookieCloud获取cookie"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "schedule_login", "label": "自动登录"}}]}
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "cron", "label": "检测IP周期", "placeholder": "*/11 * * * *"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "status_cron", "label": "Cookie失效通知周期 仅在关闭自动登录时生效", "placeholder": "0 * * * *"}}]}
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "cookie_header", "label": "非必填项:COOKIE", "rows": 1, "placeholder": "非必须填写项。手动提取HeaderString格式的Cookie，仅在未使用CC和内置登录的情况下使用。"}}]}
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "wechatUrl", "label": "企微应用管理地址", "rows": 2, "placeholder": "多个地址用英文逗号分隔"}}]}
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "qr_send_users", "label": "接收通知的用户ID", "placeholder": "留空则发送给全部成员"}}]}
                        ],
                    }
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "overwrite": True,
            "use_cookiecloud": True,
            "schedule_login": False,
            "cron": "*/11 * * * *",
            "status_cron": "0 * * * *",
            "cookie_header": "",
            "wechatUrl": "",
            "qr_send_users": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
