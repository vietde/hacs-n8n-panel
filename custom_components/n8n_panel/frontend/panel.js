/* n8n panel element: a full-size iframe to the proxied n8n root.
 *
 * The iframe loads n8n's own SPA through Home Assistant's reverse
 * proxy (/api/n8n_panel/), so the user gets the real n8n UI — sign-in,
 * workflows, executions — inside Home Assistant. n8n's login happens
 * inside the iframe with the user's n8n credentials; no HA token is
 * passed in, because the proxy routes already require a logged-in HA
 * user (the browser sends its HA session cookie automatically).
 */
class N8nPanel extends HTMLElement {
  constructor() {
    super();
    this._iframe = null;
  }

  set hass(hass) {
    if (this._iframe) return;
    const iframe = document.createElement("iframe");
    iframe.src = "/api/n8n_panel/";
    iframe.style.cssText =
      "border:0;width:100%;height:100%;display:block;background:#fff;";
    this.style.cssText = "display:block;width:100%;height:100%;";
    this.appendChild(iframe);
    this._iframe = iframe;
  }
}

customElements.define("n8n-panel", N8nPanel);
