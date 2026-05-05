// Client-side weather fetcher. Calls Open-Meteo directly from the browser
// so the backend never proxies / consumes API quota.
const WMO_TO_ENUM = {
    0: 'sunny',
    1: 'cloudy', 2: 'cloudy', 3: 'cloudy', 45: 'cloudy', 48: 'cloudy',
    51: 'rainy', 53: 'rainy', 55: 'rainy', 56: 'rainy', 57: 'rainy',
    61: 'rainy', 63: 'rainy', 65: 'rainy', 66: 'rainy', 67: 'rainy',
    80: 'rainy', 81: 'rainy', 82: 'rainy', 95: 'rainy', 96: 'rainy', 99: 'rainy',
    71: 'snowy', 73: 'snowy', 75: 'snowy', 77: 'snowy', 85: 'snowy', 86: 'snowy',
};

window.fetchWeather = async function(lat, lon, dateISO) {
    const d = dateISO.slice(0, 10);
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
                `&daily=temperature_2m_max,temperature_2m_min,weather_code,wind_speed_10m_max` +
                `&current=temperature_2m&timezone=auto&start_date=${d}&end_date=${d}`;
    try {
        const r = await fetch(url);
        if (!r.ok) return null;
        const j = await r.json();
        const daily = j.daily;
        if (!daily || !daily.temperature_2m_max || daily.temperature_2m_max[0] == null) return null;
        const code = daily.weather_code[0];
        const wind = daily.wind_speed_10m_max[0] || 0;
        let weather = WMO_TO_ENUM[code] || 'cloudy';
        if (wind >= 30 && weather === 'sunny') weather = 'windy';
        return {
            temperature: j.current ? j.current.temperature_2m : daily.temperature_2m_max[0],
            temperature_high: daily.temperature_2m_max[0],
            temperature_low: daily.temperature_2m_min[0],
            weather,
        };
    } catch (e) {
        console.warn('fetchWeather failed', e);
        return null;
    }
};

window.getUserSettings = async function() {
    try {
        const r = await fetch('/users/settings');
        if (!r.ok) return null;
        return await r.json();
    } catch { return null; }
};
