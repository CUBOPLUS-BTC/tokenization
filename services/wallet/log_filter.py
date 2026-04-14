from __future__ import annotations

import logging
import re

class SensitiveDataFilter(logging.Filter):
    """
    A logging filter that redacts sensitive hex patterns from log messages.
    Focuses on potential HD seeds and private keys (hex strings >= 64 chars).
    """

    # Matches hex strings of 64 or more characters, typical of seeds and private keys.
    HEX_PATTERN = re.compile(r"\b[a-fA-F0-9]{64,}\b")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.HEX_PATTERN.sub("[REDACTED]", record.msg)
        
        # Also check arguments if they are strings
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(self.HEX_PATTERN.sub("[REDACTED]", arg))
                else:
                    new_args.append(arg)
            record.args = tuple(new_args)
            
        return True
