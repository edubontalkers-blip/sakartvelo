export default async (request) => {
  if (request.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }
  try {
    const body = await request.json();
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": process.env.ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01"
      },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    return Response.json(data, {
      headers: { "Access-Control-Allow-Origin": "*" }
    });
  } catch (err) {
    return Response.json({ debug_error: err.message }, {
      headers: { "Access-Control-Allow-Origin": "*" }
    });
  }
};
