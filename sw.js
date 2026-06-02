// Sakartvelo.ai Service Worker v1.0
// Caches key assets for offline use

const CACHE = 'sak-v1';
const OFFLINE_URL = '/';

// Assets to cache immediately
const PRECACHE = [
  '/',
  '/index.html',
  '/manifest.json'
];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(cache) {
      return cache.addAll(PRECACHE);
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE; })
            .map(function(k) { return caches.delete(k); })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', function(e) {
  // Only handle GET requests
  if (e.request.method !== 'GET') return;
  
  // Skip Netlify functions and external APIs
  var url = e.request.url;
  if (url.includes('/.netlify/') || 
      url.includes('api.mymemory') || 
      url.includes('api.anthropic') ||
      url.includes('translate.google') ||
      url.includes('fonts.googleapis')) {
    return;
  }

  e.respondWith(
    caches.match(e.request).then(function(cached) {
      if (cached) return cached;
      
      return fetch(e.request).then(function(response) {
        // Cache successful HTML and JS responses
        if (response && response.status === 200) {
          var type = response.headers.get('content-type') || '';
          if (type.includes('html') || type.includes('javascript') || type.includes('css')) {
            var clone = response.clone();
            caches.open(CACHE).then(function(cache) {
              cache.put(e.request, clone);
            });
          }
        }
        return response;
      }).catch(function() {
        // Offline fallback
        return caches.match(OFFLINE_URL);
      });
    })
  );
});
