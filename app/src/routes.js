// The site's pages, in navigation order. `file` is the canvas page in src/pages/.
export const PAGES = [
  { route: 'challenge', file: 'About.dc.html', label: 'The Challenge' },
  { route: 'profiles', file: 'Profiles.dc.html', label: 'Load profiles' },
  { route: 'overview', file: 'Main.dc.html', label: 'Overview' },
  { route: 'sub-districts', file: 'Sector.dc.html', label: 'Sub-district deep dive' },
  { route: 'climate', file: 'Climate.dc.html', label: 'Climate & cooling' },
  { route: 'productive-use', file: 'Productive.dc.html', label: 'Productive use' },
  { route: 'workflow', file: 'Workflow.dc.html', label: 'Workflow in depth' },
  { route: 'sources', file: 'Sources.dc.html', label: 'Sources & citations' },
  { route: 'validation', file: 'Validation.dc.html', label: 'Validation & quality' }
];
export const DEFAULT_ROUTE = 'challenge';
export const ROUTE_OF_FILE = Object.fromEntries(PAGES.map(p => [p.file, p.route]));
