"""
Signal Generator Engine
Generates trading signals based on confluence of market structure, FVG, Order Blocks, and Liquidity.
"""

from decimal import Decimal
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from src.models.signal import TradingSignal, SignalStrength, SignalType
from src.models.candle import Candle
from src.models.fvg import FairValueGap
from src.models.orderblock import OrderBlock
from src.models.liquidity import LiquidityPool
from src.models.market_structure import MarketStructure, StructurePoint
from src.models.premium_discount import PremiumDiscountZone


class SignalGenerator:
    """
    Generates high-probability trading signals based on multi-factor confluence.

    Confluence factors:
    1. Market Structure (BOS/CHoCH)
    2. Fair Value Gap (FVG) presence
    3. Order Block interaction
    4. Liquidity sweep detection
    5. Premium/Discount zone alignment
    """

    def __init__(self):
        self.min_confluence_score: int = 3  # Minimum factors required for signal
        self.signal_history: List[TradingSignal] = []

    def generate_signal(
        self,
        current_candle: Candle,
        market_structure: MarketStructure,
        fvg_list: List[FairValueGap],
        order_blocks: List[OrderBlock],
        liquidity_pools: List[LiquidityPool],
        premium_discount: Optional[PremiumDiscountZone] = None
    ) -> Optional[TradingSignal]:
        """
        Generate trading signal based on confluence of all factors.

        Args:
            current_candle: Current candle being analyzed
            market_structure: Current market structure state
            fvg_list: List of active FVGs
            order_blocks: List of active Order Blocks
            liquidity_pools: List of identified liquidity pools
            premium_discount: Current premium/discount zone

        Returns:
            TradingSignal if confluence criteria met, None otherwise
        """
        # Determine potential direction based on market structure
        potential_direction = self._determine_direction(market_structure)

        if potential_direction is None:
            return None

        # Calculate confluence score for the potential direction
        confluence_score, factors = self._calculate_confluence(
            current_candle=current_candle,
            direction=potential_direction,
            market_structure=market_structure,
            fvg_list=fvg_list,
            order_blocks=order_blocks,
            liquidity_pools=liquidity_pools,
            premium_discount=premium_discount
        )

        # Check if minimum confluence threshold is met
        if confluence_score < self.min_confluence_score:
            return None

        # Determine signal strength based on score
        signal_strength = self._determine_strength(confluence_score)

        # Calculate entry, stop loss, and take profit levels
        entry_price, stop_loss, take_profit = self._calculate_levels(
            direction=potential_direction,
            current_candle=current_candle,
            order_blocks=order_blocks,
            fvg_list=fvg_list,
            market_structure=market_structure
        )

        if entry_price is None or stop_loss is None or take_profit is None:
            return None

        # Create trading signal
        signal = TradingSignal(
            timestamp=datetime.now(timezone.utc),
            symbol=current_candle.symbol,
            signal_type=SignalType.BUY if potential_direction == "bullish" else SignalType.SELL,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strength=signal_strength,
            confluence_score=confluence_score,
            factors=factors,
            market_structure_state=market_structure.current_trend,
            metadata={
                "candle_time": current_candle.timestamp,
                "active_fvg_count": len(fvg_list),
                "active_ob_count": len(order_blocks),
                "liquidity_swept": any(pool.is_swept for pool in liquidity_pools)
            }
        )

        self.signal_history.append(signal)
        return signal

    def _determine_direction(self, market_structure: MarketStructure) -> Optional[str]:
        """Determine potential trade direction based on market structure."""
        if market_structure.current_trend == "bullish":
            # Look for buy opportunities on pullbacks
            if market_structure.last_bos_breakout and market_structure.last_bos_breakout > Decimal("0"):
                return "bullish"
        elif market_structure.current_trend == "bearish":
            # Look for sell opportunities on pullbacks
            if market_structure.last_bos_breakout and market_structure.last_bos_breakout < Decimal("0"):
                return "bearish"

        # Check for CHoCH (Change of Character) signals
        if market_structure.chhoch_detected:
            if market_structure.last_chhoch_point:
                last_point = market_structure.last_chhoch_point
                if last_point.point_type == "high" and last_point.break_confirmed:
                    return "bearish"  # CHoCH to bearish
                elif last_point.point_type == "low" and last_point.break_confirmed:
                    return "bullish"  # CHoCH to bullish

        return None

    def _calculate_confluence(
        self,
        current_candle: Candle,
        direction: str,
        market_structure: MarketStructure,
        fvg_list: List[FairValueGap],
        order_blocks: List[OrderBlock],
        liquidity_pools: List[LiquidityPool],
        premium_discount: Optional[PremiumDiscountZone]
    ) -> tuple[int, List[str]]:
        """Calculate confluence score and identify contributing factors."""
        score = 0
        factors = []

        current_price = current_candle.close

        # Factor 1: Market Structure Alignment (already determined, +1 base)
        score += 1
        factors.append("Market Structure Alignment")

        # Factor 2: FVG Confluence
        if self._check_fvg_confluence(current_price, direction, fvg_list):
            score += 1
            factors.append("FVG Confluence")

        # Factor 3: Order Block Confluence
        if self._check_order_block_confluence(current_price, direction, order_blocks):
            score += 1
            factors.append("Order Block Confluence")

        # Factor 4: Liquidity Sweep
        if self._check_liquidity_sweep(current_candle, direction, liquidity_pools):
            score += 1
            factors.append("Liquidity Sweep Detected")

        # Factor 5: Premium/Discount Zone Alignment
        if premium_discount and self._check_pd_zone_alignment(current_price, direction, premium_discount):
            score += 1
            factors.append("Premium/Discount Zone Alignment")

        return score, factors

    def _check_fvg_confluence(
        self,
        current_price: Decimal,
        direction: str,
        fvg_list: List[FairValueGap]
    ) -> bool:
        """Check if price is interacting with relevant FVG."""
        for fvg in fvg_list:
            if not fvg.is_active:
                continue

            if direction == "bullish":
                # For bullish trades, look for bullish FVG below or containing current price
                if fvg.bias == "bullish":
                    # Check if price is within or just above FVG
                    if fvg.low <= current_price <= fvg.high * Decimal("1.001"):
                        return True
            else:  # bearish
                # For bearish trades, look for bearish FVG above or containing current price
                if fvg.bias == "bearish":
                    # Check if price is within or just below FVG
                    if fvg.low * Decimal("0.999") <= current_price <= fvg.high:
                        return True

        return False

    def _check_order_block_confluence(
        self,
        current_price: Decimal,
        direction: str,
        order_blocks: List[OrderBlock]
    ) -> bool:
        """Check if price is interacting with relevant Order Block."""
        for ob in order_blocks:
            if not ob.is_active:
                continue

            if direction == "bullish":
                if ob.ob_type == "bullish":
                    # Check if price is at or near the OB high (entry zone)
                    tolerance = ob.high * Decimal("0.001")  # 0.1% tolerance
                    if abs(current_price - ob.high) <= tolerance:
                        return True
            else:  # bearish
                if ob.ob_type == "bearish":
                    # Check if price is at or near the OB low (entry zone)
                    tolerance = ob.low * Decimal("0.001")  # 0.1% tolerance
                    if abs(current_price - ob.low) <= tolerance:
                        return True

        return False

    def _check_liquidity_sweep(
        self,
        current_candle: Candle,
        direction: str,
        liquidity_pools: List[LiquidityPool]
    ) -> bool:
        """Check if recent liquidity sweep occurred in trade direction."""
        for pool in liquidity_pools:
            if not pool.is_swept:
                continue

            # Check if sweep happened recently (within last few candles logic would be here)
            # For now, check if the sweep aligns with our direction
            if direction == "bullish":
                # Bullish trade after sweeping lows (sell-side liquidity)
                if pool.pool_type == "low" and pool.swept_at:
                    return True
            else:  # bearish
                # Bearish trade after sweeping highs (buy-side liquidity)
                if pool.pool_type == "high" and pool.swept_at:
                    return True

        return False

    def _check_pd_zone_alignment(
        self,
        current_price: Decimal,
        direction: str,
        premium_discount: PremiumDiscountZone
    ) -> bool:
        """Check if price is in appropriate Premium/Discount zone."""
        if direction == "bullish":
            # Bullish trades should be in Discount zone
            if premium_discount.zone_type == "discount":
                if premium_discount.low <= current_price <= premium_discount.equilibrium:
                    return True
        else:  # bearish
            # Bearish trades should be in Premium zone
            if premium_discount.zone_type == "premium":
                if premium_discount.equilibrium <= current_price <= premium_discount.high:
                    return True

        return False

    def _determine_strength(self, confluence_score: int) -> SignalStrength:
        """Determine signal strength based on confluence score."""
        if confluence_score >= 5:
            return SignalStrength.VERY_HIGH
        elif confluence_score == 4:
            return SignalStrength.HIGH
        elif confluence_score == 3:
            return SignalStrength.MEDIUM
        else:
            return SignalStrength.LOW

    def _calculate_levels(
        self,
        direction: str,
        current_candle: Candle,
        order_blocks: List[OrderBlock],
        fvg_list: List[FairValueGap],
        market_structure: MarketStructure
    ) -> tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        """Calculate entry, stop loss, and take profit levels."""
        current_price = current_candle.close

        entry_price = current_price
        stop_loss = None
        take_profit = None

        if direction == "bullish":
            # Find best Order Block for entry refinement
            bullish_obs = [ob for ob in order_blocks if ob.ob_type == "bullish" and ob.is_active]
            if bullish_obs:
                # Use the highest OB high as entry zone upper bound
                best_ob = max(bullish_obs, key=lambda x: x.high)
                entry_price = best_ob.high

            # Stop loss below recent swing low or OB low
            if market_structure.last_swing_low:
                stop_loss = market_structure.last_swing_low * Decimal("0.998")  # Small buffer
            elif bullish_obs:
                lowest_ob = min(bullish_obs, key=lambda x: x.low)
                stop_loss = lowest_ob.low * Decimal("0.998")
            else:
                stop_loss = current_candle.low * Decimal("0.998")

            # Take profit at recent swing high or liquidity pool
            if market_structure.last_swing_high:
                take_profit = market_structure.last_swing_high * Decimal("1.002")
            else:
                # Target 2R minimum
                risk = entry_price - stop_loss
                take_profit = entry_price + (risk * Decimal("2"))

        else:  # bearish
            # Find best Order Block for entry refinement
            bearish_obs = [ob for ob in order_blocks if ob.ob_type == "bearish" and ob.is_active]
            if bearish_obs:
                # Use the lowest OB low as entry zone lower bound
                best_ob = min(bearish_obs, key=lambda x: x.low)
                entry_price = best_ob.low

            # Stop loss above recent swing high or OB high
            if market_structure.last_swing_high:
                stop_loss = market_structure.last_swing_high * Decimal("1.002")  # Small buffer
            elif bearish_obs:
                highest_ob = max(bearish_obs, key=lambda x: x.high)
                stop_loss = highest_ob.high * Decimal("1.002")
            else:
                stop_loss = current_candle.high * Decimal("1.002")

            # Take profit at recent swing low or liquidity pool
            if market_structure.last_swing_low:
                take_profit = market_structure.last_swing_low * Decimal("0.998")
            else:
                # Target 2R minimum
                risk = stop_loss - entry_price
                take_profit = entry_price - (risk * Decimal("2"))

        # Validate levels
        if stop_loss is None or take_profit is None:
            return None, None, None

        # Ensure positive risk
        if direction == "bullish" and stop_loss >= entry_price:
            return None, None, None
        if direction == "bearish" and stop_loss <= entry_price:
            return None, None, None

        return entry_price, stop_loss, take_profit

    def get_signal_history(self, limit: Optional[int] = None) -> List[TradingSignal]:
        """Get historical signals, optionally limited."""
        if limit is None:
            return self.signal_history.copy()
        return self.signal_history[-limit:]

    def clear_history(self):
        """Clear signal history."""
        self.signal_history.clear()