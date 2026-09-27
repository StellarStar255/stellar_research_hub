# -*- coding: utf-8 -*-
"""应用的本机数据目录，各模块统一从这里取，避免到处硬编码 ``~/.stellar_research_hub``。

环境变量 RESEARCH_HUB_HOME 可以覆盖（测试、或者想把论文库放到同步盘里）。
"""

import os

CONFIG_DIR = os.environ.get("RESEARCH_HUB_HOME") or os.path.join(
    os.path.expanduser("~"), ".stellar_research_hub")
LIBRARY_DIR = os.path.join(CONFIG_DIR, "library")
CRASH_LOG_PATH = os.path.join(CONFIG_DIR, "crash.log")
