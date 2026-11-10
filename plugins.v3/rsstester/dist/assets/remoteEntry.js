const currentImports = {};
const moduleMap = {
  './AppPage': () => __federation_import('./AppPage.js?v=1.0.6').then((module) => () => module.default),
  './Page': () => __federation_import('./AppPage.js?v=1.0.6').then((module) => () => module.default),
  './Config': () => __federation_import('./Config.js?v=1.0.6').then((module) => () => module.default),
};

async function __federation_import(name) {
  currentImports[name] ??= import(name);
  return currentImports[name];
}

const dynamicLoadingCss = () => {};

const get = (module) => {
  if (!moduleMap[module]) throw new Error('Can not find remote module ' + module);
  return moduleMap[module]();
};

const init = (shareScope) => {
  globalThis.__federation_shared__ = globalThis.__federation_shared__ || {};
  Object.entries(shareScope || {}).forEach(([key, value]) => {
    for (const [versionKey, versionValue] of Object.entries(value || {})) {
      const scope = versionValue.scope || 'default';
      globalThis.__federation_shared__[scope] = globalThis.__federation_shared__[scope] || {};
      const shared = globalThis.__federation_shared__[scope];
      (shared[key] = shared[key] || {})[versionKey] = versionValue;
    }
  });
};

export { dynamicLoadingCss, get, init };