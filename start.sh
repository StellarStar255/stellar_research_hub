#!/bin/bash
# 启动论文研究台网页版：http://localhost:8767
# 回答用本机已登录的 Claude Code（claude 命令），不需要 API Key
cd "$(dirname "$0")"
(sleep 1.5 && open http://localhost:8767) &
exec python3 -m web.server
