/* n8n panel element: a full-size iframe to the proxied n8n root.
 *
 * The iframe loads n8n's own SPA through Home Assistant's reverse
 * proxy (/api/n8n_panel/), so the user gets the real n8n UI — sign-in,
 * workflows, executions — inside Home Assistant. n8n's login happens
 * inside the iframe with the user's n8n credentials.
 *
 * Auth: an iframe load is a plain browser navigation and cannot send
 * an Authorization header, so the proxy cannot use HA's own auth
 * (it would 401 the iframe). Instead the panel first calls the
 * session endpoint with its HA token — hass.fetchWithAuth sends the
 * Bearer header — and the integration sets a session cookie that the
 * proxy validates on every request, including the ones n8n's SPA
 * makes afterwards. If the session call fails, the panel shows the
 * error instead of an empty iframe.
 */
class N8nPanel extends HTMLElement {
  constructor() {
    super();
    this._iframe = null;
    this._loading = false;
  }

  set hass(hass) {
    if (this._iframe || this._loading) return;
    this._loading = true;
    this.style.cssText = "display:block;width:100%;height:100%;";
    const showError = (message) => {
      this._loading = false;
      this.innerHTML = "";
      const div = document.createElement("div");
      div.style.cssText =
        "padding:24px;font-family:var(--primary-font-family,Roboto,sans-serif);color:var(--primary-text-color,#212121);";
      div.textContent = message;
      this.appendChild(div);
    };
    // fetchWithAuth sends the HA Bearer token; the session endpoint
    // (admin only) sets the cookie the proxy validates.
    hass
      .fetchWithAuth("/api/n8n_panel/__ha__/session", { method: "POST" })
      .then((resp) => {
        if (!resp.ok) {
          throw new Error(
            "Could not start the n8n panel session (HTTP " + resp.status + ")."
          );
        }
        const iframe = document.createElement("iframe");
        iframe.src = "/api/n8n_panel/";
        iframe.style.cssText =
          "border:0;width:100%;height:100%;display:block;background:#fff;";
        this.innerHTML = "";
        this.appendChild(iframe);
        this._iframe = iframe;
        this._loading = false;
      })
      .catch((err) => {
        showError(
          "n8n Panel: " + (err && err.message ? err.message : "could not start a session.") +
          " Reload the panel to try again."
        );
      });
  }
}

customElements.define("n8n-panel", N8nPanel);
