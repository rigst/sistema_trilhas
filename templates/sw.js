{% load static %}/* Service worker do Trilhas: instalável + offline básico.
   - /static/  → cache-first (arquivos versionados por hash, imutáveis).
   - navegação → network-first; offline, serve a última versão vista da página
     (tópicos já abertos funcionam sem internet) e, na falta, a página offline.
   - resto     → passa direto para a rede. */
const VERSAO = "v2";
const CACHE_ESTATICO = "trilhas-static-" + VERSAO;
const CACHE_PAGINAS = "trilhas-pages-" + VERSAO;
const OFFLINE_URL = "/offline/";

const PRECACHE = [
  OFFLINE_URL,
  "{% static 'css/app.css' %}",
  "{% static 'css/pygments.css' %}",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_ESTATICO)
      .then((c) => c.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  const manter = [CACHE_ESTATICO, CACHE_PAGINAS];
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !manter.includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // Estáticos versionados: cache-first (rápido e funciona offline).
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
        if (resp.ok) {
          const copia = resp.clone();
          caches.open(CACHE_ESTATICO).then((c) => c.put(req, copia));
        }
        return resp;
      }))
    );
    return;
  }

  // Navegações (páginas HTML): network-first, com fallback ao cache e à página offline.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp.ok) {
            const copia = resp.clone();
            caches.open(CACHE_PAGINAS).then((c) => c.put(req, copia));
          }
          return resp;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match(OFFLINE_URL)))
    );
  }
});
