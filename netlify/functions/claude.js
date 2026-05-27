exports.handler = async function(event) {
  if (event.httpMethod !== "POST") {
    return { statusCode: 405, body: "Method Not Allowed" };
  }
  try {
    const body = JSON.parse(event.body);
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": process.env.ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01"
      },
      body: JSON.stringify(body)
    });
    const text = await response.text();
    return {
      statusCode: 200,
      headers: { "Access-Control-Allow-Origin": "*" },
      body: text
    };
  } catch (err) {
    return {
      statusCode: 200,
      headers: { "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify({ debug_error: err.message })
    };
  }
};
