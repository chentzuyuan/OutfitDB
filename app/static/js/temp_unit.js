// ClosetMind — temperature unit helpers.
//
// The DB always stores Celsius. This module is the only place that
// converts to/from the user's display unit (°C or °F) so the rest of
// the app can treat numbers uniformly.
//
// Usage:
//   cm.formatTemp(c)           → "23°C" or "73°F"
//   cm.formatTempInt(c)        → "23"   or "73"   (no unit, e.g. for compact tables)
//   cm.toCelsius(displayVal)   → input -> stored value
//   cm.fromCelsius(c)          → stored -> display value
//   cm.tempUnit()              → "C" or "F"
//   cm.tempUnitSymbol()        → "°C" or "°F"
//
// Pages should listen for window 'cm-temp-unit-change' to re-render.
(function() {
    window.cm = window.cm || {};

    function readUnit() {
        try {
            return localStorage.getItem('cm_temp_unit') === 'F' ? 'F' : 'C';
        } catch (e) {
            return 'C';
        }
    }

    cm.tempUnit = readUnit;
    cm.tempUnitSymbol = () => readUnit() === 'F' ? '°F' : '°C';

    cm.fromCelsius = function (c) {
        if (c == null || isNaN(c)) return null;
        return readUnit() === 'F' ? (c * 9 / 5 + 32) : c;
    };

    cm.toCelsius = function (val, fromUnit) {
        if (val == null || isNaN(val)) return null;
        const u = fromUnit || readUnit();
        return u === 'F' ? ((val - 32) * 5 / 9) : val;
    };

    cm.formatTemp = function (celsius, opts) {
        opts = opts || {};
        if (celsius == null || isNaN(celsius)) return '—';
        const v = cm.fromCelsius(celsius);
        const fixed = opts.precision != null ? opts.precision : 0;
        const num = Number(v).toFixed(fixed);
        return opts.unitless ? num : `${num}${cm.tempUnitSymbol()}`;
    };

    cm.formatTempInt = function (celsius) {
        return cm.formatTemp(celsius, { precision: 0, unitless: true });
    };

    // Bootstrap: if localStorage doesn't have the unit yet, fetch from
    // server and broadcast the change so any already-rendered page can
    // re-render. Best-effort — failures leave default 'C'.
    (async () => {
        try {
            if (!localStorage.getItem('cm_temp_unit')) {
                const r = await fetch('/users/settings');
                if (!r.ok) return;
                const s = await r.json();
                const u = s.temp_unit === 'F' ? 'F' : 'C';
                localStorage.setItem('cm_temp_unit', u);
                if (u !== 'C') {
                    window.dispatchEvent(new CustomEvent('cm-temp-unit-change', { detail: u }));
                }
            }
        } catch (e) { /* offline / setup mode — fine, default to C */ }
    })();
})();
