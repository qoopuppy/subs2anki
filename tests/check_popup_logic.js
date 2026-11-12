/* 扩展前端纯逻辑的校验：node tests/check_popup_logic.js
 * 用 vm 把 popup.js 跑在一个最小 DOM/Chrome stub 里，验证字幕解析与断句规则
 * 和服务端保持一致（浏览器 UI 本身需按 README 手动加载验证）。
 */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const root = path.resolve(__dirname, '..');
const code = fs.readFileSync(path.join(root, 'extension', 'popup.js'), 'utf8');

const defaults = { lang: 'ja', minDur: '1.0', maxDur: '12', gap: '0.65' };
const el = (id) => ({
  value: defaults[id] !== undefined ? defaults[id] : '',
  type: '', checked: false, innerHTML: '', textContent: '', style: {},
  classList: { toggle() {}, contains() { return false; }, remove() {}, add() {} },
  querySelector() { return { textContent: '' }; },
  appendChild() {},
});

const ctx = {
  console, setTimeout, clearTimeout, Promise, Math, JSON, Date, RegExp,
  String, Number, Array, Object, Error, URL, URLSearchParams,
  encodeURIComponent, decodeURIComponent, parseInt, parseFloat,
  location: { href: 'https://www.youtube.com/watch?v=test' },
  navigator: { clipboard: { writeText() {} } },
  alert: () => {},
  document: {
    addEventListener() {}, getElementById: el,
    createElement: () => ({ value: '', textContent: '', appendChild() {} }),
  },
  chrome: {
    runtime: { sendMessage() {} },
    storage: { local: { get: () => Promise.resolve({}), set() {} } },
    downloads: { download: () => Promise.resolve(1) },
  },
};
vm.createContext(ctx);
vm.runInContext(code, ctx, { filename: 'popup.js' });

const { parseCues, parseTimedtextXml, segmentCues, tsUrl, setParam } = ctx;
let passed = 0;
function check(name, fn) {
  fn();
  passed++;
  console.log('  ✓ ' + name);
}

check('YouTube json3 + aAppend 滚动合并', () => {
  const text = JSON.stringify({ events: [
    { tStartMs: 1000, dDurationMs: 1200, segs: [{ utf8: 'Hello ' }] },
    { tStartMs: 2200, dDurationMs: 1200, aAppend: 1, segs: [{ utf8: 'world.' }] },
  ] });
  const cues = parseCues(text, 'youtube_json3');
  assert.strictEqual(cues.length, 1);
  assert.ok(cues[0].text.includes('Hello'));
  assert.ok(cues[0].text.includes('world'));
});

check('timedtext XML 兜底', () => {
  const xml = '<transcript><text start="1.5" dur="2.0">おはよう</text>' +
              '<text start="3.6" dur="1.8">こんにちは</text></transcript>';
  assert.strictEqual(parseTimedtextXml(xml).length, 2);
  assert.strictEqual(parseCues(xml, 'youtube_json3').length, 2);
});

check('B 站字幕 JSON', () => {
  const cues = parseCues(JSON.stringify({ body: [
    { from: 1, to: 2.5, content: '你好' }, { from: 2.6, to: 4, content: '世界' },
  ] }), 'bilibili_json');
  assert.deepStrictEqual(cues.map((c) => c.text), ['你好', '世界']);
});

check('srt 解析', () => {
  const srt = '1\n00:00:01,200 --> 00:00:02,600\nHello\n\n2\n00:00:02,700 --> 00:00:04,000\nworld.\n';
  assert.strictEqual(parseCues(srt, 'auto').length, 2);
});

check('日语碎片合并成完整单句', () => {
  const cues = parseCues(JSON.stringify({ body: [
    { from: 1.0, to: 2.0, content: 'おはよう、' },
    { from: 2.1, to: 3.8, content: '今日はいい天気ですね。' },
    { from: 5.0, to: 6.5, content: '朝ごはんはもう食べましたか？' },
  ] }), 'bilibili_json');
  const out = segmentCues(cues);
  assert.ok(out.length >= 2);
  assert.ok(out[0].text.includes('おはよう') && out[0].text.includes('天気'));
  assert.ok(!out[0].text.includes('朝ごはん'));
  assert.strictEqual(out[0].text, 'おはよう、今日はいい天気ですね。');
});

check('英文碎片合并（CJK 不加空格 / 拉丁加空格）', () => {
  defaults.lang = 'en';
  const cues = [
    { start: 1, end: 2, text: 'I ' }, { start: 2.1, end: 3, text: 'think ' },
    { start: 3.1, end: 4.5, text: 'this is great.' }, { start: 6, end: 7.5, text: 'Really?' },
  ];
  const out = segmentCues(cues);
  assert.strictEqual(out.length, 2);
  assert.strictEqual(out[0].text, 'I think this is great.');
  defaults.lang = 'ja';
});

check('时间戳链接', () => {
  assert.ok(tsUrl('https://www.youtube.com/watch?v=abc', 12.4).includes('t=12s'));
  assert.ok(tsUrl('https://www.bilibili.com/video/BV123?p=2', 12.4).includes('t=12'));
});

check('URL 参数替换', () => {
  assert.ok(setParam('https://www.youtube.com/api/timedtext?v=a&fmt=vtt', 'fmt', 'json3')
    .includes('fmt=json3'));
});

async function checkAsync(name, fn) {
  await fn();
  passed++;
  console.log('  ✓ ' + name);
}

