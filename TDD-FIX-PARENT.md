# 适配层审计修复 TDD

本轮不访问真实 B站，不读取真实凭据，不改宿主配置或 Git 暂存。

- H-P01：新增 test_search_enforces_configured_candidate_ceiling，实际观察 20!=1、20!=3 两个失败；integration 搜索取 min(请求limit, max_candidates)，再次执行1项通过；已有integration12项通过。
- H-P01 边界：max_candidates=0 在搜索前拒绝，新增测试直接通过，属于同一实现的边界验证，不伪称额外RED。
- H-P02：新增真实 CyclePlanner/Store、无效 UTF-8 persona 测试，实际RED证明返回失败但running已保存；将 role_context 校验移至 start 之前，GREEN且integration既有12项通过。
- CLIENT03 service配合：新增test_readback_uses_persisted_accepted_state，实际RED state=None；service.send改为将finish_send返回的accepted对象交给verify，GREEN，service11项通过。
- H-P03：新增按draft_id隔离审批rule_key测试，实际RED两者None；根register hook显式rule_key=hermes-bilibili:send:<draft_id>，GREEN，plugin7项通过。README/任务skill澄清宿主off/YOLO/allowlist仍适用，不声称不可关闭的二次人工同意；精确数据库批准不变。

最终全仓测试、Store/client并行集成结果另见修复验收报告，以上为适配层的真实逐步执行记录。
