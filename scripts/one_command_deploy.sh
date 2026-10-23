#!/usr/bin/env bash
# 已废弃 —— 这个脚本不再执行任何操作，只说明该用什么代替。
#
# 为什么废弃：它假定的是旧布局
#   · docker-compose.yml（而不是现在的 compose.yml）
#   · 容器名 shuake-easy-learning-{app,web,db}（而不是 uh-app-1 / uh-web-1 / uh-postgres-1）
#   · 从仓库目录运行（而服务器上只有 compose.yml + .env，没有 checkout）
# 三项都和现在的部署对不上，继续保留只会让人踩坑，所以改成硬失败。
#
# 正确的入口见下面打印的内容，或看仓库根目录的：
#   · 更新手册.md            —— 服务器日常更新 / 备份 / 回滚 / 排错
#   · 镜像上传部署教程.md     —— 服务器拉不动 GHCR 时，本机取镜像再上传
#   · 部署步骤-全新机器.md    —— 从零部署

cat >&2 <<'EOF'
✗ scripts/one_command_deploy.sh 已废弃，不做任何事。

  它假定 docker-compose.yml、shuake-easy-learning-* 容器名、以及一个完整的仓库
  checkout，这三样都和当前部署（/www/wwwroot/uh + compose.yml + uh-* 容器）不符。

请改用：

  1) 日常更新（服务器能拉 GHCR 时）
       cd /www/wwwroot/uh
       docker compose pull && docker compose up -d

  2) 服务器拉不动镜像（pull 卡住 / net/http: timeout awaiting response headers）
       见 镜像上传部署教程.md：本机 docker pull --platform linux/amd64
       → docker save | gzip → 宝塔上传 → 服务器 docker load
       → docker compose up -d --pull never

  3) 只备份数据库
       cd /www/wwwroot/uh
       STAMP=$(date +%Y%m%d-%H%M%S)
       docker exec uh-postgres-1 pg_dumpall -U easylearning | gzip > /www/backup/uh-db-$STAMP.sql.gz

  4) 看当前状态
       cd /www/wwwroot/uh
       docker compose ps
       curl -sS http://127.0.0.1:8080/health; echo

  5) 在服务器上从源码构建（有完整 checkout 时）
       bash scripts/deploy_server.sh --build --host <服务器IP> --admin-email <邮箱> -y
EOF

exit 1
