# 开发、测试与接力验收

本文命令面向Linux/Bash服务端。源码要求Python3.11+，实际已验证Python3.14.9、Hermes0.22.5；没有据此宣称所有版本/Windows服务器均通过。Windows电脑只作SSH/noVNC客户端不受此限制。

## 开发约定

- 先读 [HANDOFF](../HANDOFF.md)与[架构](../ARCHITECTURE.md)，确认实际分支/工作树。旧TDD为历史证据，不能当永久待办。
- 生产代码变更先失败测试，再最小修复、全量回归；真实SQLite覆盖预算/guard/中断，不只断言FakeStore调用。
- fixture明确标synthetic，无真实账号网络；假sleep要推进注入clock。勿为让测试通过放松生产间隔。
- 保留现有staging；精确add。提交前检查secret、profile、日志，.gitignore不影响已tracked数据。
- 没有当前授权不启用默认profile、不登录/发消息、不创建cron、不改网络入口。
- 凭据、OTP、Cookie不进聊天/源码/log；通过宿主安全入口/vault。浏览器人工验证码由用户完成。
- 文档更新不顺手重构生产；实现变化同步HANDOFF、ROADMAP和VERIFICATION，报告实际运行/skip范围。

## 1. 克隆与解释器

    git clone https://github.com/Lar367/hermes-bilibili.git
    cd hermes-bilibili
    git status --short
    git log -1 --oneline

私有仓库需有权限的GitHub CLI/SSH/PAT；不要把token写入remote URL。账号网页密码不能直接用于git push。

先选择能够导入当前Hermes的Python（在Hermes工具终端通常是python）：

    HERMES_PY=$(python -c 'import sys; print(sys.executable)')
    "$HERMES_PY" -c 'import hermes_constants, hermes_cli.config, plugins.plugin_storage; print("host imports OK")'

若失败，用宿主实际解释器替代HERMES_PY；不要假设venv或/usr/bin/python包含Hermes，不要复制宿主私有配置来“修导入”。

    "$HERMES_PY" -m venv .venv
    .venv/bin/python -m pip install -e '.[test]'
    TEST_SITE=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')

PyYAML只进项目.venv，运行时仍标准库。完整测试由HERMES_PY运行，并将TEST_SITE传给其子进程；直接.venv/bin/python可能缺Hermes导入。ffmpeg/ffprobe支持实际本地媒体测试；Node用于跨语言编码向量。缺可选程序时注意skip，不能称相关能力已验证。

## 2. 隔离离线全量

