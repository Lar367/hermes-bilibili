# Hermes Bilibili 与 dsh-bili-whale：重新审计及对比报告

审计日期：2026-10-29（Asia/Shanghai）
本地项目：/home/admin/projects/hermes-bilibili，版本标记 0.1.0。
对照项目：Alterssss/dsh-bili-whale，0.4.6，提交 8b0009f8ce2ebdc421afcb2e727b19ac18912ec6。

## 一、结论先行

当前 Hermes 版应定位为“安全优先的 B站阅读、角色记忆与逐条人工批准发布原型”，而不是“功能更完整的 dsh-bili-whale 移植版”。上游的自主生活闭环、事件监听、选题和反馈机制明显更完整；本地在精确审批、事务状态、输出确认语义、HTTP 限界与按 profile 隔离方面有明确加强，但基础读取和账号身份保护仍有缺口。[3][4][16]

本轮重新运行本地完整测试 102 项全部通过；上游选择审阅过的 planner、traffic、ai-share、dm-history 四组，共 43 项通过。[10][11]
二者测试范围和断言不同，数量不能作为优劣分数。新探针又复现了既有测试没有覆盖的问题，因此“测试全通过”不能等同“可直接无人值守上线”。[13][14][15]

本次不是修复任务：未修改生产源码、原有测试、真实配置或账号。审计前固定的本地 26 个文件哈希，以及对比报告引用的 43 份源文件哈希，复核均未改变。本地仓库尚无 HEAD 提交，报告以 baseline.json 的文件指纹标识实际审计版本，而不是虚构一个提交号。[2][16]

建议验收判定：本轮报告完成；当前实现不建议直接进入真实账号无人值守外发。先补统一读取派发、稳定身份暂停、WBI 编码与恢复/可观测性，再进行明确授权的线上只读验收，最后才做逐条批准的外发验收。这是基于下述证据的工程判断，不是平台封号风险保证。

## 二、审计方法与证据边界

1. 从入口、参数、配置、存储、调度、审批、HTTP、回读、媒体处理及故障恢复重新追踪，不把上轮局部复审通过视为全仓安全通过。
2. 三个独立分支分别审 core/service、client/media、上游功能/宿主/流程；父审计负责 Hermes 集成、周期、宿主审批契约和交叉核验。
3. 本地完整 unittest 重新执行；上游仅执行已审阅、可隔离的四组测试，没有运行完整 DSH 桌面链路或扫码测试。
4. 父审计重新执行 core-repros.py、client-repros.py、parent-repros.py，全部命令退出 0。这里退出 0 表示成功观察到报告所述行为，不表示实现没有缺陷。core 探针还真实启动独立 OS 进程检查领取竞争和异常退出。
5. 临时 HOME、HERMES_HOME 和 SQLite 放在 scratch；B站请求均为明确 fixture。未登录、未读取真实 Cookie、未访问真实 B站端点、未发评论或私信。[13][14][15]
6. 本轮重新执行隔离 Plugin Doctor：注册成功，8 个工具、1 个 hook。它只证明宿主加载/注册契约，不证明搜索、字幕、媒体、发布端点线上可用。原始输出见 host-doctor.txt。
7. 上游远端 HEAD 在本轮开头以 git ls-remote 核对，与固定提交相同。所有上游判断限定此提交，不泛指同名分支、其他衍生项目或未来版本。[1][2]

严重性口径：优先修复表示会影响日常正确性、风控承诺或可恢复性；并不自动意味着可远程盗号。需要管理员改时区、直接修改数据库、直接调用底层 API 的场景均明确标注前提。

## 三、功能：实际上实现了什么

### 3.1 Hermes 版目前提供的能力

模型工具实际为八个入口，不是八个完整业务系统：[12]

- bili_status：本地额度、暂停、草稿/操作状态、write_in_flight；不等于在线账号与依赖体检。
- bili_read：关键词搜索、视频元信息、首个热评页、字幕；没有多源 feed、最新评论分页或通用楼中楼读取。
- bili_frames：受限下载并生成 3×3 本地 PNG 拼图；最长 600 秒、源文件最多 20MB、解码尺寸有限；不是完整视频/音频理解。
- bili_memory：public/private/feedback 分类记录与读取；没有自动反馈学习器。
- bili_cycle：持久化每日计划、单次领取、完成和检索证据关联；没有常驻监听进程。
- bili_prepare：生成绑定账号、动作、目标、全文、理由、分数的不可变草稿。
- bili_send：按批准草稿发送视频顶层评论，或给配置 owner 发普通私信。
- bili_verify：读取操作结果；评论检查匹配证据，私信可靠送达核查未实现。[4][5][8]

人设只显式进入当前 B站任务，默认最多读取 6000 字符，合并最近 12 条记忆，不修改全局 SOUL.md。private 是用途分类，不是加密或独立访问控制；模型防泄漏仍依赖宿主权限和任务约束。[5]

### 3.2 主要能力对比

| 维度 | dsh-bili-whale 0.4.6 | Hermes Bilibili 0.1.0 |
|---|---|---|
| 自主触发 | 常驻普通程序定时器；多窗口、多次唤醒 | 人工或用户选择的宿主 cron；每日最多一轮 |
| 无消息时模型成本 | 监听用普通程序，命中才唤醒模型 | 尚无事件监听；带模型 cron 检查可能也消耗调用 |
| 选题 | 搜索、热门、排行、关注；去重与规则排序 | 关键词搜索，模型自行选择 |
| 深读 | debrief 聚合封面、简介、字幕、弹幕热区、热评、画面 | 分散 read/frames；缺弹幕热区、字幕对齐与统一深读 |
| 视频抽帧 | 可选网格、场景检测、较大格图、字幕条 | 固定小格 3×3，边界更窄、能力更少 |
| 人设连续性 | 专用会话、人设注入、轮换/去重、双 Markdown 日记 | 当前任务上下文和分类 SQLite 记忆 |
| 反馈闭环 | 回复、收到的赞、私信、手工反馈参与规则回流 | 只有 feedback 存储，缺自动回流算法 |
| 沉默衰减 | 分享额度和提示随反馈沉默变化 | 未实现 |
| AI 评论分享闸 | 可选样本/分类/比例策略 | 未实现 |
| 互动监听 | 评论/@、主人私信、直播 | 未实现 |
| 写操作 | 评论/回复、私信、收藏、动态、图文、弹幕、删除等 | 视频顶层评论和 owner 普通私信 |
| 审批 | 待审队列与提示流程，底层可直接 --go | 每条草稿精确摘要批准，数据库强制 |
| 结果确认 | code=0 即接受；回读通常另行执行 | accepted/unverified/unknown/confirmed 分开 |
| 登录和 UI | 扫码、手工导入、专用设置页 | 宿主凭据机制和标准配置；无扫码与专用审核 UI |
| 存储 | JSON/Markdown；多种账本按 OS 用户共享 | 按 Hermes profile 的 SQLite/文件 |

依据及每项源文件行号见附录完整 37 项矩阵；这里是产品差异，不是以“删除功能”冒充“同功能更安全”。[1][16]

