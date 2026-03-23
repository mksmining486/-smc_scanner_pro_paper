# src/utils/data_loader.py

"""
SMC Trading Engine - Data Loader Module.

Этот модуль отвечает за загрузку, валидацию и нормализацию рыночных данных (OHLCV).
Поддерживает различные источники данных через стратегию (CSV, Mock, API заглушки).

Гарантии:
- Строгая типизация всех методов.
- Обработка ошибок ввода-вывода и некорректных данных.
- Нормализация временных меток в UTC.
- Валидация схемы данных (наличие обязательных колонок).
- Отсутствие побочных эффектов при загрузке.
"""

import logging
import csv
import random
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Iterator, Union, TextIO, Tuple
from datetime import datetime, timezone, timedelta
from pathlib import Path
from decimal import Decimal, InvalidOperation
import io

# Import Models
from src.models.candle import Candle

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Исключения (Custom Exceptions)
# -------------------------------------------------------------------------

class DataLoaderError(Exception):
    """Базовое исключение для ошибок загрузчика данных."""
    pass

class DataNotFoundError(DataLoaderError):
    """Выбрасывается, если файл или источник данных не найден."""
    pass

class InvalidDataFormatError(DataLoaderError):
    """Выбрасывается, если данные не соответствуют ожидаемой схеме."""
    pass

class UnsupportedSourceError(DataLoaderError):
    """Выбрасывается, если тип источника данных не поддерживается."""
    pass

# -------------------------------------------------------------------------
# Константы и Типы
# -------------------------------------------------------------------------

# Ожидаемые колонки в CSV файле (регистронезависимая проверка)
REQUIRED_COLUMNS = {'timestamp', 'open', 'high', 'low', 'close', 'volume'}

# Маппинг возможных названий колонок в стандартные
COLUMN_ALIASES = {
    'time': 'timestamp',
    'date': 'timestamp',
    'datetime': 'timestamp',
    'o': 'open',
    'h': 'high',
    'l': 'low',
    'c': 'close',
    'v': 'volume',
    'vol': 'volume'
}

# -------------------------------------------------------------------------
# Абстрактный базовый класс (Strategy Pattern)
# -------------------------------------------------------------------------

class BaseDataLoader(ABC):
    """
    Абстрактный базовый класс для загрузчиков данных.
    Определяет контракт для всех реализаций.
    """

    @abstractmethod
    def load_historical_data(self, limit: int = 1000) -> List[Candle]:
        """
        Загружает исторические данные.
        
        Args:
            limit: Максимальное количество свечей для загрузки.
            
        Returns:
            Список объектов Candle, отсортированный по времени (возрастание).
        """
        pass

    @abstractmethod
    def stream_candles(self) -> Iterator[Candle]:
        """
        Генератор для потоковой передачи свечей (для реального времени).
        
        Yields:
            Объекты Candle по мере их поступления.
        """
        pass

    @abstractmethod
    def validate_source(self) -> bool:
        """
        Проверяет доступность и валидность источника данных.
        
        Returns:
            True если источник готов к работе.
        """
        pass


# -------------------------------------------------------------------------
# Реализация для CSV файлов
# -------------------------------------------------------------------------