在仓库根运行。SCRATCH必须是你允许创建临时文件的目录；在本Hermes环境优先沿用TMPDIR。保存原解释器及依赖路径后才替换HOME：

    SCRATCH=${TMPDIR:-"$HOME/.cache/hermes-bilibili-scratch"}
    SCRATCH=$(python -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$SCRATCH")
    mkdir -p "$SCRATCH"
    ISOLATED=$(mktemp -d "$SCRATCH/bili-handoff.XXXXXX")
    TEST_PATH="$TEST_SITE${PYTHONPATH:+:$PYTHONPATH}"
    env HOME="$ISOLATED" USERPROFILE="$ISOLATED" HERMES_HOME="$ISOLATED" TMPDIR="$SCRATCH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TEST_PATH" "$HERMES_PY" -m unittest discover -s tests -v
    env HOME="$ISOLATED" USERPROFILE="$ISOLATED" HERMES_HOME="$ISOLATED" TMPDIR="$SCRATCH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TEST_PATH" "$HERMES_PY" -m unittest discover -s remote_browser -p test_session.py -v
    git diff --check

当前基线预期插件144项、远程启动策略2项；结果以真实输出为准，不把两个suite混成144。全套B站HTTP为fixture；E2E是本地adapter/service/client/SQLite端到端，不是线上账号E2E。终端输出的审批拒绝JSON为负向测试，依据unittest最终结果判断。

这些测试约定离线，但不是独立进程的全局网络沙箱。可在隔离容器/firewall中加强；历史verify-offline.py仅在测试父进程禁DNS/socket，子进程不因此获得系统网络隔离。

## 3. 聚焦回归

保持相同隔离环境和TEST_PATH，可先执行更小suite：

    env HOME="$ISOLATED" USERPROFILE="$ISOLATED" HERMES_HOME="$ISOLATED" TMPDIR="$SCRATCH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TEST_PATH" "$HERMES_PY" -m unittest discover -s tests -p test_dispatch.py -v
    env HOME="$ISOLATED" USERPROFILE="$ISOLATED" HERMES_HOME="$ISOLATED" TMPDIR="$SCRATCH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TEST_PATH" "$HERMES_PY" -m unittest discover -s tests -p test_client_compat.py -v
    env HOME="$ISOLATED" USERPROFILE="$ISOLATED" HERMES_HOME="$ISOLATED" TMPDIR="$SCRATCH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TEST_PATH" "$HERMES_PY" -m unittest discover -s tests -p test_e2e.py -v

新增fixture不得吞掉真正要测的边界：例如多加nav后，CDN暂停注入必须移到CDN等待阶段，不能让更早签名失败误当CDN保护成功。

## 4. 独立离线探针

在上述临时环境变量下运行以下补充探针，预期使用当前实现通过：

    env HOME="$ISOLATED" USERPROFILE="$ISOLATED" HERMES_HOME="$ISOLATED" TMPDIR="$SCRATCH" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TEST_PATH" "$HERMES_PY" audits/remaining-fixes/compat-review-repro.py

compat-review-repro为5项/30子场景，含真实双连接SQLite。不要把其他审计脚本批量作为当前验收：本轮实跑发现replay-fixed.py的字幕fixture仍按旧无nav路径配置，当前会在“WBI signing unavailable”先失败，不能证明CDN暂停；这属于待更新的历史探针，不是本轮文档要求顺手改生产的依据。当前正式test_client_fixes和compat-review-repro覆盖新链路。其他旧探针可能期望漏洞存在，其失败或38秒无socket演示都须先判断适用版本。完整分类见[AUDIT-INDEX](AUDIT-INDEX.md)。

## 5. 隔离宿主安装

不要在默认profile直接试。保持 `HOME == HERMES_HOME == ISOLATED`，TMPDIR为SCRATCH；以下用子shell隔离导出，并采用当前目录计算绝对路径：

    (
      export HOME="$ISOLATED" USERPROFILE="$ISOLATED" HERMES_HOME="$ISOLATED" TMPDIR="$SCRATCH"
      export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TEST_PATH"
      export HERMES_BUNDLED_PLUGINS="$ISOLATED/no-bundled-plugins"
      "$HERMES_PY" scripts/install_local.py &&
      hermes plugins doctor "$HERMES_HOME/plugins/hermes-bilibili" --ci &&
      hermes plugins validate "$HERMES_HOME/plugins/hermes-bilibili" --json &&
      hermes plugins enable hermes-bilibili --no-allow-tool-override &&
      "$HERMES_PY" scripts/verify_host.py &&
      (cd "$HERMES_HOME/plugins/hermes-bilibili" && "$HERMES_PY" -m hermes_bilibili status)
    )

安装拒绝覆盖是正确行为，重复时换新临时HOME，不能删除真实状态绕过。公开plugins list看status='enabled'；工具/hook数量看doctor，不套用内部API字段。status应success=true且writes_enabled=false。完成后只清理自己创建的临时目录，先保留失败日志。

## 6. 历史验证脚本的迁移限制

`audits/remaining-fixes/verify-offline.py`是原机流程，不是通用runner：依赖Linux `.venv/bin/python`、Hermes CLI与当前宿主解释器、TMPDIR；写日志到audits，日志被Git忽略。其 `.venv` 必须来自当前仓库。`scripts/verify_host.py`主动拒绝非scratch HOME是安全设计，不应删除检查。

`remote_browser/verify_live.py`依赖额外websockets包和运行中的noVNC；它不是插件stdlib运行依赖。会请求实际画面并写截图，不在用户输入密码/验证码时运行；测试启动参数不需要启动真实浏览器。

## 7. 每次交付至少记录

- 基线/改动文件、实际命令、测试数量/skip、受影响能力。
- 新缺陷的最小失败复现、根因和RED→GREEN，不仅现有测试全绿。
- 若有独立复审：精确源哈希与范围，父开发者重跑阻断复现。
- 真实外部写后读回精确目标；push后核对commit/visibility，不能仅依赖命令exit0。
- 未完成/未验线上事项和下一步，不写“全仓安全认证”。
