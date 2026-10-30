# Hermes Bilibili 0.1.0

原生 Hermes 插件，参考 dsh-bili-whale 的“人设、阅读经历、日记、反馈”闭环，重新以 Python 编写；不复制 DSH 宿主代码，不修改 Hermes 核心。

## 接力开发导航

- [HANDOFF.md](HANDOFF.md)：从这里开始，当前完成度、功能基线、本机目录、未验证事项与下一步。
- [ARCHITECTURE.md](ARCHITECTURE.md)：实际模块、状态机、统一派发、schema与权限边界。
- [DEVELOPMENT](docs/DEVELOPMENT.md)：新机器环境、隔离测试、宿主安装验收和开发约定。
- [OPERATIONS](docs/OPERATIONS.md)：凭据、暂停/中断、备份与远程浏览器安全运维。
- [ROADMAP](docs/ROADMAP.md)：优先级、已知限制及逐项验收标准。
- [AUDIT-INDEX](docs/AUDIT-INDEX.md)：历史报告/探针分类及未入Git证据说明。

这些说明供源码仓库接力使用；安装脚本只复制运行载荷，不会将整套开发文档安装进profile。

## 本版范围

- 8个模型工具：状态、视频搜索/元信息/评论/字幕、帧图、不可变草稿、发送、回读核查、分类型记忆、每日阅读周期。
- SQLite 事务保护审批、分享额度、去重、请求预算与暂停状态；默认所有外发关闭。
- 每条外发必须先由用户批准精确草稿；模型工具不能批准或修改配置。发送工具另外请求 Hermes 宿主审批，规则键按草稿 ID 隔离；是否再次弹窗仍受宿主 off/YOLO/已允许规则影响，不能承诺永远二次人工确认。
- 同一个草稿绑定账号、目标与全文，审批有时效。模型变更正文须重建草稿重新批准。发送不明或只被接口接受时不谎报成功、不自动重发。
- 硬上限：分享每日最多3条、同视频7天内不重复；请求预算只能调低，冷却只能调长。
- profile 隔离：运行数据位于当前 HERMES_HOME/plugin-data/hermes-bilibili；密钥使用 Hermes 的 secret scope。
- 角色背景按任务显式读取，不覆盖全局 SOUL.md、不修改系统提示、不把私密日记自动塞进无关会话。
- 安装不启动轮询、不创建cron、不登录、不发消息。没有偷偷常驻的线程或进程。

不是 DSH 功能逐项搬运：本版不含 B站私信自动监听、评论自动回复、直播 WebSocket、动态经营、自动点赞/投币/关注，也不提供原生 ASR。扫码登录未在本版实现。视频接口可能要求登录，字幕可能缺失；抽帧只是采样，不保证完整理解。

## 本地安装

要求 Hermes 原生 plugin API（本机验证版本 v0.22.5）、Python 3.11+；帧图可选安装 ffmpeg。Python运行依赖仅标准库。

使用 Hermes 当前 Python 在仓库运行本地安装脚本（拒绝覆盖、不自动启用、不改配置）：

    python scripts/install_local.py

或将发行文件放入当前 profile 的 plugins/hermes-bilibili 目录，然后执行：

    hermes plugins doctor /绝对路径/hermes-bilibili --ci
    hermes plugins validate /绝对路径/hermes-bilibili
    hermes plugins enable hermes-bilibili

不要覆盖已有同名目录。已启用插件须新开 Hermes 会话加载工具；当前会话不会动态重写工具列表。

插件提供 /bili 聊天命令。在插件宿主隔离模式下，Hermes目前不转发 register_cli_command；这种情况下用 /bili 或仓库自带本地 CLI，不要为了一个命令改变全局隔离模式。

## 安全配置

桌面端的 Settings → Plugins → hermes-bilibili 自动根据 config_schema 提供表单。

普通设置也可通过官方命令：

    hermes config set plugins.entries.hermes-bilibili.settings.owner_mid "你的UID"
    hermes config set plugins.entries.hermes-bilibili.settings.preferences '["AI工具", "音乐创作"]'

默认 writes_enabled=false。仅当你要发布时才由你开启：

    hermes config set plugins.entries.hermes-bilibili.settings.writes_enabled true

不要将 Cookie 粘到聊天或命令参数里。仅在 Hermes 的安全凭据表单填写：

    BILIBILI_SESSDATA
    BILIBILI_JCT
    BILIBILI_BUVID3

