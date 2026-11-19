"""Tool-result faults and approver modes."""
from edat.faults.approver import DENIED_TEXT, TIMEOUT_TEXT, Approver
from edat.faults.layer import FaultyEnvironment, default_text

__all__ = ["Approver", "DENIED_TEXT", "FaultyEnvironment", "TIMEOUT_TEXT", "default_text"]
