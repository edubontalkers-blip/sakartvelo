const { getStore } = require('@netlify/blobs');

// ══════════════════════════════════════
// ФУНКЦИЯ ПРОВЕРКИ СТАТУСА ЗАДАЧИ
// ══════════════════════════════════════
// Быстрая функция (доли секунды) — сайт вызывает её каждые ~2.5 секунды,
// пока planner-background.js генерирует маршрут в фоне.
// Отвечает: {status:'pending'} | {status:'done', route:{...}} | {status:'error', message:'...'}
// ══════════════════════════════════════

exports.handler = async (event) => {
  const jobId = (event.queryStringParameters && event.queryStringParameters.jobId) || '';

  if (!jobId) {
    return {
      statusCode: 400,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'error', message: 'Missing jobId' })
    };
  }

  try {
    const store = getStore('planner-jobs');
    const job = await store.get(jobId, { type: 'json' });

    if (!job) {
      // Задача ещё не успела записать свой статус (или jobId неверный) —
      // считаем это "в процессе", чтобы клиент просто продолжил ждать
      // ещё немного, а не сразу показывал ошибку.
      return {
        statusCode: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'pending' })
      };
    }

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(job)
    };

  } catch (e) {
    console.error('Status check error:', e.message);
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'pending' })
    };
  }
};
