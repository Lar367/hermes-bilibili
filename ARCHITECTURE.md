# 当前架构与数据契约

适用基线：0.1.0，功能提交64d2ed0。此文件描述当前代码，不是最初任务分工或未来功能承诺。源码发生变化时同步更新。

## 总体链路

    Hermes PluginManager
      -> 根 __init__.py.register(ctx) / plugin.yaml
      -> integration.py（参数白名单、profile、secret scope、工具分派）
      -> Service（prepare / send / verify）
          -> BiliClient（API、WBI、规范化、身份、dispatch）
          -> Store（SQLite事务：草稿、批准、额度、暂停、guard）
      -> CyclePlanner（独立SQLite：每日计划、run、证据）
      -> media.py（公共CDN下载 -> 本地ffprobe/ffmpeg -> PNG）

remote_browser目录是独立人工接管辅助，与BiliClient/插件账号状态尚未连通。插件导入、注册和本地status不要求访问B站。

## 模块与入口

| 模块 | 当前职责 | 主要回归 |
| --- | --- | --- |
| 根 `__init__.py`、`plugin.yaml` | 8工具、1 pre_tool_call hook、/bili、可选host CLI、命名空间skill | test_plugin |
| `integration.py` | 严格参数、当前profile和安全凭据读取、证据/周期/人设适配 | test_integration、test_integration_fixes |
| `core.py` | Settings、Reservation、Store，所有本地业务安全状态 | test_core、test_core_fixes、test_dispatch |
| `client.py` | 受限HTTP、WBI、读取与发送API、账号绑定与精确回读 | test_client、test_client_fixes、test_client_compat |
| `service.py` | 不可变草稿准备、claim、结果持久化、回读流程 | test_service、test_e2e |
| `cycle.py` | 随机日计划、唯一领取、证据绑定、完成/人工中止 | test_cycle |
| `media.py` | 公共媒体下载、安全URL、mov解码、拼图与淘汰 | test_media、test_client_compat |
| `cli.py`、`__main__.py` | 读取宿主设置，调用统一adapter；审批需真实交互 | test_cli |
| `scripts/install_local.py` | 复制原生发行文件；拒绝覆盖；不启用 | test_cli |
| `scripts/verify_host.py` | 只在scratch临时profile中核验公开CLI启用状态 | test_plugin |

## 工具表面

| 工具 | 能做 | 不做 |
| --- | --- | --- |
| bili_status | 本地预算、暂停、草稿状态计数、在途指示 | 不输出凭据/正文，不清锁 |
| bili_read | action=search/video/comments/subtitles，返回untrusted和evidence_id | 不接受任意URL，不批准 |
| bili_frames | 单BV受限九宫格和evidence_id | 不完整观看、不ASR |
| bili_prepare | comment/dm草稿与digest | 不批准、不发送 |
| bili_send | 一条已批准草稿 | 不改正文、不自动重试 |
| bili_verify | 持久operation回读核查 | 不重发、不保证DM送达 |
| bili_memory | append/list/context，public/private/feedback | 分类不是加密，不自动全局注入 |
| bili_cycle | start/status/complete | 不提供模型abort，不创建cron |

参数schema以根__init__.py及integration.ALLOWED/valid_arguments为准。用户入口 `/bili approve/reject/draft/resume/cycle-abort/status` 与模型工具区分。CLI审批检查isatty和HERMES_GATEWAY；同OS用户任意代码执行不在此隔离保证内。

## 草稿/发送状态机

    draft -> approved -> sending -> unverified -> confirmed
      |         |          |
      +-------> rejected   +-> unknown -> confirmed
                           +-> failed

- 草稿有效1800秒；批准最长600秒且不越过草稿到期。重复批准不延长期限。
- digest绑定account、kind、target、text、reason、score、share；approvals表核对digest与expiry。SHA256不是防本机恶意改库的MAC。
- claim_send原子绑定operation_id并进入sending；以后不可退回approved。failed也不复活。
- BiliClient发送前比对持久claim、账号、owner/私信互关、CSRF与设置。所有评论都按share处理；私信仅给主人、不能发给自己。
- API返回remote_id后，Service先持久化unverified，然后将该持久对象交verify。精确rpid/oid/type/member/text匹配才confirmed。
- definitive拒绝记failed；传输/不明错误记unknown。BaseException/强杀可能留下sending或guard。都不是自动再发依据。
- unknown/unverified只能在有匹配证据后升confirmed；DM目前返回不支持可靠回读。

## HTTP派发契约

