"""Checked-in reference data the package reads at runtime.

amount_labels.toml holds the human verdicts on amount strings the parser refuses (#74). It
lives in the package rather than the repo root so it ships with the wheel and is readable via
importlib.resources, exactly as store/schema.sql is.
"""
