"""Read-only audit probes. No production edits or Bilibili network."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from hermes_bilibili import integration
from hermes_bilibili.core import Settings, Store
from hermes_bilibili.cycle import CyclePlanner


def run():
    findings = []
    with tempfile.TemporaryDirectory(prefix='bili-parent-probe-', dir=os.environ['TMPDIR']) as temp:
        root = Path(temp)
        now = datetime(2026, 10, 29, 21, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        store = Store(root / 'state.db', clock=lambda: now)
        class Client:
            def search(self, keyword, limit=5):
                return {'items': [{'title': str(i)} for i in range(limit)], 'untrusted': True}
        from types import SimpleNamespace
        service = SimpleNamespace(settings=Settings(max_candidates=1), client=Client(), store=store)
        try:
            with patch.object(integration, 'open_service', return_value=service):
                result = json.loads(integration.invoke_tool(None, 'bili_read',
                                                           {'action': 'search', 'query': 'fixture', 'limit': 20}))
            findings.append({'id': 'H-P01', 'title': 'max_candidates配置未参与搜索上限',
                             'configured_max_candidates': 1, 'requested_limit': 20,
                             'returned_count': len(result['data']['items']), 'success': result['success']})
        finally:
            store.close()

    with tempfile.TemporaryDirectory(prefix='bili-cycle-probe-', dir=os.environ['TMPDIR']) as temp:
        root = Path(temp)
        store = Store(root / 'state.db')
        (root / 'persona.md').write_bytes(b'\xff\xfe invalid utf8')
        service = SimpleNamespace(settings=Settings(), store=store)
        start = CyclePlanner.start
        # Freeze only planner clock, preserving the real planner implementation.
        def fixed_start(planner, settings):
            planner.clock = lambda: now
            planner.random = lambda: 0
            return start(planner, settings)
        with patch.object(integration, 'open_service', return_value=service), patch.object(CyclePlanner, 'start', fixed_start):
            result = json.loads(integration.invoke_tool(None, 'bili_cycle', {'action': 'start'}))
        status = CyclePlanner(root / 'cycle.db').status()
        findings.append({'id': 'H-P02', 'title': '人设读取失败但周期已领取且返回错误无run_id',
                         'tool_result': result, 'persisted_state': status[0]['state'],
                         'run_id_in_response': 'run_id' in result.get('data', {})})

    # Verify host's actual approval bypass, without changing config or globals.
    from tools import approval
    with patch.object(approval, '_yolo_active', return_value=False), patch.object(approval.approval_context, '_get_approval_mode', return_value='off'):
        result = approval.request_tool_approval('bili_send', 'audit fixture approval only')
    findings.append({'id': 'H-P03', 'title': '宿主关闭审批时pre_tool_call不是第二次人工确认保证',
                     'host_approvals_mode': 'off', 'result': result,
                     'qualification': '不可变草稿数据库审批仍然保留；不是绕过草稿审批'})
    return findings


if __name__ == '__main__':
    output = run()
    path = Path(__file__).with_name('parent-findings.json')
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(output, ensure_ascii=False, indent=2))
