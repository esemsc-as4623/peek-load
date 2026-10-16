// A small renderer for the design canvas's page format (.dc.html), so the pages
// drawn on the canvas run unchanged as a static Preact site.
//
// The format (see the canvas's format reference):
//   {{a.b}}                 dotted lookup into renderVals(); literals true/false/null/numbers
//   attr="{{x}}"            the raw value (number, function, array); attr="a {{x}} b" a string
//   <sc-for list as>        repeat children, with the item and $index in scope
//   <sc-if value>           render children when value is truthy
//   <dc-import name=…>      mount another page component, other attributes become props
//   <script data-dc-script> class Component extends DCLogic { renderVals() {…} }
import { h, Component, Fragment } from 'preact';

const HOLE = /\{\{\s*([^}]+?)\s*\}\}/g;
const WHOLE = /^\{\{\s*([^}]+?)\s*\}\}$/;

// ---- parsing ----------------------------------------------------------------

function toTree(node) {
  if (node.nodeType === 3) return { text: node.nodeValue };
  if (node.nodeType !== 1) return null;
  const attrs = [];
  for (const a of node.attributes) attrs.push([a.name, a.value]);
  const children = [];
  for (const c of node.childNodes) {
    const t = toTree(c);
    if (t) children.push(t);
  }
  return { tag: node.localName, attrs, children };
}

/** Parse one .dc.html source into its template tree, logic source and title. */
export function parseDC(source, DOMParserImpl = globalThis.DOMParser) {
  const doc = new DOMParserImpl().parseFromString(source, 'text/html');
  const root = doc.querySelector('x-dc');
  if (!root) throw new Error('no <x-dc> root');
  const script = doc.querySelector('script[data-dc-script]');
  const title = (doc.querySelector('title') || {}).textContent || '';
  return { tree: toTree(root), code: script ? script.textContent : 'class Component extends DCLogic {}', title };
}

// ---- holes ------------------------------------------------------------------

export function lookup(expr, scope) {
  if (expr === 'true') return true;
  if (expr === 'false') return false;
  if (expr === 'null') return null;
  if (/^-?\d+(\.\d+)?$/.test(expr)) return Number(expr);
  if (/^'.*'$|^".*"$/.test(expr)) return expr.slice(1, -1);
  let v = scope;
  for (const part of expr.split('.')) {
    if (v === null || v === undefined) return undefined;
    v = v[part];
  }
  return v;
}

function str(v) {
  return v === null || v === undefined || v === false ? '' : String(v);
}

export function interpolate(text, scope) {
  const whole = text.match(WHOLE);
  if (whole) return lookup(whole[1], scope);
  return text.replace(HOLE, (_, e) => str(lookup(e, scope)));
}

// ---- rendering --------------------------------------------------------------

const camel = s => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

function renderChildren(children, scope, ctx) {
  const out = [];
  for (const c of children) {
    const r = renderNode(c, scope, ctx);
    if (Array.isArray(r)) out.push(...r);
    else if (r !== null && r !== undefined && r !== '') out.push(r);
  }
  return out;
}

function attrValue(value, scope) {
  return value.indexOf('{{') >= 0 ? interpolate(value, scope) : value;
}

function renderNode(node, scope, ctx) {
  if (node.text !== undefined) {
    if (node.text.indexOf('{{') < 0) return node.text;
    const v = interpolate(node.text, scope);
    return str(v);
  }
  const { tag, attrs, children } = node;
  const get = name => {
    const a = attrs.find(x => x[0] === name);
    return a ? attrValue(a[1], scope) : undefined;
  };
  if (tag === 'helmet' || tag === 'script') return null;
  if (tag === 'x-dc') return renderChildren(children, scope, ctx);
  if (tag === 'sc-if') return get('value') ? renderChildren(children, scope, ctx) : null;
  if (tag === 'sc-for') {
    const list = get('list') || [];
    if (!Array.isArray(list)) throw new Error('sc-for list is not an array: ' + JSON.stringify(attrs) + ' -> ' + typeof list);
    const as = get('as') || 'item';
    return list.map((item, i) => h(Fragment, { key: i },
      renderChildren(children, Object.assign(Object.create(scope), { [as]: item, $index: i }), ctx)));
  }
  if (tag === 'dc-import') {
    const Child = ctx.components[get('name')];
    if (!Child) return null;
    const props = {};
    for (const [k, v] of attrs) {
      if (k === 'name' || k.startsWith('hint-')) continue;
      props[camel(k)] = attrValue(v, scope);
    }
    return h(Child, props);
  }
  const props = {};
  for (const [k, raw] of attrs) {
    let name = k, v = attrValue(raw, scope);
    if (name === 'class') name = 'className';
    else if (name === 'for') name = 'htmlFor';
    else if (name.startsWith('on')) {
      // live search: a text input's onChange fires on every keystroke, as in React
      if (name === 'onchange' && tag === 'input') name = 'oninput';
    } else if ((name === 'href' || name === 'src') && typeof v === 'string') {
      v = ctx.url(v);
    }
    if (v === undefined) continue;
    props[name] = v;
  }
  return h(tag, props, renderChildren(children, scope, ctx));
}

/**
 * Build the Preact component for one parsed page.
 * ctx.components: name -> component, for <dc-import>
 * ctx.url(u): rewrites links to other pages and '@data/' paths
 * ctx.fetch: the fetch the page's logic sees
 */
export function makeComponent(parsed, ctx) {
  const tree = parsed.tree;
  class DCLogic extends Component {
    renderVals() { return {}; }
    render() {
      const vals = this.renderVals() || {};
      const out = renderChildren(tree.children, vals, ctx);
      return out.length === 1 ? out[0] : h(Fragment, null, out);
    }
  }
  // the page's own logic, written for the canvas runtime
  const factory = new Function('DCLogic', 'fetch', parsed.code + '\nreturn Component;');
  return factory(DCLogic, ctx.fetch);
}
