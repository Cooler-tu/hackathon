# roostoo_client.py
import requests
import hashlib
import hmac
import time
from loguru import logger
from dotenv import load_dotenv
import os

load_dotenv()

BASE_URL = "https://mock-api.roostoo.com"
API_KEY = os.getenv("ROOSTOO_API_KEY")
API_SECRET = os.getenv("ROOSTOO_API_SECRET")


def now_ts() -> int:
    """返回 13 位毫秒时间戳"""
    return int(time.time() * 1000)


class RoostooClient:
    def __init__(self):
        if not API_KEY or not API_SECRET:
            raise ValueError("⚠️ 请先在 .env 文件中设置 ROOSTOO_API_KEY 和 ROOSTOO_API_SECRET")
        self.api_key = API_KEY
        self.api_secret = API_SECRET
        self.session = requests.Session()
        self.session.headers.update({"RST-API-KEY": self.api_key})

    # ----------------- 内部工具 -----------------
    def sign(self, params: dict = None) -> str:
        """HMAC SHA256 签名"""
        params = params or {}
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _sign_and_request(self, method: str, endpoint: str, params=None, data=None):
        """核心请求函数"""
        params = params or {}
        data = data or {}
        all_params = {**params, **data, "timestamp": now_ts()}
        signature = self.sign(all_params)

        headers = {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": signature,
        }

        url = BASE_URL + endpoint
        try:
            if method.upper() == "GET":
                response = self.session.get(url, params=all_params, headers=headers)
            else:
                response = self.session.post(url, data=all_params, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"API 请求失败: {endpoint} | {response.text if 'response' in locals() else str(e)}")
            raise

    # ----------------- 公共接口 -----------------
    def get_server_time(self):
        return self._sign_and_request("GET", "/v3/serverTime")

    def get_exchange_info(self):
        return self._sign_and_request("GET", "/v3/exchangeInfo")

    def get_balance(self) -> dict:
        """获取账户余额"""
        try:
            resp = self._sign_and_request("GET", "/v3/balance")
            if resp and resp.get("Success"):
                wallet = resp.get("Wallet", {})
                balances = {coin: info.get("Free", 0) for coin, info in wallet.items()}
                return balances
            else:
                logger.error(f"获取余额失败: {resp.get('ErrMsg') if resp else '无响应'}")
                return {}
        except Exception as e:
            logger.error(f"获取余额异常: {e}")
            return {}

    def pending_count(self) -> dict:
        return self._sign_and_request("GET", "/v3/pending_count")

    # ----------------- 交易接口 -----------------
    def place_order(self, pair: str, side: str, quantity: float, price: float = None) -> dict:
        """下单（MARKET 或 LIMIT）"""
        payload = {
            "pair": pair,
            "side": side.upper(),
            "quantity": float(quantity),
            "type": "MARKET" if price is None else "LIMIT"
        }
        if price is not None:
            payload["price"] = float(price)
        return self._sign_and_request("POST", "/v3/place_order", data=payload)

    def cancel_order(self, pair: str, order_id: int = None) -> dict:
        """撤单（可指定订单号或全部）"""
        payload = {"pair": pair}
        if order_id:
            payload["order_id"] = order_id
        return self._sign_and_request("POST", "/v3/cancel_order", data=payload)

    def query_order(self, pair: str = None, order_id: int = None, pending_only: bool = None) -> dict:
        """查询订单"""
        payload = {}
        if pair:
            payload["pair"] = pair
        if order_id:
            payload["order_id"] = order_id
        if pending_only is not None:
            payload["pending_only"] = "TRUE" if pending_only else "FALSE"
        return self._sign_and_request("POST", "/v3/query_order", data=payload)
