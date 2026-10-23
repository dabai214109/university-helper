import requests
from loguru import logger

from ..answer_base import Tiku

# 响应里出现这些字样时，换 token 才有意义（额度耗尽或配置失效）。
_TOKEN_UNUSABLE_MARKERS = ("次数不足", "已经无效", "已被重置", "配置过期")


class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = "言溪题库"
        self.api = "https://tk.enncy.cn/query"
        self._token = None
        self._token_index = 0  # token队列计数器
        self._times = 100  # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self, q_info: dict):
        try:
            res = requests.get(
                self.api,
                params={
                    "question": q_info["title"],
                    "token": self._token,
                    # 'type':q_info['type'], #修复478题目类型与答案类型不符（不想写后处理了）
                    # 没用，就算有type和options，言溪题库还是可能返回类型不符，问了客服，type仅用于收集
                },
                timeout=30,
            )
        except requests.exceptions.Timeout as exc:
            logger.error(f"{self.name}查询超时: {exc.__class__.__name__}")
            return None
        except requests.exceptions.ConnectionError as exc:
            logger.error(f"{self.name}网络连接失败: {exc.__class__.__name__}")
            return None
        except requests.exceptions.RequestException as exc:
            logger.error(f"{self.name}请求失败: {exc.__class__.__name__}")
            return None

        if res.status_code != 200:
            logger.error(f"{self.name}查询失败: HTTP 状态码 {res.status_code}")
            return None

        try:
            res_json = res.json()
        except ValueError as exc:
            logger.error(f"{self.name}查询失败: 响应不是有效JSON ({exc.__class__.__name__})")
            return None

        if not isinstance(res_json, dict) or "code" not in res_json or not isinstance(res_json.get("data"), dict):
            logger.error(f"{self.name}查询失败: 响应结构无效")
            return None

        response_data = res_json["data"]
        response_answer = response_data.get("answer")
        if not isinstance(response_answer, str):
            logger.error(f"{self.name}查询失败: 响应结构无效")
            return None

        if not res_json["code"]:
            # 换 token 才能恢复的情况有两种：额度耗尽，以及题库配置本身失效
            # （被重置/过期）。后者以前不算在这里，于是配置一旦失效，每道题都只
            # 记一句「未命中」，日志里完全看不出真实原因，也不会去试下一个 token。
            reason = next((m for m in _TOKEN_UNUSABLE_MARKERS if m in response_answer), "")
            if self._times == 0 or reason:
                logger.info(f"{self.name} 当前 Token 不可用（{reason or '额度耗尽'}），尝试更换下一个")
                self._token_index += 1
                try:
                    self.load_token()
                except PermissionError:
                    logger.error(
                        f"{self.name} 所有 Token 均不可用：请到 https://tk.enncy.cn/ 个人中心"
                        " 重新复制题库配置，粘贴后需新建任务才会生效"
                    )
                    return None
                # 重新查询
                return self._query(q_info)
            logger.error(f"{self.name}查询失败: provider 返回未命中")
            return None
        self._times = response_data.get("times", self._times)
        return response_answer.strip()

    def load_token(self):
        token_list = self._conf["tokens"].split(",")
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error("TOKEN用完, 请自行更换再重启脚本")
            raise PermissionError(f"{self.name} TOKEN 已用完, 请更换")
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()