插件通过 agent.secret_scope.get_secret 读取，不读取其他工具的账号文件，也不会回显秘密。凭据由 Hermes 管理；本插件不保证系统密钥库加密。提供这些密钥会授予账号操作能力，请使用适合授权自动化的账号，遵守平台条款。速率限制不是免封承诺。

## 一次人工辅助阅读与分享

在新会话要求加载 hermes-bilibili:bilibili-workflow，然后让 Hermes 搜索和读取一个主题。

1. bili_status 检查本地状态。
2. bili_read 读取搜索、视频、评论、字幕；输出均标记 untrusted，并附实际检索产生的 evidence_id。
3. 需要时 bili_frames 返回本地拼图，再用宿主图片工具读图。
4. bili_memory 记录 public/private/feedback。private 不是加密，只是记忆分类。
5. bili_prepare 生成完整草稿。此时未发送。
6. 你查看精确内容、账号、目标和digest，自行输入：

       /bili draft 草稿ID
       /bili approve 草稿ID 完整DIGEST

7. 请求发送，Hermes 调用 bili_send 并请求宿主审批。宿主可能按其审批模式直接允许，但数据库精确草稿批准不可省略；只能发已批准且未过期的原草稿。无人值守任务只准准备草稿，不要设置审批绕过来自动发布。
8. 只看到 API 接受时是 unverified；只有回读匹配才 confirmed。unknown 不能重发。使用 bili_verify 继续核查，私信可能无法可靠自动核验送达。

拒绝草稿：

    /bili reject 草稿ID

## 中断恢复

运行中的阅读周期跨日仍会阻止下一轮，不能自动认定已经结束。你核查后可以显式中止，原因会保存且当天不会重跑：

    /bili cycle-abort 周期ID "已人工核查：进程中断，未完成"

账号验证码/登录问题由你处理后，可以仅解除人工暂停标记，平台要求的定时等待仍保留：

    /bili resume 账号UID

这些命令只属于用户入口，没有对应模型工具。CLI 中需要真实交互终端；具备任意本机代码执行权的进程不在本插件的隔离保证内。

## 人设和记忆

可由用户在当前 profile 的 plugin-data/hermes-bilibili/persona.md 写人设。建议依次写：身份、说话风格、在意什么、不做什么、什么值得分享。默认最多读取6000字符。

人设文件须为 UTF-8；读取人设与记忆失败会在领取周期前拒绝，不留下隐藏的 running 周期。max_candidates 是搜索实际结果数上限，默认5；设为0禁止候选搜索，不会发送搜索请求。

记忆保存 SQLite 中，可用 bili_memory list/context 读取。阅读周期回传背景与最近记忆，只对该任务使用。不要将未经确认的发送写成“已发”，不要为了人设编造真实浏览经历。

## 可选定时任务

没有默认定时任务。需要时，使用 Hermes 自己的 cron，附带命名空间 skill：

    hermes cron create "every 30m" "调用 bili_cycle start；只有 claimed=true 才按角色阅读；不发消息，只准备草稿；记录日记后用真实evidence_id complete。" --skill hermes-bilibili:bilibili-workflow --name "B站阅读"

这是带模型的周期检查：非到期也会发生一次模型调用；不是原项目的零token监听。本版每天最多领取一个周期。计划随机时间持久化，崩溃后 running 状态不会自动再跑，宁可人工核查而不重复执行。

网关/桌面的 cron ticker 必须运行。CLI 的本地结果只落地，不会主动回到这个终端；需要消息通知时应由你明确选已连接的投递平台。本次开发不会替你创建这个任务。

## 测试与安全边界

运行时不需要第三方 Python 包；清单一致性测试需要 test extra 中的 PyYAML。请在独立测试环境安装，不要修改 Hermes 管理的 Python 环境：

    python -m venv .venv
    .venv/bin/python -m pip install -e '.[test]'

完整测试还需要 Hermes 的 Python 导入环境（CLI/安装集成测试会启动子进程）。请使用 [DEVELOPMENT 的隔离命令](docs/DEVELOPMENT.md)，其中会显式设置临时 HOME/HERMES_HOME/USERPROFILE、当前项目 TEST_SITE 与子进程 PYTHONPATH；不要只运行一个未隔离的 unittest 简写。

