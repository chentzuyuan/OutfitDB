// Temperature unit helpers.
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
// Pages should listen for window '<EVENT_PREFIX>temp-unit-change' to re-render.
// The namespace + storage prefix come from window.OD_BRAND (set by base.html
// from app/branding.py) so a future brand rename only needs to touch
// branding.py — this file picks up the new identifiers on next load.
//
// Backwards-compat: every legacy brand prefix listed in OD_BRAND
// (e.g. 'cm_' for the pre-0.2.0 ClosetMind name) gets mirror-migrated:
// old localStorage values are copied to the canonical key, the legacy
// key is removed, and BOTH the new + legacy custom events are fired
// when the unit changes so any page snippet still listening on the old
// name keeps working.
(function() {
    const BRAND = window.OD_BRAND || {};
    const NAMESPACE = BRAND.namespace || 'od';
    const LS_PREFIX = BRAND.lsPrefix || 'od_';
    const EV_PREFIX = BRAND.eventPrefix || 'od-';
    const LEGACY_LS_PREFIXES = BRAND.legacyLsPrefixes || ['cm_'];
    const LEGACY_EV_PREFIXES = BRAND.legacyEventPrefixes || ['cm-'];

    const STORAGE_KEY = LS_PREFIX + 'temp_unit';
    const EVENT_NAME = EV_PREFIX + 'temp-unit-change';

    // One-time migration: copy any legacy *_temp_unit value into the
    // canonical key, then drop the old one. Walks every previous
    // prefix so a user upgrading through multiple renames doesn't
    // lose their °F preference.
    try {
        for (const p of LEGACY_LS_PREFIXES) {
            const legacyKey = p + 'temp_unit';
            const legacy = localStorage.getItem(legacyKey);
            if (legacy != null && localStorage.getItem(STORAGE_KEY) == null) {
                localStorage.setItem(STORAGE_KEY, legacy);
            }
            if (legacy != null) {
                localStorage.removeItem(legacyKey);
            }
        }
    } catch (e) { /* private mode — ignore */ }

    const ns = window[NAMESPACE] || {};
    window[NAMESPACE] = ns;
    // Mirror onto every legacy namespace so old page snippets keep working.
    // (Currently legacyLsPrefixes is the only "previous brand" hint we
    // have; the namespace itself is hardcoded as window.cm because that
    // was the only one used pre-0.2.0.)
    if (NAMESPACE !== 'cm' && !window.cm) window.cm = ns;

    function readUnit() {
        try {
            return localStorage.getItem(STORAGE_KEY) === 'F' ? 'F' : 'C';
        } catch (e) {
            return 'C';
        }
    }

    function emitChange(unit) {
        // Fire the canonical event plus every legacy alias.
        window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: unit }));
        for (const p of LEGACY_EV_PREFIXES) {
            window.dispatchEvent(new CustomEvent(p + 'temp-unit-change', { detail: unit }));
        }
    }

    ns.tempUnit = readUnit;
    ns.tempUnitSymbol = () => readUnit() === 'F' ? '°F' : '°C';

    ns.fromCelsius = function (c) {
        if (c == null || isNaN(c)) return null;
        return readUnit() === 'F' ? (c * 9 / 5 + 32) : c;
    };

    ns.toCelsius = function (val, fromUnit) {
        if (val == null || isNaN(val)) return null;
        const u = fromUnit || readUnit();
        return u === 'F' ? ((val - 32) * 5 / 9) : val;
    };

    ns.formatTemp = function (celsius, opts) {
        opts = opts || {};
        if (celsius == null || isNaN(celsius)) return '—';
        const v = ns.fromCelsius(celsius);
        const fixed = opts.precision != null ? opts.precision : 0;
        const num = Number(v).toFixed(fixed);
        return opts.unitless ? num : `${num}${ns.tempUnitSymbol()}`;
    };

    ns.formatTempInt = function (celsius) {
        return ns.formatTemp(celsius, { precision: 0, unitless: true });
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
