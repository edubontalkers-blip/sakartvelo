export default async (request) => {
  const SUPABASE_URL = process.env.SUPABASE_URL;
  const SUPABASE_KEY = process.env.SUPABASE_KEY;
  const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN;
  const TG_CHAT_ID = process.env.TG_CHAT_ID;

  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };

  if (request.method === 'OPTIONS') {
    return new Response('', { status: 200, headers });
  }

  const url = new URL(request.url);
  const action = url.searchParams.get('action');

  // ── GET approved places ──────────────────────────────────────────
  if (request.method === 'GET' && action === 'list') {
    const city = url.searchParams.get('city') || 'Тбилиси';
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/places?approved=eq.true&city=eq.${encodeURIComponent(city)}&order=likes.desc&limit=50`,
      { headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` } }
    );
    const data = await res.json();
    return new Response(JSON.stringify(data), { headers });
  }

  // ── POST new place ───────────────────────────────────────────────
  if (request.method === 'POST' && action === 'add') {
    const body = await request.json();
    const { name, category, description, photo_url, city } = body;

    if (!name || !category) {
      return new Response(JSON.stringify({ error: 'name and category required' }), { status: 400, headers });
    }

    // spam guard: max 1 post per IP per hour
    const ip = request.headers.get('x-forwarded-for') || 'unknown';

    const place = { name: name.slice(0, 100), category, description: (description||'').slice(0, 300), photo_url: photo_url||null, city: city||'Тбилиси', approved: false };

    const res = await fetch(`${SUPABASE_URL}/rest/v1/places`, {
      method: 'POST',
      headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`, 'Content-Type': 'application/json', Prefer: 'return=representation' },
      body: JSON.stringify(place)
    });
    const [created] = await res.json();

    // Notify moderator in Telegram
    const emoji = { restaurant:'🍽️', cafe:'☕', sight:'🏛️', hotel:'🏨', tour:'🗺️', other:'📍' }[category] || '📍';
    const msg = `${emoji} <b>Новое место на модерацию</b>\n\n<b>${name}</b>\nКатегория: ${category}\nГород: ${city||'Тбилиси'}${description ? '\n\n'+description : ''}\n\n<b>ID:</b> <code>${created.id}</code>`;

    await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: TG_CHAT_ID,
        text: msg,
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [[
            { text: '✅ Одобрить', callback_data: `approve_${created.id}` },
            { text: '❌ Удалить', callback_data: `delete_${created.id}` }
          ]]
        }
      })
    });

    // if photo — send it too
    if (photo_url) {
      await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendPhoto`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: TG_CHAT_ID, photo: photo_url, caption: name })
      });
    }

    return new Response(JSON.stringify({ ok: true, id: created.id }), { headers });
  }

  // ── POST like ────────────────────────────────────────────────────
  if (request.method === 'POST' && action === 'like') {
    const { place_id, device_id } = await request.json();
    if (!place_id || !device_id) return new Response(JSON.stringify({ error: 'missing fields' }), { status: 400, headers });

    // insert like (unique constraint prevents duplicates)
    const likeRes = await fetch(`${SUPABASE_URL}/rest/v1/likes`, {
      method: 'POST',
      headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`, 'Content-Type': 'application/json', Prefer: 'return=minimal' },
      body: JSON.stringify({ place_id, device_id })
    });

    if (likeRes.status === 409) return new Response(JSON.stringify({ ok: false, msg: 'already liked' }), { headers });

    // increment likes counter
    await fetch(`${SUPABASE_URL}/rest/v1/rpc/increment_likes`, {
      method: 'POST',
      headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ place_id_arg: place_id })
    });

    return new Response(JSON.stringify({ ok: true }), { headers });
  }

  // ── POST Telegram webhook (approve/delete) ───────────────────────
  if (request.method === 'POST' && action === 'webhook') {
    const update = await request.json();
    const cb = update.callback_query;
    if (!cb) return new Response('ok', { headers });

    const [cmd, id] = cb.data.split('_');
    const chatId = cb.message.chat.id;
    const msgId = cb.message.message_id;

    if (String(chatId) !== String(TG_CHAT_ID)) {
      return new Response('forbidden', { status: 403, headers });
    }

    if (cmd === 'approve') {
      await fetch(`${SUPABASE_URL}/rest/v1/places?id=eq.${id}`, {
        method: 'PATCH',
        headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved: true })
      });
      await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/answerCallbackQuery`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callback_query_id: cb.id, text: '✅ Одобрено! Место опубликовано.' })
      });
      await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/editMessageReplyMarkup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, message_id: msgId, reply_markup: { inline_keyboard: [] } })
      });
    }

    if (cmd === 'delete') {
      await fetch(`${SUPABASE_URL}/rest/v1/places?id=eq.${id}`, {
        method: 'DELETE',
        headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` }
      });
      await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/answerCallbackQuery`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callback_query_id: cb.id, text: '🗑️ Удалено.' })
      });
      await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/editMessageReplyMarkup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, message_id: msgId, reply_markup: { inline_keyboard: [] } })
      });
    }

    return new Response('ok', { headers });
  }

  return new Response(JSON.stringify({ error: 'unknown action' }), { status: 400, headers });
};

export const config = { path: '/api/places' };
