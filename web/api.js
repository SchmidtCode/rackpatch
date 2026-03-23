export function parseApiError(text, status) {
  if (!text) {
    return `Request failed (${status})`;
  }
  try {
    const payload = JSON.parse(text);
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload.detail) && payload.detail.length) {
      return payload.detail.map((item) => item.msg || item.detail || "Request failed").join(", ");
    }
  } catch (_) {
    return text;
  }
  return text;
}

export function createApiClient(getToken) {
  return async function api(path, options = {}) {
    const token = getToken();
    const response = await fetch(path, {
      ...options,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(parseApiError(text, response.status));
    }
    return response.json();
  };
}
