# -*- coding: utf-8 -*-
"""Run the governed Data Agent MCP line-delimited stdio transport."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from mcp_stdio_server import McpStdioServer


def main():
    return McpStdioServer().serve()


if __name__ == '__main__':
    main()