1. `reserve_request(...) -> Reservation`：float子类表示等待秒数，`.request_id`是持久预约ID；入库即计预算，包括后来取消/失败的预约。
2. 等待期间可能暂停、到期、跨日或被其他进程抢先；`validate_dispatch(..., request_id=...)` 在事务内重算真实派发日/间隔/预算/批准并拒绝冲突。
3. 设置requests.dispatched与全局dispatch_guard同事务提交；guard跨进程覆盖校验到transport、风险响应处理结束。
4. `finish_dispatch(..., request_id=...)` 校验所有权、保守推进slot至完成时间，再删除guard。失败/强杀不自动接管。

`operation_id`用于业务发送，`request_id`用于每次HTTP，两者不能混同。读取guard使用read:<id>作为历史operation_id列标记。无request_id的读兼容路径仅检查暂停，真实客户端不能使用它替代完整派发。

HTTP/API风险在guard内落暂停；不使用自动重试、备用账号或换代理规避。HTTP仅精确host HTTPS、禁redirect，Cookie仅API host。字幕/媒体CDN无Cookie。WBI签名与实际GET都用相同RFC3986编码。

## 读取与媒体

- search先nav取密钥，再 `/x/web-interface/wbi/search/type`；adapter实际limit不大于max_candidates，0在请求前拒绝。
- video使用 `/x/web-interface/view`，仅此稳定元信息按account/BV做TTL缓存；评论 `/x/v2/reply`。
- subtitles：video -> nav -> `/x/player/wbi/v2`；仅结构正确空tracks -> 一次 `/x/v2/subtitle/web/view`；至多3轨，每轨2000 cue和60000字符等界限。错误与风险不备用，完整auth_key URL即时使用、不缓存。
- frames：video -> nav -> `/x/player/wbi/playurl`，qn16/fnval0/fourk0，单durl。公共HTTPS下载≤20,000,000 bytes，随后本地强制mov/MP4解析。视频≤600秒、视频流尺寸≤4096、拼图≤5,000,000 bytes。
- 每目录自有拼图≤20张及50,000,000 bytes；保留本次输出，淘汰最旧自有文件，不删无关文件/跟随symlink。不是跨目录/并发总配额。
- API20秒、媒体30秒是检查点/剩余socket预算；DNS/open/TLS不受严格硬总墙钟中断保证。ffprobe15秒、ffmpeg40秒子进程timeout，不构成CPU/内存/文件系统沙箱。

## 数据目录与schema

当前profile `$HERMES_HOME/plugin-data/hermes-bilibili/`：

- state.db（WAL）：drafts、operation_ids、approvals、requests、dispatch_guard、pauses、account_settings、identities、cache、memories。
- cycle.db：cycles、evidence，单独连接/事务，不与state.db构成跨库原子事务。
- persona.md：任务内按上限读UTF-8；frames/：自有PNG/短暂下载工作目录。

state.db `PRAGMA user_version=1`；接受版本0/1的已知布局并增量补列，未知版本/缺必要列拒绝保留原库。不自动补造旧审批事件。Store拒symlink，目录700文件600；CyclePlanner也检查目录/sidecar。字段详见core.py初始化与schema_preflight，不将文档表当迁移脚本。

identities只保存凭据SHA256指纹到mid关联，不存Cookie；过期历史identity缓存也迁移。account_settings固定已有账号预算timezone，冲突需显式操作员迁移。private日记是分类，不是加密。

## 周期与证据

CyclePlanner持久化日窗口内随机due/skip，每日一次；running跨日阻止再领取。integration先完成role_context再start。读取生成evidence_id，分别写state cache和cycle evidence，后者绑定当前run；complete同时检查来源/本轮/24h有效，非skip必须有证据。周期先complete后写public日记副本，日记失败返回warning且不能重跑周期。它证明调用发生，不证明模型摘要真实准确，也不是证据内容哈希存档。

## 宿主和发行边界

- 插件通过ctx与plugin_data_dir/get_secret使用当前profile，不改Hermes核心或SOUL。
- send hook按draft_id隔离rule_key，宿主off/YOLO/allowlist仍有效；精确草稿批准是独立底线，不承诺二次弹窗。
- 安装脚本复制根__init__.py/plugin.yaml/README/LICENSE及hermes_bilibili、skills。docs/tests/audits/remote_browser不是运行载荷。
- pyproject仅打包hermes_bilibili；pip安装用于开发，不等同原生插件发行安装。
- remote_browser人工接管与浏览器vault、插件secret scope、GitHub CLI认证是不同层次，不互相继承。

限制与验收计划见 [ROADMAP](docs/ROADMAP.md)；恢复及凭据处理见 [OPERATIONS](docs/OPERATIONS.md)。
