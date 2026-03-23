import os
import json
from typing import Optional, List, Dict, Any
from decimal import Decimal
from pydantic import BaseModel, Field, validator, root_validator
from pathlib import Path

class RiskConfig(BaseModel):
    """Конфигурация управления рисками"""
    risk_per_trade_percent: Decimal = Field(default=Decimal("1.0"), ge=0.1, le=5.0)
    max_daily_loss_percent: Decimal = Field(default=Decimal("3.0"), ge=1.0, le=10.0)
    min_reward_risk_ratio: Decimal = Field(default=Decimal("1.5"), ge=1.0)
    max_open_positions: int = Field(default=3, ge=1, le=10)
    default_sl_points: int = Field(default=50, ge=10)
    default_tp_points: int = Field(default=150, ge=50)

class StructureConfig(BaseModel):
    """Конфигурация детектора рыночной структуры"""
    swing_strength: int = Field(default=5, ge=3, le=10)
    structure_break_threshold_points: int = Field(default=10, ge=5)
    enable_trend_filter: bool = Field(default=True)

class FvgConfig(BaseModel):
    """Конфигурация обнаружения Fair Value Gaps"""
    min_fvg_size_points: int = Field(default=3, ge=1)
    max_fvg_age_bars: int = Field(default=50, ge=10)
    mitigation_threshold_percent: Decimal = Field(default=Decimal("0.95"), ge=0.8, le=1.0)

class LiquidityConfig(BaseModel):
    """Конфигурация уровней ликвидности"""
    lookback_bars: int = Field(default=20, ge=10, le=100)
    min_swing_strength: int = Field(default=3, ge=2)
    sweep_confirmation_bars: int = Field(default=2, ge=1)

class OrderBlockConfig(BaseModel):
    """Конфигурация ордерблоков"""
    min_ob_strength: int = Field(default=3, ge=2)
    ob_decay_bars: int = Field(default=30, ge=10)
    refresh_on_mitigation: bool = Field(default=True)

class BacktestConfig(BaseModel):
    """Конфигурация бэктестера"""
    initial_balance: Decimal = Field(default=Decimal("10000.00"), gt=0)
    commission_per_lot: Decimal = Field(default=Decimal("2.0"), ge=0)
    slippage_points: int = Field(default=1, ge=0)

class Config(BaseModel):
    """Основной класс конфигурации приложения"""
    app_name: str = "SmartMoneySystem"
    version: str = "1.0.0"
    
    # Пути
    data_dir: Path = Field(default=Path("./data"))
    logs_dir: Path = Field(default=Path("./logs"))
    
    # Торговые настройки
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    broker_spread_points: int = Field(default=10, ge=0)
    
    # Подконфигурации
    risk: RiskConfig = Field(default_factory=RiskConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    fvg: FvgConfig = Field(default_factory=FvgConfig)
    liquidity: LiquidityConfig = Field(default_factory=LiquidityConfig)
    orderblock: OrderBlockConfig = Field(default_factory=OrderBlockConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            Decimal: lambda v: float(v),
            Path: lambda v: str(v),
        }

    @validator('timeframe')
    def validate_timeframe(cls, v):
        valid_tf = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
        if v not in valid_tf:
            raise ValueError(f"Недопустимый таймфрейм: {v}. Допустимые: {valid_tf}")
        return v

    @root_validator(pre=True)
    def create_directories(cls, values):
        data_dir = values.get('data_dir', Path("./data"))
        logs_dir = values.get('logs_dir', Path("./logs"))
        
        if isinstance(data_dir, str):
            data_dir = Path(data_dir)
        if isinstance(logs_dir, str):
            logs_dir = Path(logs_dir)
            
        data_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        
        values['data_dir'] = data_dir
        values['logs_dir'] = logs_dir
        return values

    @classmethod
    def load_from_file(cls, file_path: str) -> 'Config':
        """Загрузка конфигурации из JSON файла"""
        path = Path(file_path)
        if not path.exists():
            print(f"Файл конфигурации {file_path} не найден. Используются настройки по умолчанию.")
            return cls()
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return cls(**data)
        except Exception as e:
            print(f"Ошибка при загрузке конфигурации: {e}. Используются настройки по умолчанию.")
            return cls()

    def save_to_file(self, file_path: str) -> None:
        """Сохранение текущей конфигурации в JSON файл"""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Сериализация с обработкой специальных типов
        data = self.dict()
        # Конвертация Decimal в строку для JSON, чтобы не потерять точность
        def convert_decimals(obj):
            if isinstance(obj, dict):
                return {k: convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_decimals(i) for i in obj]
            elif isinstance(obj, Decimal):
                return str(obj)
            elif isinstance(obj, Path):
                return str(obj)
            return obj
            
        final_data = convert_decimals(data)
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=4, ensure_ascii=False)
        print(f"Конфигурация сохранена в {file_path}")

# Глобальный экземпляр конфигурации
settings = Config()

def get_settings() -> Config:
    """Получить глобальный экземпляр конфигурации"""
    return settings

def reload_settings(file_path: Optional[str] = None) -> Config:
    """Перезагрузить конфигурацию из файла или создать новую"""
    global settings
    if file_path:
        settings = Config.load_from_file(file_path)
    else:
        settings = Config()
    return settings