(async () => {
  await checkAsync('B站 URL 清洗推荐流参数并锁定分 P', async () => {
    const raw = 'https://www.bilibili.com/video/BV1ZT4b65Ezp/?trackid=web_pegasus_0&spm_id_from=333.1007&vd_source=abc';
    const u = ctx.videoUrl(raw, 'bilibili', 2);
    assert.ok(u.includes('p=2'));
    assert.ok(!u.includes('trackid'));
    assert.ok(!u.includes('vd_source'));
    assert.ok(!u.includes('spm_id_from'));
  });

  await checkAsync('YouTube URL 只保留 v 参数', async () => {
    const u = ctx.videoUrl('https://www.youtube.com/watch?v=jNQXAC9IVRw&t=42s&si=abc', 'youtube', 1);
    assert.ok(u.includes('v=jNQXAC9IVRw'));
    assert.ok(!u.includes('si='));
  });

  await checkAsync('B站按 bvid 重新解析 cid 与两个分 P', async () => {
    const view = { code: 0, data: {
      aid: 117563958759785, cid: 43355604729, title: '油管精读322',
      pages: [
        { page: 1, cid: 43355604729, part: '中英' },
        { page: 2, cid: 43355605731, part: '英文' }
      ]
    } };
    ctx.chrome.runtime.sendMessage = (msg, cb) => {
      if (msg.type === 'fetchText' && msg.url.includes('/x/web-interface/view')) {
        cb({ ok: true, status: 200, text: JSON.stringify(view) });
      } else cb({ ok: false, status: 0, error: 'unexpected' });
    };
    const meta = await ctx.fetchBiliMeta('BV1ZT4b65Ezp');
    assert.strictEqual(meta.pages.length, 2);
    assert.strictEqual(meta.pages[1].cid, 43355605731);
    assert.strictEqual(meta.pages[1].part, '英文');
  });

  await checkAsync('B站字幕走 WBI 签名接口 + 页面内请求', async () => {
    const called = [];
    ctx.chrome.runtime.sendMessage = (msg, cb) => {
      if (msg.type === 'wbiSign') {
        cb({ ok: true, url: 'https://api.bilibili.com/x/player/wbi/v2?bvid=BV1Fip16dEXL&cid=42738518131&wts=1&w_rid=abc' });
      } else if (msg.type === 'fetchInPage') {
        called.push(msg.url);
        cb({ ok: true, status: 200, text: JSON.stringify({ code: 0, data: { subtitle: {
          subtitles: [{ lan: 'ai-zh', lan_doc: '中文', ai_status: 1,
            subtitle_url: '//i0.hdslb.com/bfs/subtitle/a.json' }]
        } } }), error: '' });
      } else cb({ ok: false, status: 0, error: 'unexpected' });
    };
    const subs = await ctx.fetchBiliSubs('BV1Fip16dEXL', 42738518131);
    assert.strictEqual(subs.length, 1);
    assert.strictEqual(called.length, 1);
    assert.ok(called[0].includes('/x/player/wbi/v2'), '必须走带签名的 wbi/v2');
    assert.ok(called[0].includes('w_rid='));
    assert.ok(!called[0].includes('aid='), '不能再出现 aid 兜底');
    assert.ok(!called[0].includes('/x/player/v2?'), '不能再用无签名接口');
  });

  await checkAsync('牌组按视频名自动分层', async () => {
    assert.strictEqual(ctx.deckPrefix(), 'Subs2Anki');
    assert.ok(code.includes("deckPrefix() + '::'"), 'TSV 也应带子牌组名');
    assert.ok(code.includes('allowAI'), 'B 站 AI 字幕应有开关');
    assert.ok(code.includes('subtitleStable'), '应有字幕稳定性校验');
  });

  await checkAsync('YouTube 页面内同源抓取，多 fmt 候选', async () => {
    const json3 = JSON.stringify({ events: [
      { tStartMs: 0, dDurationMs: 1500, segs: [{ utf8: 'Hello ' }] },
      { tStartMs: 1600, dDurationMs: 1500, segs: [{ utf8: 'world.' }] }
    ] });
    ctx.chrome.runtime.sendMessage = (msg, cb) => {
      if (msg.type === 'fetchInPage') {
        cb(String(msg.url).includes('fmt=json3')
          ? { ok: true, status: 200, text: json3, error: '' }
          : { ok: true, status: 200, text: '', error: '' });
      } else cb({ ok: false, status: 0, text: '', error: 'unexpected' });
    };
    const cues = await ctx.fetchYoutubeTrack('https://www.youtube.com/api/timedtext?v=x&lang=en');
    assert.strictEqual(cues.length, 2);
    assert.ok(cues[0].text.includes('Hello'));
  });

  await checkAsync('页面残留字幕轨不再被直接使用', async () => {
    assert.ok(!code.includes('d.captionTracks.map'),
      'B站分支不应再把页面初始状态的字幕轨当真');
    assert.ok(code.includes('meta.pages.find'), 'B站应按分 P 选择 cid');
    assert.ok(code.includes('fetchInPage'), 'YouTube 应支持页面内同源请求');
    assert.ok(code.includes('wbiSign'), 'B 站应走 WBI 签名');
    assert.ok(code.includes('transcriptCues'), 'YouTube 应支持页面文字记录兜底');
  });

  console.log(`\n  ${passed} 项扩展逻辑校验通过\n`);
})().catch((e) => { console.error(e); process.exit(1); });
