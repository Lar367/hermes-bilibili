# 接力开发入口

状态基准：2026-10-30，版本0.1.0；功能代码基线提交 `64d2ed0c1d37f598bbee8acbac9e0835244b2033`。本次接力文档更新不改变生产代码。拿到仓库后先核对 `git log`，不要把此日期的状态当作永久事实。

## 下一位开发者先做什么

1. 阅读本文、[开发与测试](docs/DEVELOPMENT.md)和 [架构](ARCHITECTURE.md)。
2. 按 [开发与测试](docs/DEVELOPMENT.md) 在临时 HOME/HERMES_HOME 下跑测试，得到自己的基线。
3. 从 [待办及验收条件](docs/ROADMAP.md) 选择一个任务；先复现/失败测试，再修改，不因旧测试绿就省略边界验证。
4. 安装与恢复看 [安全运维](docs/OPERATIONS.md)。不要先开启默认profile、外发或cron。
5. 离线验证、真实只读验收、真实外发是不同权限和阶段；没有明确授权不能互相代替。

## 项目是什么

原生Hermes插件：搜索/阅读B站，辅助抽帧，人设与分类记忆，每日阅读周期，以及逐条批准后发送评论或给主人私信。Python运行时仅标准库；可选本地ffmpeg。不是B站全自动运营系统，不是dsh-bili-whale全功能移植。

实际注册8工具：bili_status、bili_read、bili_frames、bili_prepare、bili_send、bili_verify、bili_memory、bili_cycle。入口与权限见 [架构](ARCHITECTURE.md)。

## 到哪里为止已经完成

- 审批：绑定账号/目标/精确正文/理由/评分/分享标志的摘要，独立审批记录和有效期，不能修改正文后沿用批准。
- 派发：API、字幕CDN、生产媒体下载统一预约ID；SQLite在真实派发前重查预算、间隔、暂停；持久全局guard覆盖transport和风险处理。
- 中断：remote_id先落unverified再回读；unknown/sending等未决操作不可自动重发；强杀guard不自动接管。
- 身份/预算：凭据指纹永久绑定账号，暂停不随TTL消失，拒绝身份漂移；账号预算时区固定。
- 读取：搜索候选硬上限生效，0禁止搜索；人设/记忆先读成功再领取周期。
- 签名：搜索复用WBI实现；字幕 `/x/player/wbi/v2`，播放 `/x/player/wbi/playurl`。仅主字幕返回结构正确空列表时尝试一次 `/x/v2/subtitle/web/view`。
- 媒体：仅允许HTTPS公共CDN、无Cookie下载，再本地受限mov/MP4解码；输出目录/时长/大小/拼图保留检查；不承诺OS沙箱。
- 独立远程浏览器辅助：SSH隧道+noVNC，手动登录/验证；不自动完成验证码，不自动把浏览器登录态导入插件。

## 实际验证与没验证的事

- 发布前在干净快照执行插件144项+远程启动策略2项测试，全部通过；本轮接力文档按DEVELOPMENT再执行相同全量、聚焦和隔离安装也通过，详见VERIFICATION。包含真实本地ffmpeg和Node签名编码向量；下一位仍须产生自己的基线。
- 本轮发现历史replay-fixed.py的字幕fixture缺新增nav响应，预期CDN暂停断言会先被签名失败打断，已列待更新而非推荐全绿入口；正式test_client_fixes与compat补充探针覆盖当前链路，不修改保护来满足旧fixture。
- 真实SQLite独立字幕探针5项/30子场景已验证预算、各阶段等待后暂停、错误不兜底、URL完整性和无Cookie。探针在 [compat-review-repro.py](audits/remaining-fixes/compat-review-repro.py)。
- 安装doctor/validate/启用/status仅在隔离profile验过；它们不证明线上接口可用。
- 匿名首页和单个公开元信息接口曾实际可达；真实账号字幕/播放/搜索/下载的完整验收仍未完成，CLIENT-06线上兼容性不能标closed。
- 没有执行真实评论/私信发布。尚未验收浏览器→插件的安全登录态接入。
- 第一次102项、后来137项、当前144项是不同阶段，不能把旧报告数量当当前总数。远程浏览器2项不包含在144里。
- 各阶段独立复审都是限定结论，详见 [审计证据索引](docs/AUDIT-INDEX.md)，不是全仓认证。

## 本机交接位置（换机器后不适用）

- 当前有remote、可继续提交的工作副本：`/home/admin/projects/hermes-bilibili-published`，origin为本项目私有仓库。
- 早期原开发目录：`/home/admin/projects/hermes-bilibili`。其原暂存区保留且此前无HEAD，不能直接在这里 `git add .` 或当成最新main。
- 安全发布快照/审查清单和详细日志位于本机scratch，可能被清理；不是长期证据唯一来源，也不属于源码仓库。
- 离线测试依赖应在当前克隆新建 `.venv`；不依赖旧目录的 `.venv` 或硬编码python3.14目录。
- 手动浏览器profile独立于Hermes profile：`~/.local/share/hermes-bilibili-browser`。其中可能有登录态，不上传、不读取内容做交接。
- 本次开始交接时，`bili-browser-takeover` 和 `github-publish-browser` 服务均为inactive。不要把曾经可用的localhost入口当仍在线。
- B站登录事项按用户要求暂停；默认profile未因本项目自动启用B站插件或创建阅读cron，写开关默认false。再次操作前读取实际配置。

## 剩余风险与下一步建议

优先只读线上验收和安全人工接管，再做一键阅读报告、关注更新、反馈闭环。明确保留：强杀guard没有操作员恢复工具、跨目录/并发的全局磁盘额度、严格DNS/TLS墙钟中断、OS资源沙箱、更多schema迁移能力。详细验收边界见 [ROADMAP](docs/ROADMAP.md)。

不实现：自动解人机验证、风控后换账号/代理重试、审批绕过、未知结果自动重发。现有业务安全契约不能为“跑通登录”而放松。

## 接力后应更新的内容

一次有意义的变更结束后，更新本文状态、ROADMAP任务验收、VERIFICATION实际命令/结果；新增缺陷记录复现与修复范围。不要记录密码、OTP、Cookie、OAuth token或vault秘密。仓库包含明确synthetic测试值，不是账号授权来源。
