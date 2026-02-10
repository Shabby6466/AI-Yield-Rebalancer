"""
Mean Reversion Filter
Detects if a yield spike is temporary or sustainable.
"""
import numpy as np
import logging

logger = logging.getLogger(__name__)

class MeanReversionFilter:
    def __init__(self, spike_threshold: float = 2.0):
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
            
        history = np.array(historical_apys)
        mean = np.mean(history)
        std = np.std(history)
        
        if std == 0:
            return False
            
        z_score = (current_apy - mean) / std
        
        if z_score > self.spike_threshold:
            logger.info(f"Mean Reversion Triggered! Z-Score: {z_score:.2f}. Yield {current_apy:.2%} is likely a temporary spike.")
            return True
        
        return False