特别区分：Hermes 私信强制 share=false，不参与视频分享评分、七天 BV 去重和分享日额度；它目前是逐条批准的 owner 聊天通道，并不等价于上游 dm --share。Hermes 评论也不自动插入 @owner，不能宣称推荐必然触达用户。[3][4][16]

两者都没有本项目内置的独立推理模型或 ASR；上游 likes 是读取“收到的赞”，不是自动点赞；不要把收藏、投币、关注、投稿混称为“三连自动化”。[1][16]

## 四、架构

### 4.1 dsh-bili-whale

    DSH / Cordis 插件入口 + React 设置页
        ↓ 同源配置、登录、状态、待审 API
    常驻 scheduler
        ├─ 生物钟
        ├─ 评论/@轮询
        ├─ 私信 peek/ack
        └─ 直播 await 子进程
        ↓
    session-delivery 统一串行投递
        ↓ 注入人设、恢复提示、选择/轮换专用会话
    DSH Agent / 宿主模型
        ↓ 终端命令与读图
    bili.mjs → Node 业务脚本
        ↓ 共享 traffic 保护、缓存、业务 JSON 账本
    B站接口 / CDN

上游的核心价值是“把角色、经历、反馈和行动串进持续运行的生活循环”，不是 persona.md 一张卡。其 JSON 请求保护层已经有跨进程锁、请求预算、活跃请求保护、读取合并和未知发送防重，不应误写成上游完全没有并发防护；主要薄弱点是业务分享/回复/收藏账本与审批契约未同等事务化。[1][16]

存储分为 DSH_HOME 的计划、会话、日记、日志与素材，以及 HOME/.bili-ai 的凭据和多种业务账本；后者不随 DSH_HOME 隔离。[1][16]

### 4.2 Hermes 版

    Hermes 原生插件 register / plugin.yaml / 命名空间 skill
        ├─ 模型 bili_* 工具
        ├─ 用户 /bili 命令
        └─ 可选宿主 cron
        ↓
    integration 参数校验 + 当前 profile 配置/secret scope
        ↓ 每次调用创建、结束关闭
    Service 工作流
        ├─ Store：state.db
        │    草稿、批准、操作、预算、暂停、缓存、记忆、写在途 guard
        ├─ CyclePlanner：cycle.db
        │    每日计划、run_id、状态、evidence_id
        └─ BiliClient / Media
             HTTPS 限界、签名、账号、只读、受限写、回读、抽帧
        ↓
    B站接口 / 公开 CDN / 本地 ffmpeg

Hermes 版把“工具/模型能要求什么”和“什么状态下程序允许执行”分开。SQLite BEGIN IMMEDIATE 对领取和预算做串行事务；写在途 guard 跨越派发与响应处理窗口。它不把锁持在网络事务里，而是持久记录在途所有权；强杀会保留 guard，不自动超时接管。[3][4][12]

这种设计的代价是恢复不完整：当前没有受支持的用户清理遗留写 guard 命令。只有 /bili resume 不能解除它；停写是安全取舍，但不能称为已经完善的故障自愈系统。[3][13]

双数据库还意味着周期完成和日记副本不是一个原子事务；适配器已经将“周期完成但日记副本失败”作为警告返回，这是正确区分，但尚非统一事务日志或完整事件存储。[5][6]

## 五、一次操作的真实流程

### 5.1 上游：自主醒来并分享

    普通定时器检查持久计划/窗口/忙碌状态
        → 统一会话投递人设与任务提示
        → 模型按提示体检、读日记
        → feed 多源选题
        → 文字初筛，再少量 debrief/读图深读
        → 模型评分和决定是否分享
        → 可选 AI 评论样本策略
        → comment/dm --share 业务检查
        → traffic 预算、冷却、暂停、防重
        → POST 接受、记账、通知
        → 模型另行回读、写双日记、处理反馈

重要边界：调度器通常将“prompt 投递成功”记作本轮计划已执行，不能据此证明视频已读完或日记已写成。先 dry 后 go、第三方必须待审、写后复核等仍有模型提示约束，不都由 CLI 凭证强制。评分和排名也由模型自报。[1][16]

### 5.2 Hermes：阅读周期

    用户/宿主 cron 调用 bili_cycle start
        → SQLite 创建/读取每日计划
        → 未到期、已有 running 或已完成：claimed=false
        → 领取成功：返回 run_id、人设、记忆、偏好、指引
        → 模型调用 read / frames
        → 程序生成 evidence_id，记录实际检索及所属周期
        → 模型形成总结、写分类记忆、按需准备草稿
        → complete 检查证据来源/轮次/有效期
        → 保存 complete，再写 public 日记副本

“存在本轮检索证据”只证明发生过检索，不证明模型认真理解、结论正确、一定深读两三条、一定写 private 日记。当前至少一个合法证据即可完成非 skip 周期；候选上限、排第一、排除不喜欢内容等仍有提示或实现缺口。[5][6][16]

### 5.3 Hermes：批准并发送

    prepare：查账号 → 保存精确草稿及 digest
        → 用户查看内容、目标、账号、摘要、期限
        → /bili approve ID DIGEST
        → bili_send 请求进入宿主审批机制
        → claim：禁写/账号/期限/评分/分享额度/去重检查
        → 再查真实账号及对应业务条件
        → 请求预约、等待
        → 派发前复查，原子标记在途 guard
        → POST 与风险响应处理
        → 从完成时刻保守更新冷却并释放 guard
        → 明确拒绝：failed
          歧义：unknown，不自动重发
          接受且有 remote_id：先保存 unverified
        → 评论回读匹配才 confirmed；DM 可能一直 unverified

草稿通常 30 分钟有效，批准最多 10 分钟且不超过草稿期限。精确批准是本地数据库强约束。[3]
宿主 hook 是额外的审批请求，不是永远强制再点一次：宿主审批 off、YOLO、已允许规则等会影响它。不可将后一层描述成不受宿主设置影响的第二次人工同意。[9][15]

## 六、本地重新审计发现

本节记录 12 项已观察行为：核心/客户端 9 项，加父审计 3 项；其中包括缺陷、配置语义问题和文档/信任边界限制，不能一概算成 12 个可远程利用漏洞。还有 4 项待验证事项单列。审计没有自动修复它们。[13][14][15]

### A. 优先修复：正常或合理边界可触达

1. CORE-001：读取派发缺少实际时刻重核算。
   预约后的 GET 延迟恢复时，两次实际请求可同刻发生；read_daily=1 的午夜实验，同一实际日发出两个 GET，状态只报一个。写请求已补的防护没有完整覆盖读取。
   位置：core.py:349–445；client.py:146–163。
   建议：读写都使用唯一请求预约 ID，统一派发时间、额度与间隔边界；字幕与媒体下载也纳入明确策略。[3][4][13]

