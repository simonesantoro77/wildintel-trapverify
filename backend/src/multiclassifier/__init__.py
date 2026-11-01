"""Combine several classifiers (and a human reference) into one scored CamtrapDP.

Python port of the R workflow "flujo multi-classifier" (see docs/multiclassifier.md).
"""
from .config import ClassifierSource, PipelineConfig, Strategy, DEFAULT_STRATEGIES
from .pipeline import run

__all__ = ["ClassifierSource", "PipelineConfig", "Strategy", "DEFAULT_STRATEGIES", "run"]
