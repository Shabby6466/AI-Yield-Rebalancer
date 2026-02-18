"""
Mean Reversion Filter
Detects if a yield spike is temporary or sustainable.
"""
import numpy as np
import logging
import pandas as pd

logger = logging.getLogger(__name__)

class MeanReversionFilter:
    def __init__(self, window: int = 168, spike_threshold: float = 2.5, min_std: float = 0.0005):
        self.window = window # 7 days if hourly
        self.spike_threshold = spike_threshold
        self.min_std = min_std
        """
        Args:
            spike_threshold: Z-score threshold to consider an APY a "spike"
        """
        self.spike_threshold = spike_threshold

    def is_spike(self, current_apy: float, historical_apys: list) -> bool:
        """
        Check if current APY is a statistical outlier (spike) likely to revert.
        
        Args:
            current_apy: Live APY
            historical_apys: List of APYs from last 30 days
            
        Returns:
            True if it's a spike (IGNORE), False if it's a trend (ACT)
        """
        if not historical_apys:
            return False
        
        if len(historical_apys) < 10:  # Not enough data to be statistically significant
            return False
        
        
        if len(historical_apys) < (self.window / 4):
            return False
            
        # Use Pandas for easy Exponential Weighting
        series = pd.Series(historical_apys)
        ema = series.ewm(span=self.window).mean().iloc[-1]
        e_std = series.ewm(span=self.window).std().iloc[-1]
        
        # Apply the Volatility Floor
        effective_std = max(e_std, self.min_std)
        
        z_score = (current_apy - ema) / effective_std
        
        return abs(z_score) > self.spike_threshold