2. CLIENT-01：24 小时身份缓存过期削弱持久暂停。
   同一 fixture 凭据对应账号仍人工暂停；缓存未过期时零请求，过期重建客户端后以 anonymous 预算发出一次带凭据 nav。
   位置：client.py:139–160、384–390；core.py:480–488。
   本报告将其列为中高优先级的保护缺口，但未证明未经批准的 POST、盗号或远程提权。建议凭据指纹到身份/暂停的绑定独立于普通 TTL 缓存。[4][14]

3. CLIENT-04：字幕分支等待后未复查暂停。
   在字幕等待中设置暂停，仍派发字幕 CDN GET；该请求不含 Cookie，因此不是凭据泄漏。
   位置：client.py:255–258。建议所有出网都经过同一派发契约。[4][14]

4. CLIENT-05：WBI 空格编码不一致。
   相同密钥、固定时间和 keyword='hello world'，本地 Python urlencode 使用 '+'，上游实际 Node 签名使用 '%20'，w_rid 不同。既有纯数字向量未覆盖。
   位置：client.py:117–125、156、195–196；上游 tools/lib/bili-api.mjs:10–17。
   这是已复现算法差异，未在线证明服务端一定拒绝。建议统一 RFC3986 查询序列化并加入空格、中文、加号等跨语言向量。[4][14]

5. H-P02：周期已领取后上下文失败，返回错误但隐藏已领取状态。
   非 UTF-8 persona 令 role_context 失败；工具返回 success=false，但 cycle.db 已是 running，响应无 run_id。后续会被阻止，虽可从 status 找回并人工 abort，却属于部分成功未清楚报告。
   位置：integration.py:128–134、152–155、161–170；cycle.py:88–91。
   建议在领取前验证上下文，或在出错响应中明确返回 run_id、持久状态与恢复方法。[5][6][15]

### B. 条件性问题与低层契约

6. CORE-002：时区热变更重新解释历史预算。
   同一原 UTC 日先完成三次批准分享，改成 Asia/Shanghai 后又完成三次；新时区状态只报三次。需要合法配置权限，不是模型自行绕过。
   位置：core.py:390–442、557–589。
   应明确日历切换语义；建议同账号预算日历固定或在旧窗口结束前同时满足新旧限制。[3][13]

7. CLIENT-02：nav 发现新账号后未复查新账号暂停。
   同一客户端先返回 9，再返回已暂停的 8，account 仍成功；预算归 anonymous、9。生产同一 SESSDATA 是否自行换账号未验证；发送路径仍比较批准账号。
   位置：client.py:147–160、384–391。建议身份变化拒绝或人工确认，重核新账号保护状态。[4][14]

8. CLIENT-03：底层 verify 未绑定持久 remote_id。
   数据库存 91，直接传 92，fixture 的 92 同作者/目标/文本仍被判 verified。现有公开工具只接 operation_id，Service 从库读操作，未发现公开模型工具可直接注入 remote_id；因此是低层 API 硬化问题，不是已证实公开越权确认。
   位置：client.py:354–374；service.py:25–42。建议校验持久 remote_id、digest 和允许状态。[4][8][14]

9. H-P01：max_candidates 是无效执行配置。
   配为 1，显式 search limit=20 仍返回 20 条；配置仅被定义/校验，没有参与实际搜索上限。
   位置：core.py:41–54；integration.py:31–32、84–85、128–134。
   建议执行入口取更严格上限，或删除/重新定义该配置，不把“最多五候选”当程序保证。[3][5][15]

10. H-P03：额外宿主人工确认承诺需收窄。
    approvals.mode=off 的真实宿主函数调用返回 approved=true；插件 hook 并未提供不受宿主模式/allowlist 影响的二次同意。草稿精确数据库审批仍在。
    位置：根 __init__.py:59–65；宿主 tools/approval.py:968–994、1121–1145。
    建议文档写“请求宿主审批”；若产品要求每次人工再确认，需要不可缓存、精确绑定草稿且无人值守拒绝的契约。[9][12][15]

### C. 本地状态健壮性，不作为远程攻击结论

11. CORE-003：digest 是内容摘要，不是生命周期来源认证。
    直接修改本地 SQLite 的 state 和 approval_expires_at，可使未经过 approve_draft 的草稿被 claim；digest 不变。该实验需要数据库写权限，属于 README 排除的同 OS 用户任意执行权，不能说成远程审批绕过。
    位置：core.py:267–297、325–337、576–592。若要防误迁移/语义损坏，应另存审批事件并交叉校验；不要把 SHA256 说成防同用户恶意改库。[3][13]

12. CORE-004：非 JSON 列损坏未统一封装。
    cache.expires_at='tomorrow' 时 cache_get 抛 TypeError，而不是 PolicyError。外层适配器仍捕获，不证明泄密；属于错误模型/健壮性不一致。
    位置：core.py:149–166、480–492。建议对 SQLite 类型、有限值、时间范围做读取时校验。[3][13]

### D. 四项待验证/兼容性风险

- CORE-005：合成缺列旧 schema 初始化可成功、首次预约才报错；未找到已发布旧版证据，不应称为已确认生产升级回归。应定义 schema 版本与迁移。
- CLIENT-06：字幕/取流沿用未签名旧路径，上游已有 WBI 主路径和字幕兜底；未联网，不能说本地端点必然不可用。
- CLIENT-07：urllib 单 socket 超时不等于总墙钟 deadline；PNG 无聚合清理、子进程资源边界待加强。未做慢服务/耗尽测试，不能声称已复现 DoS。
- CLIENT-08：file 协议白名单不是文件系统沙箱；本轮 HLS 本地引用探针被当前 ffprobe 拒绝，明确未复现任意本地文件读取。[13][14]

## 七、上游问题：不能照搬的部分

上游详细报告列出 20 项缺陷、策略边界或文档漂移，并分别标明静态/离线探针证据。这里列最影响方案选择的部分；不是说 20 项全是已运行证实的安全漏洞。[16]

- 默认值分叉：宿主评论开、DM 关、once 关；工具链默认相反，新安装与首次保存行为可能不同。
- 每日最多三条不是底层硬封顶：设置接口可提高到 20；风险预算也可放大、DM 冷却可为 0。
- 第三方待审主要是工作流要求：底层评论命令可直 --go，没有精确人类审批凭证。
- 业务分享/收藏/回复账本检查、发送、记账分离；请求层锁不等于业务额度事务，存在静态并发窗口。
- 12051、10031 没进入集中暂停数组，和 UI 的“遇到就停”说法不一致。
- 待审组件定义了却未挂载；先 take 删除再发送，故障窗口可能丢草稿，动态回复还存在坐标传递不全。
- “只回主人”直播配置并非排他过滤：任何人 @自己昵称可先命中；宿主默认启动的是 gethistory 轮询，不是默认实时 WebSocket。
- legacy night 配置可覆盖统一设置；人设 3000 字说明与实际 20000 字截断不一致。
- AI 评论样本不足 20（含 0）按策略放行；这是策略选择，不是独立可靠检测器，也不是免封机制。
- 成功投递 prompt 不等于完成阅读；API code=0 也不等于评论可见、@送达或私信已读。[1][16]

