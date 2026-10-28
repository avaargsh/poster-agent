import source from './workbench.js?raw';

export function initializeDevelopment() {
    const script = document.createElement('script');
    script.textContent = `${source}\ndocument.documentElement.dataset.ready='true';\ndocument.getElementById('root').inert=false;`;
    document.body.append(script);
}
