(() => {
  "use strict";
  const POLL_MS = 5000;
  const strings = JSON.parse(document.getElementById("i18n").textContent);
  const locale = document.body.dataset.locale;
  const live = document.querySelector("[data-live]");
  const flash = document.querySelector("[data-flash]");
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
  const absolute = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const format = (template, params) => template.replace(/\{(\w+)\}/g, (_, key) => (key in params ? params[key] : ""));
  let lastUpdate = Date.now();
  let fingerprint = null;
  let timer = null;
  let failed = false;

  const localizeTimes = (root) => {
    root.querySelectorAll("time[datetime]").forEach((node) => {
      const moment = new Date(node.getAttribute("datetime"));
      if (Number.isNaN(moment.getTime())) return;
      node.textContent = absolute.format(moment);
      node.title = moment.toISOString();
    });
  };

  const ago = () => {
    const seconds = Math.round((lastUpdate - Date.now()) / 1000);
    return Math.abs(seconds) < 60 ? relative.format(seconds, "second") : relative.format(Math.round(seconds / 60), "minute");
  };

  const renderStatus = () => {
    if (!live) return;
    if (failed) {
      live.dataset.state = "error";
      live.textContent = format(strings.offline, { time: absolute.format(new Date(lastUpdate)), seconds: POLL_MS / 1000 });
    } else {
      delete live.dataset.state;
      live.textContent = format(document.hidden ? strings.paused : strings.updated, { ago: ago() });
    }
  };

  const fingerprintOf = (data) => JSON.stringify([data.issues, data.acu.committed, data.acu.ceiling, data.upstream_sync, data.automation_rate, data.median_time_to_pr_s,
    data.issues_detail.map((issue) => [
      issue.number, issue.title, issue.state, issue.route, issue.cves, issue.caveats, issue.last_error, issue.pr_url, issue.updated_at,
      issue.stage_track.map((stage) => [stage.state, stage.ended_at]),
    ]),
    data.sessions.map((session) => [session.session_id, session.status, session.finished, session.acus_consumed]), (data.acu.reservations || []).length]);

  const swapRegions = async () => {
    const response = await fetch(location.pathname + location.search, { headers: { Accept: "text/html" }, credentials: "same-origin" });
    if (!response.ok) throw new Error(String(response.status));
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    document.querySelectorAll("[data-region]").forEach((region) => {
      const next = doc.querySelector(`[data-region="${region.dataset.region}"]`);
      if (!next || region.contains(document.activeElement) && document.activeElement !== document.body) return;
      const open = new Set([...region.querySelectorAll("details[open][id]")].map((node) => node.id));
      region.replaceChildren(...next.childNodes);
      open.forEach((id) => document.getElementById(id)?.setAttribute("open", ""));
      localizeTimes(region);
    });
  };

  const poll = async () => {
    timer = null;
    if (document.hidden) return;
    try {
      const response = await fetch("/metrics.json", { headers: { Accept: "application/json" }, credentials: "same-origin" });
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      const next = fingerprintOf(data);
      if (fingerprint === null || next !== fingerprint) await swapRegions();
      fingerprint = next;
      lastUpdate = Date.now();
      failed = false;
    } catch (error) {
      failed = true;
    }
    renderStatus();
    schedule();
  };

  const schedule = () => {
    if (timer === null && !document.hidden) timer = setTimeout(poll, POLL_MS);
  };

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(timer);
      timer = null;
      renderStatus();
    } else {
      poll();
    }
  });

  const sweep = document.querySelector('[data-action="sweep"]');
  if (sweep) {
    sweep.hidden = false;
    sweep.addEventListener("click", async () => {
      if (sweep.getAttribute("aria-busy") === "true") return;
      sweep.setAttribute("aria-busy", "true");
      sweep.textContent = strings.sweep_running;
      flash.dataset.tone = "";
      flash.textContent = strings.sweep_running;
      try {
        const response = await fetch("/sweep", { method: "POST", credentials: "same-origin" });
        const body = await response.json().catch(() => ({}));
        if (response.status === 403) {
          flash.dataset.tone = "danger";
          flash.textContent = strings.sweep_forbidden;
        } else if (!response.ok) {
          flash.dataset.tone = "danger";
          flash.textContent = strings.sweep_failed;
        } else {
          flash.dataset.tone = "open";
          flash.textContent = body.new === 1 ? strings.sweep_done_one : format(strings.sweep_done_other, { count: body.new ?? 0, found: body.found ?? 0 });
          clearTimeout(timer);
          timer = null;
          fingerprint = null;
          await swapRegions().catch(() => {});
          poll();
        }
      } catch (error) {
        flash.dataset.tone = "danger";
        flash.textContent = strings.sweep_offline;
      } finally {
        sweep.removeAttribute("aria-busy");
        sweep.textContent = strings.sweep;
      }
    });
  }

  localizeTimes(document);
  setInterval(() => { if (!document.hidden) renderStatus(); }, 1000);
  poll();
})();
