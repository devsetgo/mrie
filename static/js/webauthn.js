// Passkey (WebAuthn) login/registration helpers.
// The browser's WebAuthn API works in raw bytes (ArrayBuffer/Uint8Array);
// the server exchanges base64url strings. These helpers convert between them.

function base64urlToBuffer(base64url) {
  const padded = base64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(
    base64url.length + ((4 - (base64url.length % 4)) % 4), "="
  );
  const raw = atob(padded);
  const buffer = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    buffer[i] = raw.charCodeAt(i);
  }
  return buffer.buffer;
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function setStatus(el, message, isError) {
  if (!el) return;
  el.textContent = message;
  el.className = isError ? "text-danger" : "text-muted";
}

// The app's global HTTP exception handler renders an HTML error page for
// ordinary page navigations; it only returns plain JSON (which these calls
// need) when the request sends this Accept header - see
// http_exception_handler in src/app_routes.py.
const JSON_ACCEPT_HEADERS = { Accept: "application/json" };

// Reads a fetch Response as JSON, but reads it as text first so a non-JSON
// body (an HTML error/redirect page, a proxy error, stale-cache weirdness)
// can be logged in full instead of surfacing only a generic
// "Unexpected token '<'" parse error with no way to tell what actually came
// back. Logs everything relevant to console under [webauthn] so a failure
// here can be diagnosed from devtools without reproducing it live.
async function readJson(resp, label) {
  const text = await resp.text();
  try {
    return JSON.parse(text);
  } catch (parseErr) {
    console.error(`[webauthn] ${label}: response was not valid JSON`, {
      status: resp.status,
      statusText: resp.statusText,
      url: resp.url,
      redirected: resp.redirected,
      contentType: resp.headers.get("content-type"),
      bodyPreview: text.slice(0, 500),
      parseError: String(parseErr),
    });
    throw new Error(
      `${label}: server returned a non-JSON response (status ${resp.status}` +
        `${resp.redirected ? ", after a redirect" : ""}). See browser console for the raw body.`
    );
  }
}

async function passkeyLogin(statusEl) {
  try {
    setStatus(statusEl, "Requesting challenge...");
    const optionsResp = await fetch("/users/login/options", { headers: JSON_ACCEPT_HEADERS });
    const options = await readJson(optionsResp, "login/options");
    if (!optionsResp.ok) throw new Error(options.detail || "Could not start login");

    options.challenge = base64urlToBuffer(options.challenge);
    if (options.allowCredentials) {
      options.allowCredentials = options.allowCredentials.map((cred) => ({
        ...cred,
        id: base64urlToBuffer(cred.id),
      }));
    }

    setStatus(statusEl, "Waiting for passkey...");
    const assertion = await navigator.credentials.get({ publicKey: options });

    const payload = {
      id: assertion.id,
      rawId: bufferToBase64url(assertion.rawId),
      type: assertion.type,
      response: {
        clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
        authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
        signature: bufferToBase64url(assertion.response.signature),
        userHandle: assertion.response.userHandle
          ? bufferToBase64url(assertion.response.userHandle)
          : null,
      },
    };

    setStatus(statusEl, "Verifying...");
    const verifyResp = await fetch("/users/login/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...JSON_ACCEPT_HEADERS },
      body: JSON.stringify(payload),
    });
    const result = await readJson(verifyResp, "login/verify");
    if (!verifyResp.ok) throw new Error(result.detail || "Passkey login failed");
    window.location.href = result.redirect || "/notes";
  } catch (err) {
    setStatus(statusEl, err.message || "Login failed", true);
  }
}

async function passkeyRegister(statusEl, deviceName, bootstrapToken) {
  try {
    const tokenHeaders = bootstrapToken ? { "X-Bootstrap-Token": bootstrapToken } : {};

    setStatus(statusEl, "Requesting challenge...");
    const optionsResp = await fetch("/users/register/options", {
      headers: { ...JSON_ACCEPT_HEADERS, ...tokenHeaders },
    });
    const options = await readJson(optionsResp, "register/options");
    if (!optionsResp.ok) throw new Error(options.detail || "Registration is not available");

    options.challenge = base64urlToBuffer(options.challenge);
    options.user.id = base64urlToBuffer(options.user.id);
    if (options.excludeCredentials) {
      options.excludeCredentials = options.excludeCredentials.map((cred) => ({
        ...cred,
        id: base64urlToBuffer(cred.id),
      }));
    }

    setStatus(statusEl, "Waiting for passkey...");
    const credential = await navigator.credentials.create({ publicKey: options });

    const payload = {
      id: credential.id,
      rawId: bufferToBase64url(credential.rawId),
      type: credential.type,
      response: {
        clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
        attestationObject: bufferToBase64url(credential.response.attestationObject),
        transports: credential.response.getTransports
          ? credential.response.getTransports()
          : [],
      },
    };

    setStatus(statusEl, "Verifying...");
    const query = deviceName ? `?device_name=${encodeURIComponent(deviceName)}` : "";
    const verifyResp = await fetch(`/users/register/verify${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...JSON_ACCEPT_HEADERS, ...tokenHeaders },
      body: JSON.stringify(payload),
    });
    const result = await readJson(verifyResp, "register/verify");
    if (!verifyResp.ok) throw new Error(result.detail || "Passkey registration failed");
    setStatus(statusEl, "Passkey registered. You can log in now.");
  } catch (err) {
    setStatus(statusEl, err.message || "Registration failed", true);
  }
}