上述具体源文件行号、复现实验或静态风险标记见附录 U01–U20。

## 八、相对上游真正改善的点

1. 精确审批从模型约定变为数据库状态机：账号、目标、全文、理由和分数绑定；变内容要新草稿，批准有时效，模型工具无 approve/resume/config。[3][5][8]
2. 请求保护和业务额度都有事务基础：单次领取、业务保留、unknown 不释放成可重试、独立进程竞争只有一次成功；不是只在提示里写“别重复”。[3][13]
3. 确认语义更诚实：先保存 remote_id/unverified 再回读，过程终止可保留核查入口；DM 不夸称送达。仍须修底层 remote_id 绑定缺口。[4][8][14]
4. 按宿主 profile 解析数据与凭据来源，安装不自动创建后台活动，减小意外启用面；依赖宿主正确绑定 secret scope，不是插件自建密钥库。[5][12]
5. 显式限制 HTTP 主机、重定向、Cookie 目的地、响应大小；媒体限制单文件/时长/尺寸并清理原片。更窄边界不等于更丰富理解，也不等于 OS 沙箱。[4][7]
6. 周期完成关联真实检索证据，而不是把 prompt 成功投递等同做完；不过证据仍只证明检索发生，不证明推理正确。[5][6]
7. 默认外发关闭，配置预算不能提高超过代码上限。时区切换和读取派发的问题说明这些保证还需要补齐，不能省略例外。[3][13]

## 九、下一步改善路线与验收标准

优先级 A：统一安全与正确性底座。

- 读、写、字幕、媒体都使用明确预约对象、实际派发核验与一致暂停语义；测试延迟乱序、跨午夜、读写交错、身份缓存过期。
- 稳定凭据指纹/账号绑定与暂停，检测身份漂移；明确同账号跨 profile 是否共享平台额度，不能默认以 profile 隔离就解决同账号总请求量。
- 修签名/请求序列化一致性，补 Unicode/空格/特殊字符向量；对所有支持端点做用户授权的只读兼容性验收。
- 绑定回读 remote_id、状态与摘要；修部分成功响应、无效配置与 SQLite 字段校验。
- 增加带审计记录的操作员恢复：确认原进程已停止、只核查不重发、可查看/处理遗留 guard，不能通过删除数据库恢复。

优先级 B：把“阅读工具”补成“可靠阅读工作流”。

- 增加多源 feed、已看记录和可测试的负偏好/多样性；候选数量真正执行。
- 聚合 debrief，明确每条结论依赖的字幕/评论/画面及缺失材料；不要把抽帧当完整观看。
- 完成事件、日记、反馈之间的一致关联；形成真实的偏好回流和沉默减扰，而不只多存一条 feedback。
- 按需求补只读事件监听，先明确最小权限、消息 receipt/ack、游标、积压、重复投递和撤回处理，再考虑自动回复。

优先级 C：补产品和运维。

- 扫码登录、依赖/账号体检、可见待审队列、周期/保护状态、通知投递和错误诊断。
- 单一配置 schema/defaults，生成帮助/表单/文档，避免复制上游的漂移。
- schema 版本迁移、缓存/frames 保留策略、总资源限额、可分发发布包和版本提交。
- 只在明确需要时逐项扩展收藏、楼中楼、动态、直播；每项沿用精确批准/预算/回读，不恢复“默认全权限＋提示自觉”。

上线前最低验收：新增缺陷回归通过；真实账号只读验收；一条可控、精确批准的写入与回读；超时/断网/进程强杀/恢复演练；确认无人值守模式不自行获得发布权限。未做到这些前，应继续标记实验性，不以测试数量替代验收。

## 十、交付与可复核资料

本报告目录：/home/admin/projects/hermes-bilibili/audits/20261029-080759

- baseline.json：本地文件指纹与版本基线。
- local-tests.txt：本轮 102 项完整测试输出。
- upstream-tests.txt：四组上游 43 项测试输出。
- host-doctor.txt：本轮隔离宿主注册验证。
- core-findings.json / core-repros.py / core-repro-output.txt：核心发现、脚本、父审计重跑结果。
- client-findings.json / client-repros.py / client-repro-output.txt：网络/媒体发现与重跑结果。
- parent-findings.json / parent-repros.py / parent-repro-output.txt：集成、周期与宿主审批探针。
- comparison-findings.json：37 项能力矩阵、38 个上游 CLI 入口、20 项上游问题及源文件引用。

下列附录由 JSON 产物自动整理，计数去重由程序完成。Sources 块由引用账本生成。

## 附录 A：完整能力矩阵（37 项）

### A01 宿主及入口
上游：完整 DSH Cordis 宿主插件 + React 设置 section + Node CLI 工具链
本地：完整 Hermes 原生 register：8工具、pre_tool_call、/bili、CLI、命名空间 skill
程序约束差异：上游 inject sessionController/webServer/connection，缺服务不启动；本地无自建HTTP路由和常驻调度
模型/提示边界：两者都由宿主LLM理解素材；并非插件内置独立AI模型
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:152-158；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/client.js:674-695；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/cordis.patch.yml:16-18；/home/admin/projects/hermes-bilibili/__init__.py:4-67。[16]

### A02 设置界面
上游：完整专用设置页：身份/偏好/窗口/分享/监听/推送/凭据/体检
本地：标准 config_schema 表单；没有上游专用设置、待审队列UI或在线体检UI
程序约束差异：上游多套默认值；本地 Settings 做类型及数值硬边界
模型/提示边界：宿主自动渲染表单是宿主能力，不是本地自写 React
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/client.js:58-83；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/client.js:623-671；/home/admin/projects/hermes-bilibili/plugin.yaml:16-38；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:51-86。[16]

### A03 扫码/手工登录
上游：扫码挑战、轮询、取消、过期、验证后原子写凭据；手工Cookie后备
本地：缺扫码；只从 Hermes secret scope 获取3项凭据
程序约束差异：上游验证登录后保护0600/Windows ACL；有待审草稿禁止换号；本地不导入上游凭据文件
模型/提示边界：两者都不应让模型在聊天接收Cookie；本地该禁令是skill和README规则
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/auth.js:39-142；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/credentials.js:28-94；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:56-72；/home/admin/projects/hermes-bilibili/README.md:49-55。[16]

### A04 状态/凭据隔离
上游：JSON/JSONL/Markdown，DSH_HOME存日记计划；HOME/.bili-ai存凭据与许多账本
本地：profile-scoped state.db/cycle.db/persona/frames；密钥由宿主管理
程序约束差异：上游凭据和quota跨DSH_HOME共享；本地SQLite事务、权限和符号链接检查
模型/提示边界：private名称或分类都不等于加密
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/paths.mjs:21-37；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:23-38；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:58-69；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:96-166。[16]

### A05 关键词搜索
上游：WBI签名搜索，独立search及feed
本地：支持 bili_read search，首个结果页最多20
程序约束差异：本地搜索参数及返回字段限界；上游还有选题池
模型/提示边界：语义相关性由模型判断
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:22-38；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/feed.mjs:90-129；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:186-203。[16]

