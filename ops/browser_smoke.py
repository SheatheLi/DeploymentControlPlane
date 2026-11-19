"""Real Chromium DOM/event test, OFFLINE mocked API; not PG end-to-end.

No browser navigation is needed (some CI environments block browser URL navigation).
Run: python ops/browser_smoke.py
Needs Playwright Python + a local Chromium executable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web/react"
EVIDENCE = ROOT / "evidence/round12"

FIXTURE_JS = r'''
window.__apiCalls = [];
window.__force503 = false;
window.fetch = async (url, opt = {}) => {
  window.__apiCalls.push({url, opt});
  if (window.__force503) return {ok:false,status:503,text:async()=>JSON.stringify({detail:'PostgreSQL 连接不可用'})};
  const fixtures = {
    '/api/services': [{name:'demo',desired_version:'v1',generation:1,
       active_version:'v1',active_generation:1,active_endpoint:'http://127.0.0.1:8766'}],
    '/api/audit': [{id:1,ts:'2026-10-16',service:'demo',generation:1,event:'PROMOTED',detail:'ok'}],
    '/api/status/demo': {service:{name:'demo',active_endpoint:'http://127.0.0.1:8766'},
       jobs:[{id:1,state:'PROMOTED',attempts:1,generation:1}],
       effects:[{effect_id:'release:demo:1',state:'ACKED',attempts:1,generation:1}]},
    '/api/deploy': {service:'demo',generation:1,status:'PENDING'}
  };
  if (!(url in fixtures)) throw new Error('unexpected request: ' + url);
  return {ok:true,status:200,text:async()=>JSON.stringify(fixtures[url])};
};
'''


def main() -> None:
    browser_exe = os.environ.get('DCP_CHROMIUM', '/usr/bin/chromium')
    if not Path(browser_exe).is_file():
        raise SystemExit('BROWSER_CHROMIUM_GATE=BLOCKED: set DCP_CHROMIUM to Chromium executable')

    with sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=browser_exe, headless=True,
                                         args=['--no-sandbox', '--disable-dev-shm-usage'], timeout=15000)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 960})
            exceptions: list[str] = []
            page.on('pageerror', lambda error: exceptions.append(str(error)))
            # Chromium navigation is blocked by administrator policy in this CI.
            # Render the exact shipped React vendor, app.js and CSS into a page.
            page.set_content('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
                             '<body><div id="root"></div></body></html>')
            page.add_style_tag(content=(WEB/'app.css').read_text(encoding='utf-8'))
            page.add_script_tag(content=(WEB/'vendor/react.production.min.js').read_text())
            page.add_script_tag(content=(WEB/'vendor/react-dom.production.min.js').read_text())
            page.add_script_tag(content=FIXTURE_JS)
            page.add_script_tag(content=(WEB/'app.js').read_text(encoding='utf-8'))

            assert page.locator('h1').inner_text() == 'Deployment Control Plane'
            assert page.get_by_role('button', name='提交发布').count() == 1
            assert page.get_by_role('button', name='刷新状态').count() == 1
            assert page.locator('input[type=password]').count() == 1
            # Fail-closed UI: no request should occur without the admin token.
            page.get_by_role('button', name='提交发布').click()
            assert '请先输入管理令牌' in page.get_by_role('alert').inner_text()
            assert page.evaluate('window.__apiCalls.length') == 0

            page.get_by_label('管理令牌').fill('local-admin')
            page.get_by_label('服务名称').fill('demo')
            page.get_by_label('目标版本').fill('v1')
            page.get_by_role('button', name='提交发布').click()
            page.get_by_text('已持久化请求', exact=False).wait_for(timeout=5000)
            calls = page.evaluate('window.__apiCalls')
            assert [c['url'] for c in calls] == ['/api/deploy','/api/services','/api/audit','/api/status/demo'], calls
            posted = calls[0]['opt']
            assert posted['method'] == 'POST'
            assert posted['headers']['X-Admin-Token'] == 'local-admin'
            assert json.loads(posted['body'])['idempotency_key'].startswith('ui-')
            assert json.loads(posted['body'])['service'] == 'demo'
            assert page.locator('table tbody tr').count() == 1
            assert 'PROMOTED' in page.locator('body').inner_text()
            assert 'ACKED' in page.locator('body').inner_text()

            EVIDENCE.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(EVIDENCE/'chromium_dom_contract.png'), full_page=True)

            # Failure is a DOM-visible error, NOT success; status is unchanged.
            page.evaluate('window.__force503 = true')
            page.get_by_role('button', name='刷新状态').click()
            assert 'PostgreSQL 连接不可用' in page.get_by_role('alert').inner_text()
            assert not exceptions, exceptions
            print('BROWSER_CHROMIUM_DOM=PASS')
            print('REACT_VENDOR_AND_COMPILED_TYPESCRIPT=PASS')
            print('REAL_BROWSER_CLICKS_AND_ERROR_DOM=PASS')
            print('MOCKED_API_CALLS=5')
            print('POSTGRESQL_REAL_BROWSER_NETWORK=NOT_RUN (browser navigation prohibited; API mocked)')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
