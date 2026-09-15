const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const template = fs.readFileSync('career_radar/templates/index.html', 'utf8');
const script = fs.readFileSync('career_radar/static/app.js', 'utf8');

test('persistent chat drawer exposes the conversation controls', () => {
  for (const id of ['chat-drawer', 'chat-context-label', 'chat-messages', 'chat-form', 'chat-input']) {
    assert.match(template, new RegExp(`id="${id}"`));
  }
});

test('target company resume workbench exposes selection, editing and export controls', () => {
  for (const id of ['panel-6', 'target-job-form', 'tailoring-questions', 'resume-workbench', 'resume-preview', 'export-resume']) {
    assert.match(template, new RegExp(`id="${id}"`));
  }
  assert.match(script, /\/api\/target-jobs/);
  assert.match(script, /\/api\/resume-tailorings/);
  assert.match(script, /\/api\/resume-drafts/);
  assert.match(script, /为这个岗位修改简历/);
});

test('chat UI uses asynchronous turns and explicit action confirmation', () => {
  assert.match(script, /\/api\/conversations\/\$\{state\.conversationId\}\/messages/);
  assert.match(script, /\/api\/chat-turns\/\$\{taskId\}/);
  assert.match(script, /\/api\/chat-actions\/\$\{actionId\}\/confirm/);
  assert.match(script, /\/api\/chat-actions\/\$\{actionId\}\/reject/);
  assert.match(script, /旧岗位和报告继续保留/);
  assert.match(script, /停止当前采集/);
});