验证摘要见 VERIFICATION.md；可移植的隔离命令见 docs/DEVELOPMENT.md。原始 TEST-RESULTS.txt、HOST-RESULTS.json 及各阶段JSON/txt日志仅保留本机、不入Git；历史引用缺失的原因与探针适用范围见 docs/AUDIT-INDEX.md。

发送前会重新检查审批有效期、暂停、预算、分享日期和请求间隔。旧请求因调度延迟与后续请求冲突时直接取消，不挤在同一时刻发送、不自动重试。收到 remote_id 后先持久化 unverified，再回读；回读期间中断仍可继续核查。若 POST 结果或落库失败导致 remote_id 未保存，保留不可重发状态，不能自动认定成功。

API、字幕和生产帧下载共用唯一请求预约 ID 与 SQLite 全局在途 guard，读取也在实际派发时复核时间、预算和暂停；冲突保守拒绝，不自动重试。历史字段 write_in_flight 表示任意请求在途（含读取），其他进程不能并行派发。进程被强杀时 guard 保留、不会按超时自动接管；本版没有自动清锁或模型清锁入口，/bili resume 只解除人工暂停，不解除在途锁；请勿为恢复运行直接删除数据库。

账号首次使用后预算时区固定；更换 timezone 不会重新解释历史额度，而会明确拒绝冲突配置。需要变更时区时先保留状态并设计操作员迁移，不要删除历史账本。身份指纹与账号绑定永久保存（不保存 Cookie），旧 identity 缓存即使过期也会迁移，避免因缓存过期失去暂停关联；账号漂移需人工核查。

状态库当前 schema version=1，支持本项目当前未版本化布局的增量迁移；缺必要列或未知版本在使用前拒绝且保留原库。旧批准草稿没有独立审批事件时不能自动补造批准，须重新建立并批准草稿；历史已发送/未决记录保留读取与核查。内容 SHA256 不是防拥有数据库写权限者的认证码。

媒体只接受受限的 MOV/MP4 解码路径，保留协议白名单；每个输出目录最多保留20张本插件拼图/50MB，自动淘汰最旧自有文件，不删除无关文件或跟随符号链接。此限制不是跨目录、跨并发任务的全局配额，也不是 OS 文件/CPU/内存沙箱。

HTTP 使用 read1 分块并在块间检查耗时，标准库响应存在底层 socket 时会在每次读取前将 timeout 缩至剩余预算；媒体调度等待耗尽期限时在 open 前拒绝，open 也只使用剩余预算。DNS/连接/TLS/响应头阶段没有可保证的硬总墙钟中断，替代响应对象无可调 socket 时仍仅为检查点期限；不承诺全流程绝对20/30秒。

字幕改用 `/x/player/wbi/v2`，播放信息改用 `/x/player/wbi/playurl`，两者通过 nav 获取密钥并复用搜索的 WBI 编码。字幕主接口必须返回结构正确的空 tracks 列表才尝试一次 `/x/v2/subtitle/web/view`；HTTP/API 风控、鉴权、非JSON或畸形结构直接失败，不降级绕过暂停。完整字幕 URL（含 auth_key）仅用于即时下载，不截断、不带 Cookie 访问 CDN、不缓存链接。nav、主接口、备用接口与 CDN 下载各自计入请求预算并在等待后复查暂停，因此比旧路径多耗读取额度。新增 `source` 字段说明字幕来源。签名端点与备用接口的真实服务兼容性（审计 CLIENT-06）仍未联网验收；本轮仅补代码契约和离线验证，不把 fixture 通过描述成线上可用。

测试使用明确的 fixture transport、临时 SQLite 和注入时钟；不是线上接口成功证据。Hermes Plugin Doctor 和 validate 只验证加载与注册，不证明所有B站端点长期可用。真实登录和外发需用户另行授权验收。

这是可信本机插件，不是系统级沙箱。拥有任意终端代码执行权限的同一OS用户可以改文件或数据库。审批机制不阻止一个已获任意本机代码权限的恶意执行者；网络提示注入、模型泄露私人日记等风险仍需宿主权限与用户判断配合。

## 来源

设计参考：https://github.com/Alterssss/dsh-bili-whale，审阅版本0.4.6 / 8b0009f8ce2ebdc421afcb2e727b19ac18912ec6。实现独立编写，没有引入其代码文件。Hermes API依据官方文档与本机安装源码核对。
