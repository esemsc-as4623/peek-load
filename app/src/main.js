// Entry: loads the canvas pages, wires them to the data host and a hash router.
import { h, render, Component } from 'preact';
import { parseDC, makeComponent } from './dc/runtime.js';
import { PAGES, ROUTE_OF_FILE, DEFAULT_ROUTE } from './routes.js';

// every page file, bundled as text at build time
const SOURCES = import.meta.glob('./pages/*.dc.html', { query: '?raw', import: 'default', eager: true });

// where the data lives: same server by default, e.g. https://example.org/data/v1
const DATA_BASE = (import.meta.env.VITE_DATA_BASE || '/data/v1').replace(/\/$/, '');

const url = u => {
  if (u.startsWith('@data/')) return DATA_BASE + '/' + u.slice(6);
  const page = u.match(/^([\w.-]+\.dc\.html)(#.*)?$/);
  if (page && ROUTE_OF_FILE[page[1]]) return '#/' + ROUTE_OF_FILE[page[1]];
  return u;
};
const dataFetch = (u, opts) => fetch(url(u), opts);

const components = {};
const ctx = { components, url, fetch: dataFetch };
for (const [path, source] of Object.entries(SOURCES)) {
  const name = path.match(/([\w-]+)\.dc\.html$/)[1];
  const parsed = parseDC(source);
  components[name] = makeComponent(parsed, ctx);
  components[name].pageTitle = parsed.title;
}

const routeNow = () => {
  const r = location.hash.replace(/^#\/?/, '').split(/[?#]/)[0];
  return PAGES.find(p => p.route === r) ? r : DEFAULT_ROUTE;
};

class App extends Component {
  constructor() {
    super();
    this.state = { route: routeNow() };
    this.onHash = () => { this.setState({ route: routeNow() }); window.scrollTo(0, 0); };
  }
  componentDidMount() { window.addEventListener('hashchange', this.onHash); }
  componentWillUnmount() { window.removeEventListener('hashchange', this.onHash); }
  render() {
    const page = PAGES.find(p => p.route === this.state.route);
    const Page = components[page.file.replace('.dc.html', '')];
    document.title = page.label + ' · Rooftops → Load Curves';
    return h(Page, { key: page.route });
  }
}

render(h(App), document.getElementById('app'));