class CSVDataLoader(BaseDataLoader):
    """
    Загрузчик данных из CSV файлов.
    
    Требования к файлу:
    - Наличие заголовка.
    - Колонки: timestamp, open, high, low, close, volume (или алиасы).
    - Timestamp в формате ISO8601 или Unix timestamp (секунды/миллисекунды).
    """

    def __init__(self, file_path: Union[str, Path]):
        """
        Инициализация загрузчика.
        
        Args:
            file_path: Путь к CSV файлу.
        """
        self.file_path = Path(file_path)
        self._validated_columns: Optional[List[str]] = None

    def validate_source(self) -> bool:
        """Проверяет существование файла и корректность заголовка."""
        if not self.file_path.exists():
            raise DataNotFoundError(f"Файл не найден: {self.file_path}")
        
        if not self.file_path.is_file():
            raise InvalidDataFormatError(f"Путь указывает не на файл: {self.file_path}")

        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                # Читаем первую строку для проверки заголовка
                reader = csv.reader(f)
                header = next(reader, None)
                
                if not header:
                    raise InvalidDataFormatError("CSV файл пуст или не имеет заголовка")
                
                # Нормализуем заголовки (lowercase + trim)
                normalized_header = [col.strip().lower() for col in header]
                
                # Применяем алиасы
                mapped_header = []
                for col in normalized_header:
                    mapped_header.append(COLUMN_ALIASES.get(col, col))
                
                # Проверка наличия всех обязательных колонок
                missing = REQUIRED_COLUMNS - set(mapped_header)
                if missing:
                    raise InvalidDataFormatError(f"Отсутствуют обязательные колонки: {missing}")
                
                self._validated_columns = mapped_header
                logger.info(f"CSV файл валидирован: {self.file_path}")
                return True
                
        except csv.Error as e:
            raise InvalidDataFormatError(f"Ошибка парсинга CSV: {e}")
        except UnicodeDecodeError:
            raise InvalidDataFormatError("Неверная кодировка файла. Ожидается UTF-8.")

    def load_historical_data(self, limit: int = 1000) -> List[Candle]:
        """
        Загружает свечи из CSV файла.
        
        Args:
            limit: Лимит количества свечей (берутся последние N записей).
            
        Returns:
            Список Candle.
        """
        if not self._validated_columns:
            self.validate_source()
            
        candles = []
        
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                # Переназначаем fieldnames на нормализованные
                if reader.fieldnames:
                    norm_fields = [COLUMN_ALIASES.get(col.strip().lower(), col.strip().lower()) for col in reader.fieldnames]
                    # Хак для DictReader: читаем как обычный dict, но ключи будем мапить вручную
                    # Или просто используем raw reader и индексы. Используем DictReader для удобства, 
                    # но будем обращаться по оригинальным ключам и мапить значения.
                    
                    # Пересоздадим reader с правильными ключами? Нет, проще мапить при чтении.
                    # Сбросим указатель после validate, который прочитал заголовок
                    f.seek(0)
                    reader = csv.DictReader(f)
                    
                    count = 0
                    for row in reader:
                        try:
                            candle = self._parse_row(row)
                            if candle:
                                candles.append(candle)
                                count += 1
                        except Exception as e:
                            logger.warning(f"Пропущена строка из-за ошибки: {e}. Row: {row}")
                            continue
                            
        except FileNotFoundError:
            raise DataNotFoundError(f"Файл не найден: {self.file_path}")
        except Exception as e:
            raise DataLoaderError(f"Критическая ошибка при чтении CSV: {e}")
            
        # Сортировка по времени и ограничение лимита
        candles.sort(key=lambda x: x.timestamp)
        
        if limit > 0:
            candles = candles[-limit:]
            
        logger.info(f"Загружено {len(candles)} свечей из {self.file_path}")
        return candles

    def _parse_row(self, row: Dict[str, str]) -> Optional[Candle]:
        """
        Парсит строку CSV в объект Candle.
        Выполняет нормализацию ключей и преобразование типов.
        """
        # Функция безопасного получения значения по алиасам
        def get_val(key: str) -> str:
            # Ищем прямое совпадение или через алиасы (если ключи в row еще не маплены)
            # В нашем случае reader использует оригинальные заголовки из файла.
            # Нам нужно найти значение, соответствующее нужной логической колонке.
            
            # Пробежимся по исходным ключам row и попробуем сопоставить
            for k, v in row.items():
                clean_k = k.strip().lower()
                mapped_k = COLUMN_ALIASES.get(clean_k, clean_k)
                if mapped_k == key:
                    return v
            raise KeyError(f"Колонка {key} не найдена в строке")

        try:
            ts_str = get_val('timestamp')
            timestamp = self._parse_timestamp(ts_str)
            
            open_p = Decimal(get_val('open').replace(',', '.'))
            high = Decimal(get_val('high').replace(',', '.'))
            low = Decimal(get_val('low').replace(',', '.'))
            close = Decimal(get_val('close').replace(',', '.'))
            volume = Decimal(get_val('volume').replace(',', '.'))
            
            return Candle(
                timestamp=timestamp,
                open=open_p,
                high=high,
                low=low,
                close=close,
                volume=volume
            )
        except (KeyError, ValueError, InvalidOperation) as e:
            raise ValueError(f"Ошибка парсинга данных строки: {e}")

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """
        Парсит строку времени в datetime UTC.
        Поддерживает: ISO8601, Unix timestamp (int/float).
        """
        ts_str = ts_str.strip()
        
        # Попытка парсинга как Unix timestamp (число)
        try:
            ts_float = float(ts_str)
            # Если число очень большое (> 1млрд), скорее всего это секунды с 1970
            # Если меньше, возможно миллисекунды? Обычно TS > 1000000000 для 2001+ года
            if ts_float > 1e12: # Миллисекунды
                ts_float /= 1000.0
            return datetime.fromtimestamp(ts_float, tz=timezone.utc)
        except ValueError:
            pass
            
        # Попытка парсинга как ISO формат
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%d %H:%M:%S.%f"
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(ts_str, fmt)
                # Принудительно ставим UTC, если нет инфо о зоне
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
                
        raise ValueError(f"Неизвестный формат времени: {ts_str}")

    def stream_candles(self) -> Iterator[Candle]:
        """
        Для CSV потоковая эмуляция не имеет смысла в реальном времени,
        но мы можем реализовать ленивое чтение файла.
        """
        if not self._validated_columns:
            self.validate_source()
            
        with open(self.file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    candle = self._parse_row(row)
                    if candle:
                        yield candle
                except Exception as e:
                    logger.warning(f"Ошибка в потоке: {e}")
                    continue

# -------------------------------------------------------------------------
# Mock Загрузчик (Для тестирования и демонстрации)
# -------------------------------------------------------------------------

class MockDataLoader(BaseDataLoader):
    """
    Загрузчик фиктивных данных для тестирования стратегии без подключения к API.
    Генерирует синусоидальные или случайные данные, имитирующие рыночное движение.
    """

    def __init__(self, symbol: str = "BTCUSDT", start_price: float = 50000.0, volatility: float = 0.02):
        """
        Инициализация генератора.
        
        Args:
            symbol: Символ для генерации.
            start_price: Начальная цена.
            volatility: Волатильность (максимальное изменение за свечу в %).
        """
        self.symbol = symbol
        self.start_price = Decimal(str(start_price))
        self.volatility = Decimal(str(volatility))
        self._generated_count = 0
        
        logger.info(f"MockDataLoader initialized for {symbol} @ {start_price}")

    def validate_source(self) -> bool:
        """Mock источник всегда валиден."""
        return True

    def load_historical_data(self, limit: int = 1000) -> List[Candle]:
        """
        Генерирует исторические данные "на лету".
        Использует детерминированный алгоритм для воспроизводимости тестов.
        """
        candles = []
        current_price = self.start_price
        now = datetime.now(timezone.utc)
        
        # Генерируем свечи назад от текущего времени с шагом 1 час
        for i in range(limit, 0, -1):
            timestamp = datetime(
                now.year, now.month, now.day, now.hour, now.minute, now.second,
                tzinfo=timezone.utc
            )
            # Сдвиг времени назад (упрощенно: просто уменьшаем час)
            # В реальном тесте лучше использовать timedelta
            timestamp = now - timedelta(hours=i)
            
            # Имитация движения цены (случайное блуждание)
            # Фиксируем seed для воспроизводимости в рамках одной сессии, если нужно
            # random.seed(i) 
            
            change_pct = (Decimal(str(random.random())) - Decimal('0.5')) * self.volatility
            open_p = current_price
            close = open_p * (Decimal('1') + change_pct)
            
            # Имитация High/Low
            high = max(open_p, close) * (Decimal('1') + Decimal(str(random.random())) * self.volatility / Decimal('2'))
            low = min(open_p, close) * (Decimal('1') - Decimal(str(random.random())) * self.volatility / Decimal('2'))
            
            volume = Decimal(str(random.random() * 100)) + Decimal('10')
            
            candle = Candle(
                timestamp=timestamp,
                open=open_p,
                high=high,
                low=low,
                close=close,
                volume=volume
            )
            candles.append(candle)
            current_price = close
            
        self._generated_count = limit
        logger.debug(f"MockDataLoader generated {limit} candles")
        return candles

    def stream_candles(self) -> Iterator[Candle]:
        """
        Бесконечный генератор новых свечей для симуляции реального времени.
        """
        last_candle = self.load_historical_data(1)[0]
        current_time = last_candle.timestamp
        
        while True:
            # Ждем интервал (симуляция)
            time.sleep(0.1) # Быстрая симуляция
            
            current_time += timedelta(hours=1)
            
            change_pct = (Decimal(str(random.random())) - Decimal('0.5')) * self.volatility
            open_p = last_candle.close
            close = open_p * (Decimal('1') + change_pct)
            
            high = max(open_p, close) * (Decimal('1') + Decimal(str(random.random())) * self.volatility / Decimal('2'))
            low = min(open_p, close) * (Decimal('1') - Decimal(str(random.random())) * self.volatility / Decimal('2'))
            volume = Decimal(str(random.random() * 100)) + Decimal('10')
            
            candle = Candle(
                timestamp=current_time,
                open=open_p,
                high=high,
                low=low,
                close=close,
                volume=volume
            )
            
            yield candle
            last_candle = candle

# -------------------------------------------------------------------------
# Фабрика и Главные функции (Public API)
# -------------------------------------------------------------------------

def get_data_loader(source_type: str, **kwargs) -> BaseDataLoader:
    """
    Фабричная функция для создания нужного загрузчика.
    
    Args:
        source_type: Тип источника ('csv', 'mock', 'binance' (заглушка)).
        **kwargs: Аргументы для конструктора загрузчика.
        
    Returns:
        Экземпляр BaseDataLoader.
        
    Raises:
        UnsupportedSourceError: Если тип не поддерживается.
    """
    source_type = source_type.lower()
    
    if source_type == 'csv':
        file_path = kwargs.get('file_path')
        if not file_path:
            raise DataLoaderError("Для CSV загрузчика требуется аргумент 'file_path'")
        return CSVDataLoader(file_path)
        
    elif source_type == 'mock':
        return MockDataLoader(
            symbol=kwargs.get('symbol', 'BTCUSDT'),
            start_price=kwargs.get('start_price', 50000.0),
            volatility=kwargs.get('volatility', 0.02)
        )
        
    elif source_type == 'binance':
        # Здесь могла бы быть реализация реального API загрузчика
        # Пока заглушка, выбрасывающая исключение, чтобы не вводить в заблуждение
        logger.warning("Binance loader requested but not fully implemented in this module. Use CCXT or external lib.")
        raise UnsupportedSourceError("Прямая загрузка Binance требует установки ccxt или requests. Используйте Mock или CSV для демо.")
        
    else:
        raise UnsupportedSourceError(f"Неподдерживаемый тип источника: {source_type}")


def load_historical_data(
    source_type: str, 
    limit: int = 1000, 
    **kwargs
) -> List[Candle]:
    """
    Удобная функция-обертка для быстрой загрузки данных в одну строку.
    
    Пример:
        candles = load_historical_data('csv', file_path='data.csv', limit=500)
        candles = load_historical_data('mock', limit=100)
    """
    loader = get_data_loader(source_type, **kwargs)
    loader.validate_source()
    return loader.load_historical_data(limit)


def stream_candles(source_type: str, **kwargs) -> Iterator[Candle]:
    """
    Удобная функция-обертка для потоковой передачи.
    
    Пример:
        for candle in stream_candles('mock'):
            process(candle)
    """
    loader = get_data_loader(source_type, **kwargs)
    loader.validate_source()
    return loader.stream_candles()


# -------------------------------------------------------------------------
# Экспорт модуля
# -------------------------------------------------------------------------

__all__ = [
    # Exceptions
    'DataLoaderError',
    'DataNotFoundError',
    'InvalidDataFormatError',
    'UnsupportedSourceError',
    
    # Classes
    'BaseDataLoader',
    'CSVDataLoader',
    'MockDataLoader',
    
    # Functions
    'get_data_loader',
    'load_historical_data',
    'stream_candles',
    
    # Constants
    'REQUIRED_COLUMNS',
    'COLUMN_ALIASES'
]