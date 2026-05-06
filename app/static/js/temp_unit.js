// OutfitDB — temperature unit helpers.
//
// The DB always stores Celsius. This module is the only place that
// converts to/from the user's display unit (°C or °F) so the rest of
// the app can treat numbers uniformly.
//
// Usage:
//   od.formatTemp(c)           → "23°C" or "73°F"
//   od.formatTempInt(c)        → "23"   or "73"   (no unit, e.g. for compact tables)
//   od.toCelsius(displayVal)   → input -> stored value
//   od.fromCelsius(c)          → stored -> display value
//   od.tempUnit()              → "C" or "F"
//   od.tempUnitSymbol()        → "°C" or "°F"
//
// Pages should listen for window 'od-temp-unit-change' to re-render.
//
// Backwards-compat: pre-0.2.0 builds used `window.cm` and the localStorage
// key `cm_temp_unit` / event `cm-temp-unit-change`. We expose both name
// spaces and migrate any old localStorage value on first load so existing
// users don't reset to °C.
(function() {
    const STORAGE_KEY = 'od_temp_unit';
    const LEGACY_STORAGE_KEY = 'cm_temp_unit';
    const EVENT_NAME = 'od-temp-unit-change';
    const LEGACY_EVENT_NAME = 'cm-temp-unit-change';

    // One-time migration: copy old localStorage key to new, then remove old.
    try {
        const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
        if (legacy != null && localStorage.getItem(STORAGE_KEY) == null) {
            localStorage.setItem(STORAGE_KEY, legacy);
        }
        if (legacy != null) {
            localStorage.removeItem(LEGACY_STORAGE_KEY);
        }
    } catch (e) { /* private mode — ignore */ }

    const od = window.od || {};
    window.od = od;
    // Keep window.cm as an alias so any third-party page snippets still work.
    window.cm = od;

    function readUnit() {
        try {
            return localStorage.getItem(STORAGE_KEY) === 'F' ? 'F' : 'C';
        } catch (e) {
            return 'C';
        }
    }

    function emitChange(unit) {
        // Fire both new + legacy event names so pre-rename listeners
        // attached on imported pages keep working.
        window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: unit }));
        window.dispatchEvent(new CustomEvent(LEGACY_EVENT_NAME, { detail: unit }));
    }

    od.tempUnit = readUnit;
    od.tempUnitSymbol = () => readUnit() === 'F' ? '°F' : '°C';

    od.fromCelsius = function (c) {
        if (c == null || isNaN(c)) return null;
        return readUnit() === 'F' ? (c * 9 / 5 + 32) : c;
    };

    od.toCelsius = function (val, fromUnit) {
        if (val == null || isNaN(val)) return null;
        const u = fromUnit || readUnit();
        return u === 'F' ? ((val - 32) * 5 / 9) : val;
    };

    od.formatTemp = function (celsius, opts) {
        opts = opts || {};
        if (celsius == null || isNaN(celsius)) return '—';
        const v = od.fromCelsius(celsius);
        const fixed = opts.precision != null ? opts.precision : 0;
        const num = Number(v).toFixed(fixed);
        return opts.unitless ? num : `${num}${od.tempUnitSymbol()}`;
    };

    od.formatTempInt = function (celsius) {
        return od.formatTemp(celsius, { precision: 0, unitless: true });
    };

    // Bootstrap: if localStorage doesn't have the unit yet, fetch from
    // server and broadcast the change so any already-rendered page can
    // re-render. Best-effort — failures leave default 'C'.
    (async () => {
        try {
            if (!localStorage.getItem(STORAGE_KEY)) {
                const r = await fetch('/users/settings');
                if (!r.ok) return;
                const s = await r.json();
                const u = s.temp_unit === 'F' ? 'F' : 'C';
                localStorage.setItem(STORAGE_KEY, u);
                if (u !== 'C') {
                    emitChange(u);
                }
            }
        } catch (e) { /* offline / setup mode — fine, default to C */ }
    })();
})();
