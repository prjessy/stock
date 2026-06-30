// 주식 레이더 PWA 서비스워커 — 설치형 웹앱(홈화면/Play Store TWA) 요건 + 오프라인 셸.
// 정책: 외부(CDN)·API/시세는 건드리지 않음(항상 네트워크). 같은 출처 GET 만 네트워크 우선+캐시 폴백.
const CACHE = "stockradar-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // 외부(CDN/폰트/이미지)는 그대로
  if (url.pathname.startsWith("/api/")) return;       // 시세·API 는 항상 네트워크(SW 개입 X)
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req))                 // 오프라인이면 캐시
  );
});
