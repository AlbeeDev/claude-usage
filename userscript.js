// ==UserScript==
// @name         Claude usage reporter
// @namespace    autopilot
// @version      1.0
// @description  Pushes this account's plan usage to the local collector, from a real logged-in tab.
// @match        https://claude.ai/*
// @grant        GM_xmlhttpRequest
// @connect      usage-check-collector
// @run-at       document-idle
// ==/UserScript==

// Why a userscript: the request has to come from a genuine browser session, or
// Cloudflare challenges it. Same-origin fetch from the page inherits the real
// session and fingerprint, so there is nothing to detect. GM_xmlhttpRequest is
// used for the push because the page is HTTPS and the collector is plain HTTP
// on the docker network (normal fetch would be blocked as mixed content).

(function () {
  "use strict";

  const COLLECTOR = "http://usage-check-collector:8000/report";
  const EVERY_MS = 2 * 60 * 1000;

  async function readUsage() {
    const orgsRes = await fetch("/api/organizations", {
      headers: { Accept: "application/json" },
    });
    if (!orgsRes.ok) throw new Error("organizations " + orgsRes.status);
    const orgs = await orgsRes.json();

    const org = orgs.find((o) => (o.capabilities || []).includes("chat"));
    if (!org) throw new Error("no chat-capable organization");

    const res = await fetch(`/api/organizations/${org.uuid}/usage`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error("usage " + res.status);
    return await res.json();
  }

  function push(payload) {
    GM_xmlhttpRequest({
      method: "POST",
      url: COLLECTOR,
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify(payload),
    });
  }

  async function tick() {
    try {
      push(await readUsage());
    } catch (e) {
      // Reported so the consumer can tell "browser logged out" from "browser gone".
      push({ error: String(e) });
    }
  }

  tick();
  setInterval(tick, EVERY_MS);
})();
