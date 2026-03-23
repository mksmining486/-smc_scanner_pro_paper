"""
SMC Trading Engine - Core Trading Service.

Этот модуль представляет собой главный оркестратор (Facade), который объединяет
все аналитические движки (Pivot, Structure, Liquidity, OB, FVG) в единый конвейер.
Он управляет состоянием рынка, обрабатывает поток свечей и генерирует итоговые сигналы.

Гарантии:
- Потокобезопасность при обработке последовательных данных (single-threaded context).
- Полная типизация всех входных и выходных данных.
- Обработка ошибок внутри каждого этапа, чтобы сбой одного движка не ломал весь пайплайн.
- Сохранение полного состояния рынка для бэктестинга или живого трейдинга.
"""

import logging
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import copy

# Import Models
from src.models.candle import Candle
from src.models.pivot import PivotPoint, PivotType
from src.models.market_structure import MarketStructureState, StructureEvent, MarketTrend, StructureEventType
from src.models.liquidity import LiquidityPool, LiquidityType, LiquidityStatus
from src.models.order_block import OrderBlock, OrderBlockType, OrderBlockStatus
from src.models.fvg import FairValueGap, FVGStatus
from src.models.premium_discount import PremiumDiscountZone, PDZoneType

# Import Engines
from src.engines.pivot_detector import PivotDetector
from src.engines.market_structure_engine import MarketStructureEngine
from src.engines.liquidity_engine import LiquidityEngine
from src.engines.order_block_engine import OrderBlockEngine
from src.engines.fvg_engine import FVGEngine
from src.engines.premium_discount_engine import PremiumDiscountEngine

# Import Config
from src.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """
    Результирующий объект анализа после обработки новой свечи.
    Содержит полное снимок состояния рынка и новые события.
    """
    timestamp: datetime
    symbol: str
    timeframe: str
    
    # Текущее состояние
    current_trend: MarketTrend
    market_structure: MarketStructureState
    
    # Ключевые уровни и зоны
    active_pivots: List[PivotPoint]
    liquidity_pools: List[LiquidityPool]
    order_blocks: List[OrderBlock]
    fvgs: List[FairValueGap]
    pd_zone: Optional[PremiumDiscountZone]
    
    # События, произошедшие на текущей свече
    new_structure_events: List[StructureEvent]
    swept_liquidity: List[LiquidityPool]
    mitigated_order_blocks: List[OrderBlock]
    filled_fvgs: List[FairValueGap]
    
    # Торговые сигналы (высокоуровневые)
    signal_type: Optional[str] = None  # 'LONG', 'SHORT', 'NEUTRAL'
    signal_strength: float = 0.0       # 0.0 to 1.0
    entry_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    
    # Метаданные
    processing_time_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация результата в словарь (JSON-safe)."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trend": self.current_trend.value,
            "structure_state": self.market_structure.to_dict(),
            "active_pivots_count": len(self.active_pivots),
            "liquidity_pools_count": len(self.liquidity_pools),
            "order_blocks_count": len([ob for ob in self.order_blocks if ob.status == OrderBlockStatus.ACTIVE]),
            "fvgs_count": len([fvg for fvg in self.fvgs if fvg.status == FVGStatus.PENDING]),
            "pd_zone": self.pd_zone.to_dict() if self.pd_zone else None,
            "new_events": [ev.to_dict() for ev in self.new_structure_events],
            "swept_liquidity": [liq.to_dict() for liq in self.swept_liquidity],
            "signal": {
                "type": self.signal_type,
                "strength": self.signal_strength,
                "entry": str(self.entry_price) if self.entry_price else None,
                "sl": str(self.stop_loss) if self.stop_loss else None,
                "tp": str(self.take_profit) if self.take_profit else None
            },
            "errors": self.errors
        }


