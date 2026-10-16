// Checks the canvas-format renderer, then renders every page against the local data.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, DOMParser: dom.window.DOMParser,
  Node: dom.window.Node, Event: dom.window.Event, HTMLElement: dom.window.HTMLElement, SVGElement: dom.window.SVGElement });
if (!globalThis.navigator) globalThis.navigator = dom.window.navigator;

const { h, render } = await import('preact');
const { parseDC, makeComponent, interpolate } = await import('../src/dc/runtime.js');
const { PAGES, ROUTE_OF_FILE } = await import('../src/routes.js');

const page = (body, logic) => `<!doctype html><html><head><title>T</title></head><body><x-dc>${body}</x-dc>
<script type="text/x-dc" data-dc-script>class Component extends DCLogic { ${logic} }</script></body></html>`;
const mount = (src, extra = {}) => {
  const root = document.createElement('div');
  const ctx = { components: {}, url: u => u, fetch: () => new Promise(() => {}), ...extra };
  render(h(makeComponent(parseDC(src), ctx)), root);
  return root;
};

test('holes: dotted lookups, literals, raw values and interpolation', () => {
  const scope = { a: { b: 3 }, f: () => 1 };
  assert.equal(interpolate('{{a.b}}', scope), 3);
  assert.equal(typeof interpolate('{{ f }}', scope), 'function');
  assert.equal(interpolate('x {{a.b}} y {{missing.z}}', scope), 'x 3 y ');
  assert.equal(interpolate('{{true}}', scope), true);
});

test('sc-for, sc-if, events and templated SVG text render', () => {
  const root = mount(page(
    `<ul><sc-for list="{{xs}}" as="x"><li>{{x.n}}-{{$index}}</li></sc-for></ul>
     <sc-if value="{{show}}"><p id="p">shown</p></sc-if><sc-if value="{{hide}}"><p id="q">hidden</p></sc-if>
     <svg viewBox="0 0 10 10"><text x="1" y="{{y}}">{{label}}</text></svg>
     <button onClick="{{inc}}">{{n}}</button>`,
    `constructor(p) { super(p); this.state = { n: 0 }; }
     renderVals() { return { xs: [{ n: 'a' }, { n: 'b' }], show: true, hide: false, y: 5, label: 'tick', n: this.state.n,
       inc: () => this.setState({ n: this.state.n + 1 }) }; }`));
  assert.deepEqual([...root.querySelectorAll('li')].map(l => l.textContent), ['a-0', 'b-1']);
  assert.ok(root.querySelector('#p'));
  assert.equal(root.querySelector('#q'), null);
  const t = root.querySelector('text');
  assert.equal(t.textContent, 'tick');
  assert.equal(t.getAttribute('y'), '5');
  assert.equal(t.namespaceURI, 'http://www.w3.org/2000/svg');
});

test('dc-import passes props and links to pages become routes', () => {
  const child = makeComponent(parseDC(page('<span id="c">{{who}}</span>', 'renderVals() { return { who: this.props.active }; }')),
    { components: {}, url: u => u, fetch: () => {} });
  const root = mount(page('<dc-import name="Nav" active="sources" hint-size="1,1"></dc-import>', ''), { components: { Nav: child } });
  assert.equal(root.querySelector('#c').textContent, 'sources');
  assert.equal(ROUTE_OF_FILE['Sector.dc.html'], 'sub-districts');
});

// ---- every page, with the local data folder (skipped when the data is not here)
const DATA = new URL('../../data/dashboard/app_v1/', import.meta.url);
const PAGES_DIR = new URL('../src/pages/', import.meta.url);
test('every page renders with the real data and shows its labels', { skip: !existsSync(DATA) && 'no local data' }, async () => {
  const sources = Object.fromEntries(readdirSync(PAGES_DIR).map(f => [f.replace('.dc.html', ''), readFileSync(new URL(f, PAGES_DIR), 'utf8')]));
  const errors = [];
  const origError = console.error;
  console.error = (...a) => errors.push(a.join(' '));
  const url = u => (u.startsWith('@data/') ? new URL(u.slice(6), DATA).href : u);
  const ctx = { components: {}, url, fetch: async u => {
    const p = new URL(url(u));
    const body = readFileSync(p);
    return { ok: true, json: async () => JSON.parse(body), text: async () => body.toString() };
  } };
  for (const [name, src] of Object.entries(sources)) ctx.components[name] = makeComponent(parseDC(src), ctx);
  for (const p of PAGES) {
    const root = document.createElement('div');
    render(h(ctx.components[p.file.replace('.dc.html', '')]), root);
    await new Promise(r => setTimeout(r, 300));
    const text = root.textContent;
    assert.ok(!/\{\{/.test(root.innerHTML), `${p.route}: unrendered hole`);
    assert.ok(!/Loading/.test(text) || p.route === 'challenge', `${p.route}: still loading`);
    assert.ok(text.includes(p.label === 'The Challenge' ? 'The Challenge' : 'Rooftops'), `${p.route}: nav missing`);
    render(null, root);
  }
  console.error = origError;
  assert.deepEqual(errors, []);
});
