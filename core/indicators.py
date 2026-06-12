import pandas as pd
import numpy as np
from core import config


def calculate_rsi(df: pd.DataFrame, period: int = None) -> pd.Series:
    """RSI (Relative Strength Index) 계산"""
    period = period or config.RSI_PERIOD
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calculate_macd(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (Moving Average Convergence Divergence) 계산
    Returns: (macd_line, signal_line, histogram)
    """
    ema_fast = df["close"].ewm(span=config.MACD_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=config.MACD_SLOW, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=config.MACD_SIGNAL, adjust=False).mean()
    histogram = macd - signal
    return macd, signal, histogram


def calculate_bollinger_bands(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """볼린저 밴드 계산
    Returns: (upper_band, middle_band, lower_band)
    """
    period = config.BOLLINGER_PERIOD
    std_mult = config.BOLLINGER_STD
    middle = df["close"].rolling(window=period).mean()
    std = df["close"].rolling(window=period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return upper, middle, lower


def calculate_ema(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """EMA (지수이동평균) 계산
    Returns: (ema_short, ema_mid, ema_long)
    """
    ema_short = df["close"].ewm(span=config.EMA_SHORT, adjust=False).mean()
    ema_mid = df["close"].ewm(span=config.EMA_MID, adjust=False).mean()
    ema_long = df["close"].ewm(span=config.EMA_LONG, adjust=False).mean()
    return ema_short, ema_mid, ema_long


def calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """현재 거래량 / 평균 거래량 비율"""
    avg_volume = df["volume"].rolling(window=period).mean()
    return (df["volume"] / avg_volume.replace(0, np.nan)).fillna(1.0)


def calculate_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> tuple[pd.Series, pd.Series]:
    """스토캐스틱 오실레이터 계산
    Returns: (%K, %D)
    """
    lowest_low = df["low"].rolling(window=k_period).min()
    highest_high = df["high"].rolling(window=k_period).max()
    stoch_k = 100 * (df["close"] - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    stoch_d = stoch_k.rolling(window=d_period).mean()
    return stoch_k.fillna(50), stoch_d.fillna(50)


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """모든 지표를 데이터프레임에 추가"""
    if df.empty or len(df) < (config.MACD_SLOW + config.MACD_SIGNAL):
        return df

    df = df.copy()

    # RSI
    df["rsi"] = calculate_rsi(df)

    # MACD
    df["macd"], df["macd_signal"], df["macd_hist"] = calculate_macd(df)

    # 볼린저 밴드
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = calculate_bollinger_bands(df)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    df["bb_pct"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    df["bb_pct"] = df["bb_pct"].fillna(0.5)  # NaN → 중간값(0.5)으로 대체 (JSON 직렬화 오류 방지)

    # EMA
    df["ema_short"], df["ema_mid"], df["ema_long"] = calculate_ema(df)

    # 거래량 비율
    df["volume_ratio"] = calculate_volume_ratio(df)

    # 스토캐스틱
    df["stoch_k"], df["stoch_d"] = calculate_stochastic(df)

    # 가격 변화율
    df["price_change_pct"] = df["close"].pct_change() * 100

    return df


def get_latest_indicators(df: pd.DataFrame) -> dict:
    """최신 지표 값 반환"""
    if df.empty:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    return {
        "close": last["close"],
        "volume": last["volume"],
        "rsi": last.get("rsi", 50),
        "macd": last.get("macd", 0),
        "macd_signal": last.get("macd_signal", 0),
        "macd_hist": last.get("macd_hist", 0),
        "prev_macd_hist": prev.get("macd_hist", 0),
        "bb_upper": last.get("bb_upper", 0),
        "bb_mid": last.get("bb_mid", 0),
        "bb_lower": last.get("bb_lower", 0),
        "bb_pct": last.get("bb_pct", 0.5),
        "bb_width": last.get("bb_width", 0),
        "ema_short": last.get("ema_short", 0),
        "ema_mid": last.get("ema_mid", 0),
        "ema_long": last.get("ema_long", 0),
        "volume_ratio": last.get("volume_ratio", 1.0),
        "stoch_k": last.get("stoch_k", 50),
        "stoch_d": last.get("stoch_d", 50),
        "price_change_pct": last.get("price_change_pct", 0),
    }