### A06 多来源选题器
上游：搜索+热门+排行+关注视频；历史/已赞/收藏去重、密度/偏好/反馈主题词加权、多样性
本地：缺feed聚合；仅搜索，模型按偏好自行选题
程序约束差异：上游确定性排序过滤；本地无历史浏览过滤器和反馈学习排序
模型/提示边界：本地skill要求排除dislikes，非语义硬过滤；上游dislike变量同样未使用
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/feed.mjs:35-53；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/feed.mjs:133-168；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/feed.mjs:171-284；/home/admin/projects/hermes-bilibili/skills/bilibili-workflow/SKILL.md:24-26；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:128-134。[16]

### A07 视频元信息
上游：view/WBI工具及recon/debrief
本地：支持标题、简介、作者、统计、多P摘要；video用未签名view接口
程序约束差异：本地有ID匹配和字段限长缓存；未搬运上游所有WBI视频端点
模型/提示边界：元数据不是已经看过视频的证明
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/debrief.mjs:81-89；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:21-25；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:205-226。[16]

### A08 热评/最新/楼中楼
上游：comments hot/time分页、thread完整楼中楼、check-comment等
本地：简化：comments仅首个热评页；verify针对己方操作回读
程序约束差异：本地工具不暴露pn/sort/root/parent；无法通用导出评论线程
模型/提示边界：本地模型不能用不存在的工具完成自动线程互动
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:14-27；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:228-241；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:4-10。[16]

### A09 官方字幕
上游：WBI字幕、优先ai-zh/zh-CN、全文落TXT
本地：支持最多3轨、每轨最多2000cue/60000字符，player/v2未签名
程序约束差异：本地HTTPS/CDN白名单、不带Cookie访问字幕；有大小和时间值边界
模型/提示边界：无字幕不能推断台词，两者都无原生ASR
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/debrief.mjs:99-124；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:243-282；/home/admin/projects/hermes-bilibili/skills/bilibili-workflow/SKILL.md:24-25。[16]

### A10 视频弹幕热区/字幕时间对齐
上游：支持XML采样解压、复读剔除、热区、字幕对齐
本地：缺失
程序约束差异：上游脚本完成统计；本地无视频弹幕读取入口
模型/提示边界：上游明确采样非全集；内容合成仍由LLM
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/debrief.mjs:126-195；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:38-39。[16]

### A11 下载抽帧/拼图
上游：watch-video/debrief，3x3/4x4、720像素tile、场景/均匀抽帧及字幕条
本地：简化支持：最多600秒/20MB单文件公开CDN，固定3x3每格320x180
程序约束差异：本地ffmpeg/file协议/单线程/超时/尺寸字节上限；无字幕条、长视频和复杂媒体支持
模型/提示边界：均需宿主读图；不是程序已理解视频
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:24-25；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/debrief.mjs:210-251；/home/admin/projects/hermes-bilibili/hermes_bilibili/media.py:21-23；/home/admin/projects/hermes-bilibili/hermes_bilibili/media.py:71-145。[16]

### A12 一站式深读
上游：六路素材一条龙debrief，评分卡
本地：无聚合工具；read和frames由模型逐步串联
程序约束差异：本地evidence_id只证明检索发生，不能证明全面理解
模型/提示边界：上游筛5、深读2-3是唤醒提示；本地小候选集同样靠模型
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/debrief.mjs:3-15；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/debrief.mjs:254-267；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:81-97；/home/admin/projects/hermes-bilibili/skills/bilibili-workflow/SKILL.md:24-30。[16]

### A13 角色背景/会话恢复
上游：每次唤醒/DM/评论/直播注入人设，文件优先；可人设去重及会话轮换
本地：支持本次任务显式role_context，最多6000字符+最近12条记忆
程序约束差异：上游实际slice20000而非README3000；本地无自主会话管理
模型/提示边界：人设不会覆盖宿主全局规则；私密日记可进入本次B站模型上下文
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:317-340；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/session-delivery.js:28-71；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:161-170。[16]

### A14 双日记/事件流
上游：公共与私密Markdown + diary log JSONL事件流；要求每轮都写
本地：分类SQLite memories public/private/feedback；complete自动写public副本
程序约束差异：本地complete不强制private日记；上游日记双写主要是prompt
模型/提示边界：上游散文+表格/偏好校准要求较细；本地skill要求真实经历但非完整人格生活实现
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:135-152；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:104-110；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:135-146；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:494-520。[16]

### A15 生物钟/到期触发
上游：宿主常驻60秒tick，8秒boot；多窗口多次，上限12，随机与跳过持久化
本地：仅每日最多领取一次；无自带tick，人工/用户自选Hermes cron触发
程序约束差异：本地planned/running/complete/aborted，跨日running阻止新轮；上游prompt投递成功即completePlan
模型/提示边界：上游无消息监听无需LLM；本地cron每次检查本身可能调用模型，不是零token调度
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:247-285；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:626-635；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/night-plan.mjs:15-23；/home/admin/projects/hermes-bilibili/hermes_bilibili/cycle.py:41-113；/home/admin/projects/hermes-bilibili/README.md:96-104。[16]

### A16 工作完成与证据
上游：调度记的是成功投递prompt；无本轮检索证据完成协议
本地：run_id + 实际检索evidence_id、所属轮次、24小时有效期；非skip需证据
程序约束差异：本地明确区分领取和完成；上游投递不证明阅读/日记/分享成功
模型/提示边界：本地summary内容仍可错，evidence不验证语义；skip也只靠模型避免浏览
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:272-278；/home/admin/projects/hermes-bilibili/hermes_bilibili/cycle.py:96-131；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:135-146。[16]

### A17 视频评论分享
上游：支持顶层/楼中楼、动态评论type11/17、纯文本@owner
本地：仅type1视频顶层评论；必须精确人工审批；不自动@owner
程序约束差异：本地所有comment标share，强制审批/评分/额度；无root,parent,type等参数
模型/提示边界：上游--share才进入分享约束；不带flag不等于有真实人类授权
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:74-94；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:139-164；/home/admin/projects/hermes-bilibili/hermes_bilibili/service.py:8-19；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:311-323。[16]

### A18 主人私信
上游：owner-only、互关、300字符、每日1收件人锁，普通聊天和视频分享两种
本地：owner-only、互关、300字符、精确人工审批；无单日收件人锁
程序约束差异：本地kind=dm强制share=false，故不受share_daily/7天视频去重；仍受请求预算和审批
模型/提示边界：不能把本地DM说成上游同额度视频分享通道
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/dm.mjs:102-109；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/dm.mjs:180-225；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:302-315；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:325-352。[16]

