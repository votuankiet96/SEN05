"""Đọc config.yaml, thay biến môi trường, trả về 1 Config object dùng chung toàn hệ thống."""

import os
import re
from dataclasses import dataclass
from typing import List

import yaml

_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


@dataclass
class CTraderConfig:
    environment: str
    host: str
    port: int
    heartbeat_seconds: int
    client_id: str
    client_secret: str
    ctid_trader_account_id: int
    token_file: str
    oauth_token_url: str


@dataclass
class RiskConfig:
    risk_percent_per_trade: float
    risk_basis: str
    fallback_ksl: float
    fallback_ktp: float
    max_concurrent_trades: int


@dataclass
class RedisConfig:
    host: str
    port: int
    password: str
    db: int
    key_prefix: str


@dataclass
class ComboInstrument:
    symbol: str  # tên OG dùng trong Redis (vd "US30") — OF không được tự đổi, không biết gì về OG
    timeframe: str
    broker_symbol: str  # tên THẬT trên cTrader/broker đang connect (vd "#US30") — có thể khác hẳn
    # symbol phía trên: xác nhận thực tế trên FxPro demo, "US30" không tồn tại, phải dùng "#US30";
    # J225/HK50 lệch hẳn tên: "#Japan225"/"#HongKong50" (2026-09-19, ProtoOASymbolsListRes thật).


@dataclass
class ComboConfig:
    pubsub_channel: str
    instruments: List[ComboInstrument]

    def symbol_names(self) -> List[str]:
        """Danh sách broker_symbol duy nhất — converter.load() cần đúng tên THẬT trên broker."""
        seen = []
        for inst in self.instruments:
            if inst.broker_symbol not in seen:
                seen.append(inst.broker_symbol)
        return seen

    def broker_symbol_for(self, symbol: str) -> str:
        """Dịch tên OG (Redis) sang tên thật trên broker — dùng ở adapter.py trước khi gọi bất kỳ
        hàm nào của converter/orders, vì các hàm đó chỉ biết tên broker."""
        for inst in self.instruments:
            if inst.symbol.upper() == symbol.upper():
                return inst.broker_symbol
        raise KeyError(f"Symbol '{symbol}' không có trong combo.instruments của config.yaml")

    def broker_to_symbol_map(self) -> dict:
        """{tên broker: tên OG} cho converter.load(). Khoá theo tên BROKER (không phải tên OG) để
        mọi broker_symbol khác nhau trong config đều chắc chắn được load — đúng như symbol_names();
        tên OG chỉ dùng để hiển thị (vd "US30" thay vì "#US30") trong Telegram."""
        return {inst.broker_symbol: inst.symbol for inst in self.instruments}


@dataclass
class TelegramConfig:
    enabled: bool
    bot_token: str  # rỗng nếu enabled=False — logger._push_telegram tự no-op khi rỗng
    chat_id: str


@dataclass
class Config:
    ctrader: CTraderConfig
    redis: RedisConfig
    combo: ComboConfig
    risk: RiskConfig
    telegram: TelegramConfig
    state_db_path: str
    log_dir: str
    summary_interval_seconds: int


def _substitute_env(value: str) -> str:
    match = _ENV_PATTERN.match(value.strip())
    if not match:
        return value
    env_name = match.group(1)
    env_value = os.environ.get(env_name)
    if env_value is None:
        raise ValueError(f"Thiếu biến môi trường bắt buộc: {env_name}")
    return env_value


def load_config(path: str = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    ctrader_raw = raw["ctrader"]
    ctid = int(ctrader_raw["ctid_trader_account_id"])
    if ctid == 0:
        raise ValueError(
            "ctrader.ctid_trader_account_id chưa được điền trong config.yaml (đang là 0 - giá trị placeholder)."
        )

    ctrader = CTraderConfig(
        environment=ctrader_raw["environment"],
        host=ctrader_raw["host"],
        port=int(ctrader_raw["port"]),
        heartbeat_seconds=int(ctrader_raw["heartbeat_seconds"]),
        client_id=_substitute_env(ctrader_raw["client_id"]),
        client_secret=_substitute_env(ctrader_raw["client_secret"]),
        ctid_trader_account_id=ctid,
        token_file=ctrader_raw["token_file"],
        oauth_token_url=ctrader_raw["oauth_token_url"],
    )

    risk_raw = raw["risk"]
    risk = RiskConfig(
        risk_percent_per_trade=float(risk_raw["risk_percent_per_trade"]),
        risk_basis=risk_raw["risk_basis"],
        fallback_ksl=float(risk_raw["fallback_ksl"]),
        fallback_ktp=float(risk_raw["fallback_ktp"]),
        max_concurrent_trades=int(risk_raw["max_concurrent_trades"]),
    )

    redis_raw = raw["redis"]
    redis_config = RedisConfig(
        host=redis_raw["host"],
        port=int(redis_raw["port"]),
        password=_substitute_env(redis_raw["password"]),
        db=int(redis_raw["db"]),
        key_prefix=redis_raw["key_prefix"],
    )

    combo_raw = raw["combo"]
    if combo_raw["pubsub_channel"] == "TBD":
        raise ValueError(
            "combo.pubsub_channel chưa được điền trong config.yaml (đang là 'TBD') — cần tên kênh Pub/Sub thật từ OG."
        )
    combo = ComboConfig(
        pubsub_channel=combo_raw["pubsub_channel"],
        instruments=[
            ComboInstrument(symbol=inst["symbol"], timeframe=inst["timeframe"], broker_symbol=inst["broker_symbol"])
            for inst in combo_raw["instruments"]
        ],
    )

    # Mục "telegram" là OPTIONAL — thiếu hẳn trong config.yaml = coi như tắt, không lỗi. Chỉ đòi hỏi
    # biến môi trường khi thật sự bật, để không bắt phải set token/chat_id giả lúc chưa cần dùng.
    telegram_raw = raw.get("telegram", {})
    telegram_enabled = bool(telegram_raw.get("enabled", False))
    telegram = TelegramConfig(
        enabled=telegram_enabled,
        bot_token=_substitute_env(telegram_raw["bot_token"]) if telegram_enabled else "",
        chat_id=_substitute_env(telegram_raw["chat_id"]) if telegram_enabled else "",
    )

    return Config(
        ctrader=ctrader,
        redis=redis_config,
        combo=combo,
        risk=risk,
        telegram=telegram,
        state_db_path=raw["state"]["db_path"],
        log_dir=raw["logging"]["log_dir"],
        summary_interval_seconds=int(raw["logging"].get("summary_interval_seconds", 3600)),
    )
