# 当前发布快照验证

## 最新状态

推送准备阶段重新执行：B站插件144项 unittest、远程浏览器启动策略2项 unittest，全部通过；git diff --check无错误。前述审计、修复和端点接线的限定复审结论分别见 audits/ 下的 Markdown 报告，不能将它们视为全仓或线上认证。

发布内容包含源码、测试、安装辅助、技能、设计与TDD记录、审计Markdown及可复现脚本；不包含原始JSON/文本运行日志、数据库、浏览器profile、凭据或截图。这些本机执行证据保留在原工作区，报告中的对应文件引用可能不在Git仓库中。

B站签名字幕/播放接口及安全备用路径仅完成离线验证；匿名主页和单个公开元信息接口曾实际可达，不等于已登录或其他端点均可用。远程浏览器已部署并实测画面/端口，但用户登录尚未完成；它与插件凭据配置相互独立。默认profile未启用B站插件，外发默认关闭，没有执行真实评论或私信发布。

以下内容是第一轮102项验收的历史记录，保留用于追溯，不代表当前测试数量或全部后续修复范围。

# 历史：继续开发验证

范围：Hermes Bilibili 0.1.0 的历史阻断修复与离线验收。不代表已通过真实 B站账号/网络端点验收；默认 profile 未安装或启用本插件，未创建定时任务。

## 修复与回归

- 写请求预约前检查草稿及审批有效期；预约发送时间不得超过有效期。
- 等待结束、调用 HTTP transport 前重新检查暂停、审批及写授权。
- 分享预算在领取、预约、实际调度前事务检查；有请求的操作按预约/实际调度 slot 计日期，不再仅依赖 claimed_at。
- 调度跨午夜时更新请求 slot 并重新检查写预算，已调度标识持久化，禁止重复调度。
- 独立审查发现旧请求延迟恢复可能与新请求同刻 POST：新增离线复现，先观察断言失败，再在事务内检查与其他请求的全局/写/私信间隔冲突，冲突直接取消、不自动重试；随后回归通过。
- 后续审查在 validate 提交到 transport 调用间发现抢占窗口，已新增 RED/GREEN：SQLite 持久化单写在途 guard 与调度标记同事务提交，覆盖网络调用及风险响应处理；其他写拒绝接管。完成后推进 slot 至处理完成时刻再释放 guard，冷却更保守。
- 进程被强杀遗留 guard 不自动过期，重启也停写；status 显示 write_in_flight=true。本版本没有自动/模型解除入口，须停掉原进程并人工核查；不可把 resume 当成解除在途锁。
- remote_id 在回读前落 unverified 已是既有修复；新增真实 SQLite 关闭重开与 BaseException 中断测试确认可以核查而不重发。另覆盖接受后数据库写失败、尚无 remote_id 进程退出、failed 不可被复活。

## 测试

项目 .venv 中安装 `.[test]`，PyYAML 仅为测试依赖；未修改 Hermes 管理的 Python 环境。

实际执行：

    PYTHONPATH="$PWD/.venv/lib/python3.14/site-packages:${PYTHONPATH:-}" python -m unittest discover -s tests -q

结果：102 tests，OK（包含真实本地 ffmpeg 处理测试；B站 HTTP 都是离线 fixture）。完整日志见 TEST-RESULTS.txt。测试中的非交互审批拒绝 JSON 是预期断言，不是平台访问失败。

## 宿主验证

Hermes v0.22.5 (2026.9.24)，Python 3.14.9。

隔离 HOME/HERMES_HOME：

    /home/admin/.hermes/cache/scratch/bili-release-host-dn_biwib

在该目录中安装、启用并回读；当前默认 profile 配置未改。

- Plugin Doctor --ci：成功，8 tools / 1 hook。
- plugins validate --json：ok=true，warnings=[]，host_ready=true。
- plugins list --user --json：hermes-bilibili status=enabled。
- 从隔离已安装副本执行 `python -m hermes_bilibili status`：success=true，writes_enabled=false。

原始记录：HOST-RESULTS.json；其中 commands 保存早期探针误读 CLI JSON 格式的一次失败，final_verification 保存修复探针后的全部成功结果，不覆盖历史失败证据。

## 审查与限制

针对 validate→transport 抢占窗口的独立复审通过（deleg_173897bc）：security_concerns=[]、logic_errors=[]，独立运行 dispatch 测试 9/9 通过。复审确认 reserve 和 validate 都拒绝并行写，guard 覆盖 transport 和风险响应处理，完成时事务推进时间后释放。非阻塞建议：补充“第二请求已预先预约，再撞上第一请求在途 guard”的直接 validate 回归。

该结论仅覆盖本轮发送竞态修复，不是全项目形式化安全证明。暂停检查到实际网络 I/O 之间不是跨网络的原子事务，已经进入网络的请求不能撤回。本插件不是同一 OS 用户下任意代码的安全沙箱。

未进行登录、发布评论/私信、真实搜索或二维码验收。扫码登录、事件监听、直播和动态经营仍按 README 延期。没有创建四小时后续跑任务；当前 cron 列表为空。