class SMCTradingService:
    """
    Главный сервис торговли SMC.
    
    Отвечает за:
    1. Управление жизненным циклом всех движков.
    2. Последовательную обработку входящих свечей.
    3. Агрегацию результатов и генерацию сигналов.
    4. Хранение текущего состояния рынка.
    """

    def __init__(self, settings: Settings):
        if not isinstance(settings, Settings):
            raise TypeError("Settings object expected")
        
        self.settings = settings
        self.symbol = settings.symbol
        self.timeframe = settings.timeframe
        
        # Инициализация движков с параметрами из настроек
        self.pivot_detector = PivotDetector(
            left_bars=settings.pivot_left_bars,
            right_bars=settings.pivot_right_bars
        )
        
        self.structure_engine = MarketStructureEngine(
            min_strength=settings.min_structure_strength
        )
        
        self.liquidity_engine = LiquidityEngine(
            sweep_threshold=settings.liquidity_sweep_threshold,
            lookback_bars=settings.liquidity_lookback_bars
        )
        
        self.ob_engine = OrderBlockEngine(
            min_volume_ratio=settings.ob_min_volume_ratio,
            max_mitigation_pct=settings.ob_max_mitigation_pct
        )
        
        self.fvg_engine = FVGEngine(
            min_size_pct=settings.fvg_min_size_pct,
            inversion_enabled=settings.fvg_inversion_enabled
        )
        
        self.pd_engine = PremiumDiscountEngine(
            fib_levels=settings.fib_levels_list,
            pd_threshold=settings.pd_zone_threshold
        )

        # Внутреннее состояние (State)
        self._candles_history: List[Candle] = []
        self._pivots: List[PivotPoint] = []
        self._structure_state: MarketStructureState = MarketStructureState(
            trend=MarketTrend.UNKNOWN,
            last_high=None,
            last_low=None,
            bos_count=0,
            choch_count=0
        )
        self._liquidity_pools: List[LiquidityPool] = []
        self._order_blocks: List[OrderBlock] = []
        self _fvgs: List[FairValueGap] = []
        self _pd_zone: Optional[PremiumDiscountZone] = None
        
        # Буфер для необработанных свечей (для детектора пивотов)
        self._pending_candles: List[Candle] = []

        logger.info(f"SMCTradingService initialized for {self.symbol} [{self.timeframe}]")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def process_candle(self, candle: Candle) -> AnalysisResult:
        """
        Обрабатывает новую свечу и возвращает обновленное состояние рынка.
        
        Алгоритм:
        1. Валидация свечи.
        2. Добавление в историю и буфер.
        3. Поиск новых пивотов (на подтвержденных данных).
        4. Обновление рыночной структуры (BOS/CHoCH).
        5. Пересчет зон ликвидности.
        6. Поиск и обновление Order Blocks.
        7. Поиск и обновление FVG.
        8. Расчет зоны Premium/Discount.
        9. Генерация сигналов.
        """
        import time
        start_time = time.time()
        errors = []

        try:
            # 1. Валидация
            if not candle or not candle.is_valid():
                logger.warning(f"Invalid candle received: {candle}")
                return self._create_empty_result(candle.timestamp, error="Invalid candle data")

            # 2. Обновление истории
            # Если свеча уже есть (обновление текущей), заменяем последнюю
            if self._candles_history and candle.timestamp == self._candles_history[-1].timestamp:
                self._candles_history[-1] = candle
            else:
                # Новая свеча
                if self._candles_history and candle.timestamp < self._candles_history[-1].timestamp:
                    logger.warning(f"Out of order candle ignored: {candle.timestamp}")
                    return self._create_empty_result(candle.timestamp, error="Out of order candle")
                self._candles_history.append(candle)
            
            # Добавляем в буфер для детектора пивотов
            self._pending_candles.append(candle)

            # 3. Детекция пивотов (возвращает только подтвержденные)
            new_pivots = []
            try:
                new_pivots = self.pivot_detector.process_candle(candle, self._pending_candles)
                if new_pivots:
                    self._pivots.extend(new_pivots)
                    # Очистка старых пивотов, которые ушли далеко назад (оптимизация памяти)
                    self._cleanup_old_pivots(candle)
            except Exception as e:
                errors.append(f"PivotDetector error: {str(e)}")
                logger.error(f"PivotDetector failed: {e}", exc_info=True)

            # 4. Обновление структуры рынка
            structure_events = []
            try:
                if new_pivots:
                    structure_events = self.structure_engine.update_structure(
                        pivots=new_pivots,
                        current_state=self._structure_state,
                        current_price=candle.close
                    )
                    # Применяем изменения состояния
                    if structure_events:
                        last_event = structure_events[-1]
                        self._structure_state.trend = last_event.new_trend
                        if last_event.event_type == StructureEventType.BOS:
                            self._structure_state.bos_count += 1
                        elif last_event.event_type == StructureEventType.CHOCH:
                            self._structure_state.choch_count += 1
                        
                        # Обновляем экстремумы
                        if last_event.breakout_level:
                            if self._structure_state.trend == MarketTrend.BULLISH:
                                self._structure_state.last_high = last_event.breakout_level
                            else:
                                self._structure_state.last_low = last_event.breakout_level
            except Exception as e:
                errors.append(f"StructureEngine error: {str(e)}")
                logger.error(f"StructureEngine failed: {e}", exc_info=True)

            # 5. Ликвидность
            swept_pools = []
            try:
                # Обновляем пулы на основе новых пивотов
                if new_pivots:
                    self.liquidity_engine.update_pools(
                        pivots=new_pivots,
                        existing_pools=self._liquidity_pools,
                        trend=self._structure_state.trend
                    )
                
                # Проверяем сканирование текущей ценой
                swept_pools = self.liquidity_engine.check_sweeps(
                    candle=candle,
                    pools=self._liquidity_pools
                )
            except Exception as e:
                errors.append(f"LiquidityEngine error: {str(e)}")
                logger.error(f"LiquidityEngine failed: {e}", exc_info=True)

            # 6. Order Blocks
            mitigated_obs = []
            try:
                # Поиск новых Ордерблоков на основе последних пивотов и структурных событий
                if new_pivots or structure_events:
                    new_obs = self.ob_engine.identify_order_blocks(
                        pivots=new_pivots,
                        candles=self._candles_history,
                        structure_events=structure_events
                    )
                    self._order_blocks.extend(new_obs)
                
                # Проверка митигации существующих
                mitigated_obs = self.ob_engine.check_mitigation(
                    candle=candle,
                    order_blocks=self._order_blocks
                )
            except Exception as e:
                errors.append(f"OrderBlockEngine error: {str(e)}")
                logger.error(f"OrderBlockEngine failed: {e}", exc_info=True)

            # 7. Fair Value Gaps
            filled_fvgs = []
            try:
                # Поиск новых FVG на последней свече (или паттерне из 3 свечей)
                if len(self._candles_history) >= 3:
                    new_fvgs = self.fvg_engine.detect_fvgs(
                        candles=self._candles_history[-3:]
                    )
                    # Добавляем только уникальные (по времени)
                    existing_times = {f.timestamp for f in self._fvgs}
                    for fvg in new_fvgs:
                        if fvg.timestamp not in existing_times:
                            self._fvgs.append(fvg)
                
                # Проверка заполнения
                filled_fvgs = self.fvg_engine.check_fills(
                    candle=candle,
                    fvgs=self._fvgs
                )
            except Exception as e:
                errors.append(f"FVGEngine error: {str(e)}")
                logger.error(f"FVGEngine failed: {e}", exc_info=True)

            # 8. Premium / Discount Zone
            pd_zone = None
            try:
                # Пересчитываем зону на основе текущего свинга (High/Low тренда)
                if self._structure_state.last_high and self._structure_state.last_low:
                    pd_zone = self.pd_engine.calculate_zone(
                        high=self._structure_state.last_high,
                        low=self._structure_state.last_low,
                        current_price=candle.close
                    )
                    self._pd_zone = pd_zone
            except Exception as e:
                errors.append(f"PDZoneEngine error: {str(e)}")
                logger.error(f"PDZoneEngine failed: {e}", exc_info=True)

            # 9. Генерация сигналов
            signal_type, strength, entry, sl, tp = self._generate_signal(
                candle=candle,
                swept_liquidity=swept_pools,
                mitigated_obs=mitigated_obs,
                filled_fvgs=filled_fvgs,
                pd_zone=pd_zone
            )

            processing_time = (time.time() - start_time) * 1000

            return AnalysisResult(
                timestamp=candle.timestamp,
                symbol=self.symbol,
                timeframe=self.timeframe,
                current_trend=self._structure_state.trend,
                market_structure=copy.deepcopy(self._structure_state),
                active_pivots=self._get_relevant_pivots(candle),
                liquidity_pools=[p for p in self._liquidity_pools if p.status == LiquidityStatus.ACTIVE],
                order_blocks=[ob for ob in self._order_blocks if ob.status == OrderBlockStatus.ACTIVE],
                fvgs=[f for f in self._fvgs if f.status == FVGStatus.PENDING],
                pd_zone=pd_zone,
                new_structure_events=structure_events,
                swept_liquidity=swept_pools,
                mitigated_order_blocks=mitigated_obs,
                filled_fvgs=filled_fvgs,
                signal_type=signal_type,
                signal_strength=strength,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                processing_time_ms=processing_time,
                errors=errors
            )

        except Exception as e:
            logger.critical(f"Critical error in process_candle: {e}", exc_info=True)
            return self._create_empty_result(
                candle.timestamp if candle else datetime.utcnow(), 
                error=f"Critical system error: {str(e)}"
            )
    # -------------------------------------------------------------------------
    # Логика генерации сигналов (Signal Logic)
    # -------------------------------------------------------------------------
    def _generate_signal(
        self,
        candle: Candle,
        swept_liquidity: List[LiquidityPool],
        mitigated_obs: List[OrderBlock],
        filled_fvgs: List[FairValueGap],
        pd_zone: Optional[PremiumDiscountZone]
    ) -> Tuple[Optional[str], float, Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        """
        Анализирует текущее состояние и события для генерации торгового сигнала.
        
        Логика (упрощенная модель SMC):
        1. Снятие ликвидности (Sweep) + Разворот структуры -> Сигнал.
        2. Возврат цены в Order Block / FVG в зоне Discount/Premium -> Сигнал.
        
        Returns:
            (signal_type, strength, entry, sl, tp)
            signal_type: 'LONG', 'SHORT', или None
            strength: 0.0 - 1.0
        """
        signal_type = None
        strength = 0.0
        entry = None
        sl = None
        tp = None

        current_price = candle.close
        trend = self._structure_state.trend

        # --- ЛОГИКА ДЛЯ LONG ---
        if trend == MarketTrend.BULLISH:
            # Проверка условий для входа в Long
            is_discount = pd_zone and pd_zone.zone_type == PDZoneType.DISCOUNT
            
            # Триггер 1: Снятие ликвидности снизу (Sell Side Liquidity)
            liquidity_sweep = any(pool.type == LiquidityType.SSL for pool in swept_liquidity)
            
            # Триггер 2: Митигация бычьего Ордерблока
            ob_mitigation = any(ob.type == OrderBlockType.BULLISH for ob in mitigated_obs)
            
            # Триггер 3: Заполнение Бычьего FVG
            fvg_fill = any(fvg.direction == 'bullish' for fvg in filled_fvgs)

            if is_discount and (liquidity_sweep or ob_mitigation or fvg_fill):
                signal_type = "LONG"
                
                # Расчет силы сигнала
                score = 0.0
                if liquidity_sweep: score += 0.4
                if ob_mitigation: score += 0.3
                if fvg_fill: score += 0.2
                if is_discount: score += 0.1
                strength = min(score, 1.0)

                # Определение уровней
                # Entry: Текущая цена или зона OB/FVG (берем консервативно - текущая)
                entry = current_price
                
                # Stop Loss: Ниже последнего минимума (Swing Low) или ниже OB
                last_pivot_low = self._get_last_pivot_low()
                if last_pivot_low:
                    sl = last_pivot_low * Decimal('0.999') # Небольшой буфер
                elif mitigated_obs:
                    sl = min(ob.low for ob in mitigated_obs if ob.type == OrderBlockType.BULLISH) * Decimal('0.999')
                else:
                    sl = current_price * Decimal('0.99') # Fallback

                # Take Profit: Предыдущий хай или зона ликвидности сверху
                last_pivot_high = self._get_last_pivot_high()
                if last_pivot_high:
                    tp = last_pivot_high
                else:
                    # Целевой уровень R:R 1:2 минимум
                    risk = entry - sl
                    tp = entry + (risk * Decimal('2.0'))

        # --- ЛОГИКА ДЛЯ SHORT ---
        elif trend == MarketTrend.BEARISH:
            # Проверка условий для входа в Short
            is_premium = pd_zone and pd_zone.zone_type == PDZoneType.PREMIUM
            
            # Триггер 1: Снятие ликвидности сверху (Buy Side Liquidity)
            liquidity_sweep = any(pool.type == LiquidityType.BSL for pool in swept_liquidity)
            
            # Триггер 2: Митигация медвежьего Ордерблока
            ob_mitigation = any(ob.type == OrderBlockType.BEARISH for ob in mitigated_obs)
            
            # Триггер 3: Заполнение Медвежьего FVG
            fvg_fill = any(fvg.direction == 'bearish' for fvg in filled_fvgs)

            if is_premium and (liquidity_sweep or ob_mitigation or fvg_fill):
                signal_type = "SHORT"
                
                # Расчет силы сигнала
                score = 0.0
                if liquidity_sweep: score += 0.4
                if ob_mitigation: score += 0.3
                if fvg_fill: score += 0.2
                if is_premium: score += 0.1
                strength = min(score, 1.0)

                # Определение уровней
                entry = current_price
                
                # Stop Loss: Выше последнего максимума
                last_pivot_high = self._get_last_pivot_high()
                if last_pivot_high:
                    sl = last_pivot_high * Decimal('1.001')
                elif mitigated_obs:
                    sl = max(ob.high for ob in mitigated_obs if ob.type == OrderBlockType.BEARISH) * Decimal('1.001')
                else:
                    sl = current_price * Decimal('1.01')

                # Take Profit: Предыдущий лоу
                last_pivot_low = self._get_last_pivot_low()
                if last_pivot_low:
                    tp = last_pivot_low
                else:
                    risk = sl - entry
                    tp = entry - (risk * Decimal('2.0'))

        return signal_type, strength, entry, sl, tp

    # -------------------------------------------------------------------------
    # Вспомогательные методы состояния (State Helpers)
    # -------------------------------------------------------------------------
    
    def _get_last_pivot_low(self) -> Optional[Decimal]:
        """Находит последний подтвержденный минимум (Swing Low)."""
        lows = [p.price for p in self._pivots if p.pivot_type == PivotType.LOW]
        return max(lows) if lows else None

    def _get_last_pivot_high(self) -> Optional[Decimal]:
        """Находит последний подтвержденный максимум (Swing High)."""
        highs = [p.price for p in self._pivots if p.pivot_type == PivotType.HIGH]
        return min(highs) if highs else None

    def _get_relevant_pivots(self, current_candle: Candle) -> List[PivotPoint]:
        """
        Фильтрует пивоты, оставляя только релевантные для текущего анализа.
        Обычно это последние 5-10 свингов.
        """
        # Сортируем по времени убывания и берем последние 10
        sorted_pivots = sorted(self._pivots, key=lambda x: x.timestamp, reverse=True)
        return sorted_pivots[:10]

    def _cleanup_old_pivots(self, current_candle: Candle):
        """
        Удаляет старые пивоты из памяти для оптимизации.
        Храним только пивоты за последние N свечей или за определенный период.
        """
        if len(self._pivots) < 50:
            return
        
        # Оставляем только последние 50 пивотов
        self._pivots = sorted(self._pivots, key=lambda x: x.timestamp, reverse=True)[:50]

    def _create_empty_result(self, timestamp: datetime, error: str = "") -> AnalysisResult:
        """Создает пустой результат при ошибке или отсутствии данных."""
        errors = [error] if error else []
        return AnalysisResult(
            timestamp=timestamp,
            symbol=self.symbol,
            timeframe=self.timeframe,
            current_trend=MarketTrend.UNKNOWN,
            market_structure=self._structure_state,
            active_pivots=[],
            liquidity_pools=[],
            order_blocks=[],
            fvgs=[],
            pd_zone=None,
            new_structure_events=[],
            swept_liquidity=[],
            mitigated_order_blocks=[],
            filled_fvgs=[],
            signal_type=None,
            signal_strength=0.0,
            errors=errors
        )

    # -------------------------------------------------------------------------
    # Методы управления жизненным циклом
    # -------------------------------------------------------------------------
    
    def reset(self):
        """Полный сброс состояния сервиса. Используется при перезапуске стратегии."""
        logger.info("Resetting SMCTradingService state...")
        self._candles_history.clear()
        self._pivots.clear()
        self._structure_state = MarketStructureState(
            trend=MarketTrend.UNKNOWN,
            last_high=None,
            last_low=None,
            bos_count=0,
            choch_count=0
        )
        self._liquidity_pools.clear()
        self._order_blocks.clear()
        self._fvgs.clear()
        self._pd_zone = None
        self._pending_candles.clear()
        logger.info("SMCTradingService state reset complete.")

    def get_state_snapshot(self) -> Dict[str, Any]:
        """Возвращает полный снимок текущего состояния для отладки или UI."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candles_count": len(self._candles_history),
            "pivots_count": len(self._pivots),
            "trend": self._structure_state.trend.value,
            "bos_count": self._structure_state.bos_count,
            "choch_count": self._structure_state.choch_count,
            "active_liquidity_pools": len([p for p in self._liquidity_pools if p.status == LiquidityStatus.ACTIVE]),
            "active_order_blocks": len([ob for ob in self._order_blocks if ob.status == OrderBlockStatus.ACTIVE]),
            "pending_fvgs": len([f for f in self._fvgs if f.status == FVGStatus.PENDING]),
            "last_update": self._candles_history[-1].timestamp.isoformat() if self._candles_history else None
        }