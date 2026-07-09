const CITIES = [
  { name: 'Tbilisi',     lat: 41.7151, lon: 44.8271, emoji: '🏙️' },
  { name: 'Batumi',      lat: 41.6168, lon: 41.6367, emoji: '🌊' },
  { name: 'Kazbegi',     lat: 42.6567, lon: 44.6428, emoji: '🏔️' },
  { name: 'Telavi',      lat: 41.9165, lon: 45.4747, emoji: '🍷' },
  { name: 'Borjomi',     lat: 41.8386, lon: 43.3934, emoji: '💧' },
  { name: 'Kutaisi',     lat: 42.2679, lon: 42.6946, emoji: '🦅' },
  { name: 'Mestia',      lat: 43.0466, lon: 42.7278, emoji: '🗻' },
  { name: 'Mtskheta',    lat: 41.8453, lon: 44.7182, emoji: '🛕' },
  { name: 'Gudauri',     lat: 42.4738, lon: 44.4776, emoji: '⛷️' },
  { name: 'Sighnaghi',   lat: 41.6194, lon: 45.9169, emoji: '🌹' },
  { name: 'Akhaltsikhe', lat: 41.6403, lon: 42.9856, emoji: '🏰' },
];

const API_KEY = process.env.OPENWEATHER_KEY;
const CACHE_TTL = 3600000; // 1 час

let cache = null;
let cacheTime = 0;

exports.handler = async function(event, context) {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    'Cache-Control': 'public, max-age=3600',
  };

  if (cache && Date.now() - cacheTime < CACHE_TTL) {
    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ data: cache, cached: true, age: Math.round((Date.now() - cacheTime) / 60000) + 'min' })
    };
  }

  try {
    const results = await Promise.all(
      CITIES.map(async function(city) {
        const url = 'https://api.openweathermap.org/data/2.5/forecast?lat=' + city.lat + '&lon=' + city.lon + '&appid=' + API_KEY + '&units=metric&cnt=40';
        const res = await fetch(url);
        const data = await res.json();

        if (data.cod !== '200') throw new Error('City ' + city.name + ': ' + data.message);

        var daily = [];
        var seen = {};

        for (var i = 0; i < data.list.length; i++) {
          var item = data.list[i];
          var date = new Date(item.dt * 1000);
          var day = date.toISOString().split('T')[0];
          if (!seen[day] && date.getUTCHours() >= 11 && date.getUTCHours() <= 14) {
            seen[day] = true;
            daily.push({
              date: day,
              temp_max: Math.round(item.main.temp_max),
              temp_min: Math.round(item.main.temp_min),
              temp: Math.round(item.main.temp),
              desc: item.weather[0].description,
              icon: item.weather[0].icon,
              wind: Math.round(item.wind.speed),
              humidity: item.main.humidity,
            });
            if (daily.length >= 5) break;
          }
        }

        return {
          name: city.name,
          emoji: city.emoji,
          current: {
            temp: Math.round(data.list[0].main.temp),
            feels: Math.round(data.list[0].main.feels_like),
            desc: data.list[0].weather[0].description,
            icon: data.list[0].weather[0].icon,
            humidity: data.list[0].main.humidity,
            wind: Math.round(data.list[0].wind.speed),
          },
          forecast: daily,
        };
      })
    );

    cache = results;
    cacheTime = Date.now();

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ data: results, cached: false, updated: new Date().toISOString() })
    };

  } catch (err) {
    if (cache) {
      return { statusCode: 200, headers, body: JSON.stringify({ data: cache, cached: true, error: err.message }) };
    }
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
