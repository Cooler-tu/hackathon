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


def now_ts():
    return int(time.time() * 1000)


class RoostooClient:
    def __init__(self):
        if not API_KEY or not API_SECRET:
            raise ValueError("⚠️ 请先在 .env 文件中设置 ROOSTOO_API_KEY 和 ROOSTOO_API_SECRET")
        self.api_key = API_KEY
        self.api_secret = API_SECRET
        self.session = requests.Session()
        self.session.headers.update({"RST-API-KEY": self.api_key})
        self.base_url = BASE_URL.rstrip("/")
        

    # ✅ 正确签名函数
    def sign(self, params: dict):
        query_string = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


    '''
    # ✅ 核心请求函数
    def _sign_and_request(self, method, endpoint, params=None, data=None):
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
            if method == "GET":
                response = self.session.get(url, params=all_params, headers=headers)
            else:
                response = self.session.post(url, data=all_params, headers=headers)

            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"API 请求失败: {endpoint} | {response.text if 'response' in locals() else str(e)}")
            raise

    
    def generate_signature(params):
        query_string = '&'.join(["{}={}".format(k, params[k])
                                for k in sorted(params.keys())])
        us = API_SECRET.encode('utf-8')
        m = hmac.new(us, query_string.encode('utf-8'), hashlib.sha256)
        return m.hexdigest()
    '''
        
    # 🧩 各种接口封装
    def get_server_time(self):
        return self._sign_and_request("GET", "/v3/serverTime")

    def get_exchange_info(self):
        return self._sign_and_request("GET", "/v3/exchangeInfo")

    def get_balance(self):
        return self._sign_and_request("GET", "/v3/balance")
    '''
    def place_order(self, pair, side, quantity, price=None):
        payload = {
            "timestamp": int(time.time()) * 1000,
            "pair": pair,
            "side": side.upper(),
            "quantity": float(quantity),
            "type": "MARKET" if price is None else "LIMIT",
        }
        if price is not None:
            payload["price"] = float(price)
        return self._sign_and_request("POST", "/v3/place_order", data=payload)

    
    def place_order(coin, side, qty, price=None):
        payload = {
            "timestamp": int(time.time()) * 1000,
            "pair": coin + "/USD",
            "side": side,
            "quantity": qty,
        }

        if not price:
            payload['type'] = "MARKET"
        else:
            payload['type'] = "LIMIT"
            payload['price'] = price

        r = requests.post(
            BASE_URL + "/v3/place_order",
            data=payload,
            headers={"RST-API-KEY": API_KEY,
                    "MSG-SIGNATURE": generate_signature(payload)}
        )
        print (r.status_code, r.text)
    '''
        
    def cancel_order(self, pair, order_id=None):
        payload = {"pair": pair}
        if order_id:
            payload["order_id"] = order_id
        return self._sign_and_request("POST", "/v3/cancel_order", data=payload)

    def query_order(self, pair=None, order_id=None, pending_only=None):
        payload = {}
        if pair:
            payload["pair"] = pair
        if order_id:
            payload["order_id"] = order_id
        if pending_only is not None:
            payload["pending_only"] = pending_only
        return self._sign_and_request("POST", "/v3/query_order", data=payload)

    def pending_count(self):
        return self._sign_and_request("GET", "/v3/pending_count")
    



    def _sign_and_request(self, method, endpoint, data=None):
        data = data or {}

        # 官方：timestamp = int(time.time()) * 1000
        data["timestamp"] = int(time.time()) * 1000

        signature = self.sign(data)

        headers = {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": signature,
        }

        url = self.base_url + endpoint  # use self.base_url for consistency

        try:
            if method == "GET":
                response = self.session.get(url, params=data, headers=headers)
            else:
                response = self.session.post(url, data=data, headers=headers)

            # 抛出 HTTPError（4xx/5xx）
            response.raise_for_status()

            # 尝试解析 JSON 并返回 dict（或 list）
            try:
                return response.json()
            except ValueError:
                # 返回非 JSON（极少见），把文本包装成 dict 供上层检查
                logger.error(f"非 JSON 响应: {response.text}")
                return {"raw_text": response.text, "status_code": response.status_code}

        except requests.HTTPError as http_err:
            logger.error(f"HTTP error for {endpoint}: {http_err} | {response.text if 'response' in locals() else ''}")
            raise
        except Exception as e:
            logger.error(f"API 请求失败: {endpoint} | {str(e)}")
            raise


    # === ✔ 完全与官方 DEMO 行为相同的 place_order ===
    def place_order(self, pair, side, quantity, price=None):
        payload = {
            "pair": pair,             # 与官方一致：pair= "BTC/USD"
            "side": side,             # 不转大写，保持用户输入
            "quantity": quantity,     # 不转 float，保持原样
        }

        # 官方 DEMO：MARKET / LIMIT 判断方式
        if price is None:
            payload["type"] = "MARKET"
        else:
            payload["type"] = "LIMIT"
            payload["price"] = price  # 不转 float

        logger.info(f"[Roostoo] place_order payload = {payload}")

        resp = self._sign_and_request("POST", "/v3/place_order", data=payload)

        logger.info(f"[Roostoo] place_order response = {resp}")

        # 调用与官方一致的签名+POST
        return self._sign_and_request("POST", "/v3/place_order", data=payload)