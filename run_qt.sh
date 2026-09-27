#!/bin/bash
# 启动 Research Hub
cd "$(dirname "$0")"
python3 stellar_research_hub.py "$@"
