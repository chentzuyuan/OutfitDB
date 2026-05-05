// Register service worker for PWA installability + offline shell.
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/js/sw.js')
            .catch((err) => console.warn('SW register failed', err));
    });
}