### A19 统一分享评分/理由/排名/开关
上游：comment --share及dm --share共用评分>=minScore、理由>=8、rank1、share.enabled
本地：仅comment share；min_score可配置、理由>=8；无rank、无share.enabled单独开关，以writes_enabled控制所有写
程序约束差异：上游rank默认1，真实性无法证明；本地无rank1程序检查
模型/提示边界：上游仅分享第一名与深读后分享都依赖模型；本地skill ONE best也是提示
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/share-policy.mjs:1-11；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:151-155；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:302-315；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:557-574；/home/admin/projects/hermes-bilibili/skills/bilibili-workflow/SKILL.md:27-28。[16]

### A20 每日分享/同视频去重
上游：默认3/日，可设置20甚至CLI配置更大；同BV7天；检查和记账分离
本地：comment硬上限3，可调低；同BV7天；事务保留sending/unknown/unverified额度
程序约束差异：上游不是不可调3硬上限；本地并发先占用额度，更保守
模型/提示边界：本地DM普通聊天可能含视频但不是受分享额度管控的share
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:259-266；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:125-169；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:54-57；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:557-593。[16]

### A21 反馈采集与偏好学习
上游：主人回复/收到的赞自动识别；DM最近来信反馈；手工react；feed主题词加权
本地：仅feedback分类记忆，缺点赞收件箱、自动react与算法回流
程序约束差异：本地没有记录feedback后自动改排序或额度的代码
模型/提示边界：模型可参考近期feedback，但这不等同确定性学习器
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:176-319；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/dm-feedback.mjs:3-9；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/feed.mjs:171-224；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:104-110。[16]

### A22 沉默衰减/只问一次
上游：quota分享有效cap分档衰减默认3日起、第7日0；ask持久标记；唤醒附情绪提示
本地：缺失，share_daily恒定，不依据feedback衰减
程序约束差异：上游数值cap是代码；提示quiet最多1与实际线性cap不是同一规则
模型/提示边界：只问一次/情绪/日记不关是prompt；本地无对应细流程
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:61-80；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:135-143；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:41-96；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:557-574。[16]

### A23 疑似AI评论分享前闸
上游：可选；hot/time样本20，临界扩40；30%硬常量；6h缓存；分类必须逐条匹配；不足20放行
本地：缺失
程序约束差异：上游闸限制主动顶层type1分享且via=comment；非普通回复/DM/动态/弹幕；模型标签非独立检测
模型/提示边界：模型逐条标suspected/other/uncertain；代码只验结构/比例，不能证明作者为AI
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/ai-share.mjs:25-64；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/ai-share.mjs:15-38；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:213-227；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:4-10。[16]

### A24 人工审批安全边界
上游：第三方回复先进reply-draft是提示规则；设置API支持approve/drop，命令本身可直发
本地：所有comment/DM必须账号/目标/全文digest绑定草稿，人类approve有效期10分钟；模型无approve入口；bili_send另经宿主hook
程序约束差异：上游--go/--auto/--share是调用者自声明，不能验证人类批准；本地DB状态强制
模型/提示边界：本地可信本机代码非OS沙箱；有任意终端权限可直接修改文件，不应绝对化审批防线
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:497-505；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:132-164；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:418-460；/home/admin/projects/hermes-bilibili/__init__.py:59-65；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:267-269；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:325-337；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:576-593。[16]

### A25 第三方同人30天只回复一次
上游：可选ONCE；查parent作者，主人豁免；成功写replied账本
本地：无自动回复，故缺相应规则与账本
程序约束差异：上游未知作者fail-closed，但replied损坏读成空；本地无通用评论回复写能力
模型/提示边界：本地缺失不能简单当成已实现更安全的相同功能
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:32-60；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:166-211；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:342-364；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:311-323。[16]

### A26 回复/@收件箱自动监听
上游：常驻轮询，窗口/全天/owner过滤、批量digest、游标、失败退避
本地：缺失
程序约束差异：上游读取reply+可选at首页；首次建立当前时间基线，不回放旧消息
模型/提示边界：监听命中交给模型决定回复，非代码自动对话
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:413-522；/home/admin/projects/hermes-bilibili/README.md:14-16。[16]

### A27 主人私信监听/历史补读
上游：dm-await peek/ack receipt、历史排序/撤回过滤/最多10页；可靠投递后ack
本地：缺失
程序约束差异：上游有单飞和回执但宿主prompt非端到端exactly-once；本地无零token监听
模型/提示边界：主人私信直接回、不用审是上游prompt；本地发送仍逐条人审
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:342-411；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/dm-history.mjs:19-44；/home/admin/projects/hermes-bilibili/README.md:14-16。[16]

### A28 主人@请求总结
上游：可选mentionSummary识别总结/摘要/概括后加专用任务提示、notify
本地：缺自动识别；用户可手工要求总结
程序约束差异：上游按owner和at类型程序筛选；总结/推送仍模型完成
模型/提示边界：不是独立摘要服务
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/mention-summary.js:1-15；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:4-10。[16]

### A29 直播监听/弹幕
上游：独立live开关；宿主实际默认await轮询gethistory（基础60秒、无变化退避至5分钟）；WebSocket可选实验模式，danmaku live可直接WS；pre/send/history/live/await均有入口
本地：缺失
程序约束差异：上游持久去重、子进程重挂；宿主未传--watch所以不是默认实时WebSocket；owner范围不是排他过滤，见缺陷
模型/提示边界：命中后模型回一句；无需审核和不报备是上游策略
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:524-624；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/danmaku-await.mjs:127-143；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/danmaku.mjs:49-178；/home/admin/projects/hermes-bilibili/README.md:16；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/danmaku-await.mjs:286-328。[16]

### A30 收藏
上游：理由>=6、每日3、同BV30天、类别>=2需better，收藏反馈feed
本地：缺失
程序约束差异：上游checkRules为代码，但账本非事务/坏账回空；本地不发收藏请求
模型/提示边界：三个月后还想看和明显更好由模型判断
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/fav.mjs:102-156；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/fav.mjs:184-191；/home/admin/projects/hermes-bilibili/README.md:16。[16]

### A31 动态发布/图文/编辑/删除
上游：独立CLI完整业务脚本，图文上传、@节点、check/mention核验
本地：缺失
程序约束差异：上游写请求过traffic与部分quota gate；不同写命令审计覆盖不齐
模型/提示边界：这类能力确实存在，不能被UI旧文案“不注册动态/私信”覆盖
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:45-49；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/edit-dynamic.mjs:27-68；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:38-41。[16]

### A32 删除评论/回读诊断
上游：comment-del、check-comment、thread、动态check/mention、弹幕history
本地：无删除工具；bili_verify只核查自己操作
程序约束差异：本地回读严格匹配账号/oid/type/rpid/全文，DM可靠回读不支持
模型/提示边界：上游评论发送成功后要求模型另跑thread/check；并不自动确认可见
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:14-18；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:45-49；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:339-341；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:354-377。[16]

### A33 通知/日报推送
上游：none/desktop/webhook/command/dm/stdout/file；AI闸告警去重、评论自主发送后报备
本地：无插件notify；依赖用户选择宿主cron/gateway投递
程序约束差异：上游DM仍受300字符/互关和预算；非Windows desktop退化stdout；本地不擅自设置投递
模型/提示边界：日报内容与是否包含私密内容仍由模型/策略控制
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/notify.mjs:74-85；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/notify.mjs:111-200；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:370-379；/home/admin/projects/hermes-bilibili/README.md:98-104。[16]

