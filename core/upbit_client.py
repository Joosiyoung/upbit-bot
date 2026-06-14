import logging
import pyupbit
import pandas as pd
import time
import hashlib
import uuid
import jwt
import requests
from urllib.parse import urlencode
from core import config

UPBIT_SERVER = "https://api.upbit.com"


class UpbitClient:
    def __init__(self):
        if config.ACCESS_KEY and config.SECRET_KEY:
            self.upbit = pyupbit.Upbit(config.ACCESS_KEY, config.SECRET_KEY)
        else:
            self.upbit = None

    def get_ohlcv(self, ticker: str, interval: str, count: int = 200) -> pd.DataFrame:
        """캔들 데이터 조회"""
        try:
            df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        except Exception as e:
            logging.warning("OHLCV 조회 실패 [%s %s]: %s", ticker, interval, e)
            return pd.DataFrame()

    def get_current_price(self, ticker: str, warn: bool = True) -> float:
        """현재가 조회

        warn=False: 시장 스캔(거래대금 톱20)처럼 실패가 양성(skip만 함)인 경로용.
        상장폐지·신규 상장 직후 등으로 일부 티커가 'Code not found'를 반환하는데,
        매 사이클(30초) WARNING으로 찍히면 로그가 도배돼 진짜 경고가 묻힌다.
        보유 코인 가격 조회 등 실패가 유의미한 경로는 warn=True(기본)를 유지한다.
        """
        try:
            price = pyupbit.get_current_price(ticker)
            return price if price else 0.0
        except Exception as e:
            if warn:
                logging.warning("현재가 조회 실패 [%s]: %s", ticker, e)
            else:
                logging.debug("현재가 조회 실패(스캔, skip) [%s]: %s", ticker, e)
            return 0.0

    def get_balance_krw(self) -> float:
        """원화 잔고 조회"""
        if not self.upbit:
            return 0.0
        try:
            balance = self.upbit.get_balance("KRW")
            return balance if balance else 0.0
        except Exception as e:
            logging.warning("KRW 잔고 조회 실패: %s", e)
            return 0.0

    def get_coin_balance(self, ticker: str) -> float | None:
        """특정 코인의 실제 잔고 수량 조회. 조회 실패 시 None (0과 구분)"""
        if not self.upbit:
            return None
        try:
            bal = self.upbit.get_balance(ticker)
            return float(bal) if bal is not None else 0.0
        except Exception as e:
            logging.warning("코인 잔고 조회 실패 [%s]: %s", ticker, e)
            return None

    def _auth_headers(self, query: dict | None = None) -> dict:
        """업비트 JWT 인증 헤더 생성"""
        payload = {
            "access_key": config.ACCESS_KEY,
            "nonce": str(uuid.uuid4()),
        }
        if query:
            query_string = urlencode(query).encode()
            m = hashlib.sha512()
            m.update(query_string)
            payload["query_hash"] = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        jwt_token = jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")
        return {"Authorization": f"Bearer {jwt_token}"}

    @staticmethod
    def _throttle_from_headers(resp) -> None:
        """업비트 Remaining-Req 헤더로 잔여 요청 한도가 임박하면 짧게 대기.
        형식 예: 'group=order; min=1799; sec=4'. sec(초당 잔여)이 1 이하면 백오프."""
        rr = resp.headers.get("Remaining-Req") or resp.headers.get("remaining-req")
        if not rr:
            return
        try:
            parts = {}
            for seg in rr.split(";"):
                seg = seg.strip()
                if "=" in seg:
                    k, v = seg.split("=", 1)
                    parts[k.strip()] = v.strip()
            sec = int(parts.get("sec", "99"))
            if sec <= 1:
                time.sleep(0.3)
        except Exception:
            pass

    def _order_direct(self, query: dict) -> dict:
        """업비트 REST API 직접 호출 - 주문 공통 (에러 상세 반환)"""
        if not config.ACCESS_KEY or not config.SECRET_KEY:
            return {"error": {"name": "no_api_key", "message": "API 키 없음"}}
        try:
            res = requests.post(
                UPBIT_SERVER + "/v1/orders",
                params=query,
                headers=self._auth_headers(query),
                timeout=10,
            )
            self._throttle_from_headers(res)
            result = res.json()
            result["_http_status"] = res.status_code
            # HTTP 4xx/5xx 이면서 error/uuid 키가 없는 경우 명시적 오류 구조 추가
            if res.status_code >= 400 and "error" not in result and "uuid" not in result:
                result["error"] = {
                    "name": "http_error",
                    "message": f"HTTP {res.status_code}",
                }
            return result
        except Exception as e:
            return {"error": {"name": "exception", "message": str(e)}}

    def buy_market_order_direct(self, ticker: str, amount: float) -> dict:
        """시장가 매수 (KRW 금액 지정, 에러 상세 반환)"""
        return self._order_direct({
            "market": ticker,
            "side": "bid",
            "price": str(int(amount)),
            "ord_type": "price",
        })

    def sell_market_order_direct(self, ticker: str, volume: float) -> dict:
        """시장가 매도 (수량 지정, 에러 상세 반환)"""
        return self._order_direct({
            "market": ticker,
            "side": "ask",
            "volume": f"{volume:.8f}",
            "ord_type": "market",
        })

    def get_order_result(self, order_uuid: str, max_wait: float = 4.0) -> dict | None:
        """주문 체결 결과 조회 (uuid 기준 폴링).
        체결 완료(done/cancel) 시 {"executed_volume": float, "avg_price": float|None} 반환.
        타임아웃·조회 실패 시 None.
        시장가 주문은 체결 후 상태가 done(매도) 또는 cancel(매수 잔여금 취소)로 끝난다.
        """
        if not config.ACCESS_KEY or not config.SECRET_KEY:
            return None
        query = {"uuid": order_uuid}
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                res = requests.get(
                    UPBIT_SERVER + "/v1/order",
                    params=query,
                    headers=self._auth_headers(query),
                    timeout=5,
                )
                self._throttle_from_headers(res)
                if res.status_code != 200:
                    time.sleep(0.5)
                    continue
                order = res.json()
                state = order.get("state")
                executed = float(order.get("executed_volume") or 0)
                if state in ("done", "cancel") and executed > 0:
                    trades = order.get("trades") or []
                    total_vol, total_funds = 0.0, 0.0
                    for t in trades:
                        p, v = float(t.get("price", 0)), float(t.get("volume", 0))
                        if p <= 0 or v <= 0:
                            continue
                        total_vol   += v
                        total_funds += p * v
                    avg_price = (total_funds / total_vol) if total_vol > 0 else None
                    return {"executed_volume": executed, "avg_price": avg_price}
                if state in ("done", "cancel"):
                    return {"executed_volume": executed, "avg_price": None}
            except Exception as e:
                logging.warning("주문 체결 조회 실패 [%s]: %s", order_uuid, e)
            time.sleep(0.5)
        return None
