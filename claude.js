export default async (request) => {
  if (request.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
  try {
    const body = await request.json();

    const upstream = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": process.env.ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01"
      },
      body: JSON.stringify(body)
    });

    // Если запросили стрим — отдаём поток как есть (text/event-stream).
    // Текст у пользователя появляется по мере написания = ощущается мгновенно.
    if (body && body.stream) {
      return new Response(upstream.body, {
        status: upstream.status,
        headers: {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
          "Access-Control-Allow-Origin": "*"
        }
      });
    }

    // Обычный режим (запасной): возвращаем JSON как раньше.
    const data = await upstream.json();
    return Response.json(data, { headers: { "Access-Control-Allow-Origin": "*" } });
  } catch (err) {
    return Response.json({ debug_error: err.message }, { headers: { "Access-Control-Allow-Origin": "*" } });
  }
};