### A34 请求间隔/预算/风控
上游：跨进程traffic JSON锁、账号Cookie哈希、请求去重、412/429退避、手工暂停
本地：SQLite预算/单写在途guard、ID持久化、人工暂停保留、HTTP白名单
程序约束差异：本地预算只能调低、间隔只能调长；上游risk可放大且dmGapSec可0；上游漏12051/10031暂停
模型/提示边界：速率限制不是免封保证；异常状态都不能仅靠提示恢复
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/traffic.mjs:7-25；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/traffic.mjs:91-190；/home/admin/projects/hermes-bilibili/hermes_bilibili/core.py:349-455；/home/admin/projects/hermes-bilibili/hermes_bilibili/client.py:146-184。[16]

### A35 发送结果/不确定性
上游：traffic记录pending/unknown阻重复；命令code0即打印已发，confirmed仅API接受
本地：unknown/unverified/confirmed区分；先持久化remote_id再严格回读；不自动重发
程序约束差异：本地confirmed需匹配回读；上游confirmed不等于页面可见
模型/提示边界：本地DM通常只能unverified，并非送达保证
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/traffic.mjs:179-189；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:336-341；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/dm.mjs:221-228；/home/admin/projects/hermes-bilibili/hermes_bilibili/service.py:14-43。[16]

### A36 依赖体检/启动测试
上游：deps/selftest及设置API显示调度状态，确认真Node/ffmpeg/Cookie
本地：bili_status仅本地状态；宿主doctor/validate是加载注册验证，不测线上端点
程序约束差异：本地无上游一站式账号/ffmpeg健康检查
模型/提示边界：不能把已有测试报告当成本轮运行结果
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:354-388；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:31-32；/home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py:79-80；/home/admin/projects/hermes-bilibili/README.md:118-124。[16]

### A37 点赞/投币/关注/礼物/投稿/ASR
上游：没有这些动作命令；likes读取收到的赞而非点赞；fav确实能收藏
本地：同样缺这些动作与ASR
程序约束差异：区分收藏、点赞读取、投币和关注，不用泛称三连
模型/提示边界：模型不能凭提示创造不存在的接口
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:74-79；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:10-52；/home/admin/projects/hermes-bilibili/README.md:16。[16]

## 附录 B：上游问题与漂移（20 项）

### U01 宿主/工具链监听默认分叉，README不能代表新安装行为
README说comment关、DM开、once开；tools DEFAULTS确如此，但index DEFAULTS是comment=true、dm=false、once=false；scheduler读原始配置且comment仅显式false才关、DM需truthy。未保存配置时和首次设置页保存后的实际行为不同。
证据级别：static + tools defaults offline probe
建议：统一单一配置schema/defaults，调度器读取合并配置。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:18-21；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:72-96；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/config.mjs:49-54；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:353-360；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:423-431。[16]

### U02 所谓每日<=3硬限制实际可提高，风险参数也非写死
settings POST允许shareDaily到20；CLI配置层没有3的硬钳制。README49-50说600ms且写死，又在22/29行写1500ms及可改。riskSettings实际上默认1500、预算可任意正数放大、DM冷却可0。
证据级别：static + offline probe readDaily=999999 and dmGapSec=0
建议：文案按真实配置边界写；若承诺硬上限则在底层强制。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:22-30；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:49-50；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:326；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:259-266；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/traffic.mjs:7-25。[16]

### U03 第三方回复发前审核不是工具授权边界
comment接受任意显式--go，不需要人类审批凭证；AUTO/SHARE由调用者声明。reply-draft只是工作流选项。danger-full-access会让LLM可直接执行命令，不能把“先审”当程序强制。
证据级别：static
建议：建立账号/目标/全文绑定、不可变且限时的审批凭证。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:499-505；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:132-164；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:154-163；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:209-211。[16]

### U04 分享/回复/收藏账本检查-发送-记账分离，存在跨进程竞态
quota check和record为独立子进程/普通文件写；两个不同正文的同视频分享可能都先通过，traffic去重只对相同请求指纹，不能原子占用业务额度。replied/favorites也是非事务文件。不是已实测线上越额。
证据级别：static concurrency risk, no network reproduction
建议：事务化或持锁预留业务额度，提交后按结果结算。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/quota-gate.mjs:25-34；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:31-38；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:307-335；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:366-369；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/fav.mjs:103-118；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/traffic.mjs:77-99。[16]

### U05 12051/10031没有进入全局风控暂停
设置UI承诺12015/12051/10031立刻停监听并报备；traffic risk/manual数组有12015但没有12051与10031，命令仅解释它们。故收到这些码不会按该承诺停止后续请求。
证据级别：static
建议：集中统一业务风险码并测试监听暂停行为。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/client.js:259-261；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/traffic.mjs:163-178；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:385-397；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/danmaku.mjs:139-146。[16]

### U06 直播只回主人并非排他过滤
owner模式传operatorUid，但decide先匹配任何人@自身昵称，mine分支直接命中，再判断operator。因而陌生人的@在“只回主人”模式也可唤醒。
证据级别：static
建议：先按scope硬过滤发送者，再应用@和模板规则。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:558-564；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/danmaku-await.mjs:127-143。[16]

### U07 待审核组件存在但没有挂到设置section
PendingEditor定义于265行，但BiliWhaleSection最终render只含ShareEditor/ListenEditor/NotifyEditor等，没有h(PendingEditor)。API存在不等于设置页能看见待审队列。reply-draft67行又称不做UI，和其头部及组件提示互相矛盾。
证据级别：static complete return-tree inspection
建议：明确唯一审核入口，挂载组件或删除误导文案。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/client.js:264-305；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/client.js:623-671；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:1-9；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:67-79。[16]

### U08 队列先take再send，进程故障可丢草稿；无账号/digest/期限绑定
take会删条目；approve随后exec评论，只在回调失败时尝试重新add。若宿主在take后退出，或恢复子进程失败，草稿丢失；恢复不是确认写回。队列存的也是可变JSON，无审批摘要绑定。
证据级别：static failure-window analysis
建议：使用pending/claimed/sent持久状态和只读审批对象，不先删。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:56-64；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:112-121；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:429-459。[16]

### U09 动态评论坐标在待审链路丢失
comment支持type11/17和referer；reply-draft条目只保存oid/bvid/root/parent等，approve构造不传type/referer。动态评论没有bvid时还会触发comment必须bvid或referer校验，因此不能声称所有收件箱回复审批通路完整。
证据级别：static
建议：草稿完整持久化业务type和referer并验证目标。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:87-94；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:132-134；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:56-62；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:435-437。[16]

### U10 旧night配置覆盖统一设置
readNightConfig合并顺序是defaults,user,legacy；存在whale-night.config.json时旧值覆盖新设置页保存的night。破坏单配置及保存立即生效承诺。
证据级别：offline probe: unified maxVideos=2, legacy maxVideos=19 => 19
建议：迁移旧配置后停读或让统一配置优先，并提示来源。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/night-config.mjs:4-8；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:262-266。[16]

