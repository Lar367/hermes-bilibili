# 审计缺陷修复验收（本轮限定复审完成）

修复前审计：audits/20261029-080759/AUDIT-REPORT.md。
本轮基线：audits/fix-20261029-125129/baseline.json。
本轮没有修改 Hermes 核心、真实 profile 配置、凭据或 Git 暂存；未登录或访问真实 B站，未执行真实外发。

## 已实现并由父审计重跑验证

- CORE-001：读写都使用 float-compatible Reservation.request_id，实际派发事务重查时间/间隔/额度/暂停，持有全局在途 guard，完成后记录时间。迟到 GET 同刻突发被拒，跨午夜 read_daily=1 只能真实派发一次。
- CORE-002：账号预算时区固定，不允许通过配置热变更重新解释历史额度。不同 timezone 明确拒绝，不默默重置。
- CORE-003：独立 approvals 表校验批准摘要与期限；只改草稿生命周期字段不能产生审批来源。旧批准事件不伪造，旧未发送批准草稿需重建。不是对任意本地改库攻击的 OS 安全承诺。
- CORE-004：SQLite 时间戳、暂停标志/计数、请求标志等坏值统一 PolicyError，不再裸 TypeError。
- CORE-005：schema version=1 及必需列预检；支持当前无版本布局的增量迁移，未知/缺列版本提前拒绝且保留数据库。
- CLIENT-01/02：永久凭据指纹→账号关联，迁移过期 identity 缓存；暂停关联不随 TTL 消失，身份漂移拒绝，新身份暂停复查。
- CLIENT-03：verify 绑定持久 id/operation/digest/state/remote_id；service交给verify的是已落库accepted对象。
- CLIENT-04：字幕及生产媒体下载共用派发 context，暂停在等待结束后仍复核，风险在 guard 内处理，public不带Cookie。
- CLIENT-05：WBI及GET共用RFC3986编码；真实Node中文/空格/加号/波浪线向量一致。
- H-P01/02：候选上限实际执行，0拒搜索；人设/记忆先验证再领取周期，不留隐藏running。
- H-P03：宿主审批rule_key按草稿隔离，文档明确off/YOLO/allowlist仍适用，精确数据库批准不变。

## 最小加固与明确保留

- CLIENT-07：read1分块检查及标准库响应socket剩余预算；每输出目录自有PNG最多20张/50MB，拒匹配symlink。媒体dispatch等待后若已超时，在open前拒绝；open传入剩余预算。
- CLIENT-08：强制MOV/MP4解码与file协议白名单，真实本地MP4/PNG测试通过；没有宣称OS文件系统/CPU/内存沙箱。
- CLIENT-06：旧字幕/播放端点未改，线上兼容性没有验收，不视为已修复。
- 单次DNS/open/socket阻塞的严格总墙钟中断、跨任意目录与并发任务的全局磁盘额度、OS沙箱仍未实现。
- 强杀遗留的全局在途guard不自动过期、无模型清锁入口，当前无用户恢复UI；这是保守停请求的已说明限制。

## 实际执行

父agent修正了旧E2E测试的真实clock+空sleep，使用注入时钟推进；没有放松生产派发检查。先观察原E2E失败，再完成fixture修正和全套回归。

全量：137 tests，OK（无跳过），隔离HOME/HERMES_HOME，真实ffmpeg及Node离线执行；原始输出full-tests.txt。134为第一次合并全绿，随后新增3项deadline回归全部先RED再GREEN。

审计场景父agent重放：replay-fixed.py，11个检查均passed；原始结果audit-replay-results.json。原审计复现脚本保留不改，因为其断言描述旧缺陷，不能直接把旧脚本非零等同新回归失败。

隔离安装目录：/home/admin/.hermes/cache/scratch/bili-fixes-host-37ya_zfj。
安装、doctor、validate、启用、公共list探针、从已安装副本执行offline status共6条命令全部exit0；status success=true、writes_enabled=false。详情host-verification.json。默认profile未安装或启用。
deadline追加修复后重新执行源码Plugin Doctor通过（8工具/1hook），输出final-host-doctor.txt；随后更新隔离已安装副本的client/media/README，再次doctor、validate、offline status三条全部exit0，结果post-deadline-host.json。验证仍只涉及隔离副本，不修改默认profile。

静态有限模式扫描：shell=True/eval/exec/pickle/动态f-string SQL无匹配；不是全面静态安全证明。git diff --check无错误。

## 复审

适配层独立复审已通过（parent-review.json，独立31项测试），源码哈希父agent已核对一致。
core/client复审确认核心授权、永久身份、预算和回读修复有效，但因读取剩余期限未传给socket报passed=false；media复审因dispatch等待超时后仍open报passed=false。两项报告原文保留，不能改成通过。

父agent分别补正式失败回归后修复：标准库响应每次read1前缩短socket timeout到剩余预算；媒体dispatch后open前计算remaining并拒绝<=0，读取同样缩短socket timeout。media原始独立repro已重跑4/4通过；137全套通过。DNS/connect/TLS与无socket替代响应的硬总deadline仍不承诺。

最终限定复审通过（deadline-review-final.json）：logic_errors=[]，security_concerns=[]。独立重跑全套137项及原媒体repro4项通过；实际stdlib HTTPResponse+socketpair验证API与media两路径在模拟open消耗19秒后socket timeout缩至1秒，真实读取约1秒超时、响应关闭、媒体残件删除。未访问外网。父agent已逐项核对复审记录中的7份源码/测试/文档SHA256与当前文件一致，并复核自己的137项日志及更新后的隔离副本doctor/validate/status。

结论：本轮已确认的CORE-001–004、CLIENT-01–05、H-P01/02缺陷及CORE-005预检已实现并回归；H-P03按准确契约澄清并隔离草稿审批规则；追加两项deadline反馈修复复审通过。此结论不将上述明确保留事项标成已修复，不是全项目或线上B站认证。旧失败审查报告与原审计报告保留历史，不覆盖其证据。
