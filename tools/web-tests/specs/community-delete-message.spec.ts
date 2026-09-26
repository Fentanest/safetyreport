import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

// 공유 자료 삭제 응답 → 설정 카드 안내(Sol 통합 5차 낮음). 템플릿의 deletionResultMessage 를 그대로 꺼내 브라우저에서 실행한다.
const TEMPLATE = path.resolve(__dirname, '../../../web/templates/components/community_consent_card.html');

function extractFn(): string {
  const src = fs.readFileSync(TEMPLATE, 'utf8');
  const m = src.match(/ {4}function deletionResultMessage\(data\) \{[\s\S]*?\n {4}\}\n/);
  if (!m) throw new Error('deletionResultMessage not found in template');
  return m[0];
}

test('deletion result names every post-delete problem, success only when none', async ({ page }) => {
  await page.setContent('<html><body></body></html>');
  const fn = extractFn();
  const out = await page.evaluate((code) => {
    // eslint-disable-next-line no-new-func
    const f = new Function(code + '; return deletionResultMessage;')();
    return {
      ok: f({ result: { deleted_facts: 3 } }),
      local: f({ result: { deleted_facts: 3 }, local_cleanup_pending: true }),
      writer: f({ result: {}, writer_reset_pending: true }),
      both: f({ result: { deleted_facts: 2 }, local_cleanup_pending: true, writer_reset_pending: true }),
    };
  }, fn);
  expect(out.ok).toEqual({ text: '삭제를 요청했습니다 (3건).', level: 'success' });
  expect(out.local.level).toBe('warning');
  expect(out.local.text).toContain('대기 사본 정리를 끝내지 못했습니다');
  expect(out.writer.level).toBe('warning');
  expect(out.writer.text).toContain('업로드 연결 파일을 지우지 못했습니다');
  expect(out.both.level).toBe('warning');
  expect(out.both.text).toContain('대기 사본 정리를 끝내지 못했습니다');
  expect(out.both.text).toContain('업로드 연결 파일을 지우지 못했습니다');
});