### U11 AI检查不足20条直接放行，包括0条
decision在评估匹配之前先判断rows<20并返回insufficient allow=true；这是明确策略而非安全bug定论，但不能宣传所有分享均经逐条AI评估。评论关闭/空样本可能使闸门失去检测意义。分类结果来自同一个可发命令的模型，不是独立检测器。
证据级别：offline probe: empty sample => insufficient allow=true n=0
建议：文案明确低样本放行；若要求fail-closed可对0条/读取失败另设状态。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/ai-share.mjs:36-63；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:213-227。[16]

### U12 沉默提示和硬额度来源不一致
wake silenceLines只读feedback.last，quota silence还用DM lastIncoming合并；近期主人DM可能让硬额度恢复但提示仍称多日沉默。quiet提示最多1条，quota默认第3天仍可3条、第4天可2条，两者不是同一执行规则。
证据级别：static
建议：复用单一反馈/沉默计算器，同时分别写清建议cap与硬cap。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:41-59；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:82-93；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:61-80；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/dm-feedback.mjs:3-9。[16]

### U13 API接受被命名confirmed并输出已发布，未自动验证可见性
traffic看到response.ok和code0就confirmed；comment和dm输出已发。回读是模型另跑命令的约定。接口接受、展示、对方收到三个事实应分开。
证据级别：static
建议：改用accepted/unverified，自动或显式回读后再confirmed。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/traffic.mjs:179-182；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:336-341；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/dm.mjs:221-225；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:59-62。[16]

### U14 部分账本坏账fail-open，与quota损坏即停不一致
quota非法JSON检查fail-closed，但合法{}或log非数组被置空；replied/favorites/pending损坏均catch为初始空对象，可能失去去重、额度或草稿。credentials保存模块对pending坏账另有fail-closed，行为不一致。
证据级别：static
建议：验证schema和完整性，未知状态停写，提供人工恢复流程。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:31-38；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/comment.mjs:38-40；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/fav.mjs:51-52；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:38-42；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/credentials.js:53-64。[16]

### U15 跨DSH profile共享账号与业务账本
credentialsPath固定HOME/.bili-ai，不随DSH_HOME；quota/pending/replied/favorites也在该目录。两个profile可互相影响账户、额度、收件箱与草稿；不是多账号隔离架构。
证据级别：static
建议：明确共享语义，或将所有状态按profile+验证账号隔离。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/paths.mjs:24-37；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:23-25；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:24-25。[16]

### U16 人格截断与中性默认README漂移
README及UI说只注入3000，scheduler实际20000；README说中性不称主人，index默认ownerTitle主人，而tools默认你。
证据级别：static
建议：去重默认与文案，注明字符/token上限。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:88-103；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/client.js:581-585；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:328-335；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/index.js:90；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/lib/config.mjs:30-34。[16]

### U17 CLI帮助包含已删除子命令/遗漏有效子命令和默认行为差异
bili quota帮助列set/allow，但quota底部已删除；reply-draft有show未在主帮助列出；dynamic支持list而主帮助仅post/rm/check；dynamic-edit无需dry默认预演，与写操作都必须显式dry/go口号不同。
证据级别：static
建议：从命令schema生成帮助并增加契约测试。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:29-29；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/bili.mjs:44-48；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/quota.mjs:331-338；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/reply-draft.mjs:96-110；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/edit-dynamic.mjs:12-15；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/edit-dynamic.mjs:50。[16]

### U18 feed dislike配置未参与程序过滤
feed读取dislike后无引用，标题偏好命中/质量/历史过滤确实存在但“不爱看”只能由模型后续判断；反馈n-gram也只是启发式，不是可靠语义偏好学习。
证据级别：static reference search
建议：明确模型判断边界，或增加可测试的负偏好过滤。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/feed.mjs:35-37；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/feed.mjs:250-269；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:137-140。[16]

### U19 prompt投递和任务完成混为一谈；持久可靠交付有限
scheduler delivery.send返回后completePlan，无法证明阅读/日记真正完成；DM retainUntilAck持久化pendingRequest但未记录投递阶段或证明宿主requestId幂等，崩溃于prompt成功到ack之间是否重复依赖宿主；不是端到端exactly-once。
证据级别：static crash-window risk, no host end-to-end reproduction
建议：拆delivery/claimed/completed状态；持久化回执并明确宿主幂等契约。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:272-278；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:383-405；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/session-delivery.js:25-35；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/session-delivery.js:65-80。[16]

### U20 宿主直播监听默认是轮询而非README所称实时
liveTick启动danmaku-await时只传room及可选operator-uid，没有--watch。await默认watch=poll，interval默认60秒；相同历史按idleRounds指数退避到5分钟。源码有可选实验WS，但不能把入口存在当成默认宿主实时监听；非命中退出标记12秒后重挂也只由60秒liveTick检查，非保证12秒。
证据级别：static full branch inspection
建议：设置页/README标明真实模式、空闲退避和端到端延迟；若需要实时应显式选择WS并离线测其生命周期。
源码：/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/README.md:69；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:561-570；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/lib/scheduler.js:609-634；/home/admin/.hermes/cache/scratch/dsh-bili-whale-review/tools/commands/danmaku-await.mjs:286-328。[16]

## Sources

[1] https://github.com/Alterssss/dsh-bili-whale/tree/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6 — dsh-bili-whale v0.4.6 pinned source
[2] file:///home/admin/projects/hermes-bilibili/audits/20261029-080759/baseline.json — Hermes Bilibili audit source hashes
[3] file:///home/admin/projects/hermes-bilibili/hermes_bilibili/core.py
[4] file:///home/admin/projects/hermes-bilibili/hermes_bilibili/client.py
[5] file:///home/admin/projects/hermes-bilibili/hermes_bilibili/integration.py
[6] file:///home/admin/projects/hermes-bilibili/hermes_bilibili/cycle.py
[7] file:///home/admin/projects/hermes-bilibili/hermes_bilibili/media.py
[8] file:///home/admin/projects/hermes-bilibili/hermes_bilibili/service.py
[9] file:///home/admin/.hermes/hermes-agent/tools/approval.py
[10] file:///home/admin/projects/hermes-bilibili/audits/20261029-080759/local-tests.txt
[11] file:///home/admin/projects/hermes-bilibili/audits/20261029-080759/upstream-tests.txt
[12] file:///home/admin/projects/hermes-bilibili/__init__.py
[13] file:///home/admin/projects/hermes-bilibili/audits/20261029-080759/core-repro-output.txt
[14] file:///home/admin/projects/hermes-bilibili/audits/20261029-080759/client-repro-output.txt
[15] file:///home/admin/projects/hermes-bilibili/audits/20261029-080759/parent-repro-output.txt
[16] file:///home/admin/projects/hermes-bilibili/audits/20261029-080759/comparison-findings.json
