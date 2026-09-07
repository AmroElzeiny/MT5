"""Autonomous Quant R&D Factory for the AmroElzeiny/MT5 repository."""
from .config import FactoryConfig, load_config
from .factory import ResearchFactory

__all__ = ["FactoryConfig", "load_config", "ResearchFactory"]
__version__ = "1.0.0"
