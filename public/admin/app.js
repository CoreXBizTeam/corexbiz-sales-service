(function () {
  var adminLoginGate = document.getElementById("admin-login-gate");
  var adminLayoutEl = document.getElementById("layout");
  var adminLoginForm = document.getElementById("admin-login-form");
  var adminLoginErrorEl = document.getElementById("admin-login-error");
  var envLabelEl = document.getElementById("admin-env-label");
  var panelOverview = document.getElementById("panel-overview");
  var panelLogs = document.getElementById("panel-logs");
  var panelRuns = document.getElementById("panel-runs");
  var overviewStatusEl = document.getElementById("overview-status");
  var overviewDl = document.getElementById("overview-dl");
  var overviewRefreshBtn = document.getElementById("overview-refresh-btn");
  var logsStatusEl = document.getElementById("logs-status");
  var logsTbody = document.getElementById("logs-table-body");
  var logsRequestInput = document.getElementById("logs-filter-request-id");
  var logsRefreshBtn = document.getElementById("logs-refresh-btn");
  var logsClearFilterBtn = document.getElementById("logs-clear-filter-btn");
  var runsStatusEl = document.getElementById("runs-status");
  var runsTbody = document.getElementById("runs-table-body");
  var runsRefreshBtn = document.getElementById("runs-refresh-btn");
  var adminLogoutBtn = document.getElementById("admin-logout-btn");
  var currentPanel = "overview";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showLoginGate(show) {
    adminLoginGate.classList.toggle("d-none", !show);
    adminLayoutEl.classList.toggle("d-none", show);
  }

  function setPanel(name) {
    currentPanel = name;
    panelOverview.classList.toggle("d-none", name !== "overview");
    panelLogs.classList.toggle("d-none", name !== "logs");
    panelRuns.classList.toggle("d-none", name !== "runs");
    document.querySelectorAll("[data-admin-panel]").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-admin-panel") === name);
    });
    if (name === "overview") loadOverview();
    if (name === "logs") loadLogs();
    if (name === "runs") loadRuns();
  }

  function adminFetch(url, opts) {
    return fetch(url, Object.assign({ credentials: "same-origin" }, opts || {})).then(function (res) {
      if (res.status === 401) {
        showLoginGate(true);
        throw new Error("unauthorized");
      }
      return res;
    });
  }

  function checkSession() {
    return adminFetch("/admin/api/session")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.authRequired && !data.authenticated) {
          showLoginGate(true);
          return false;
        }
        showLoginGate(false);
        setPanel(currentPanel);
        return true;
      })
      .catch(function () {
        showLoginGate(true);
        return false;
      });
  }

  function loadOverview() {
    overviewStatusEl.textContent = "Loading…";
    adminFetch("/admin/api")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        envLabelEl.textContent = data.environment || "—";
        overviewStatusEl.textContent = "";
        var rows = [
          ["Worker mode", data.worker_mode],
          ["Cloud Run job", data.cloud_run_job || "—"],
          ["Database", JSON.stringify(data.database || {})],
          ["Google Maps", data.google_maps && data.google_maps.configured ? "configured" : "not configured"],
          ["API docs", data.api_docs],
          ["Health", data.health]
        ];
        overviewDl.innerHTML = rows.map(function (r) {
          return "<dt class=\"col-sm-3\">" + esc(r[0]) + "</dt><dd class=\"col-sm-9\"><code>" + esc(r[1]) + "</code></dd>";
        }).join("");
      })
      .catch(function (err) {
        overviewStatusEl.textContent = "Failed to load: " + err.message;
      });
  }

  function loadLogs() {
    logsStatusEl.textContent = "Loading…";
    var q = logsRequestInput.value.trim();
    var url = "/admin/logs?limit=100" + (q ? "&request_id=" + encodeURIComponent(q) : "");
    adminFetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var hint = data.source ? " (" + data.source + ")" : "";
        if (data.error) {
          logsStatusEl.textContent = data.error;
        } else {
          logsStatusEl.textContent = (data.logs ? data.logs.length : 0) + " entries" + hint;
        }
        if (data.hint) {
          logsStatusEl.textContent += " — " + data.hint;
        }
        logsTbody.innerHTML = (data.logs || []).map(function (row) {
          var rid = row.request_id || "";
          var ridCell = rid
            ? "<span class=\"rid-link text-primary\" data-rid=\"" + esc(rid) + "\">" + esc(rid) + "</span>"
            : "—";
          return "<tr><td class=\"text-nowrap small\">" + esc(row.timestamp) + "</td>" +
            "<td>" + esc(row.severity) + "</td>" +
            "<td class=\"small\">" + ridCell + "</td>" +
            "<td class=\"log-message-cell\">" + esc(row.message) + "</td></tr>";
        }).join("");
        logsTbody.querySelectorAll(".rid-link").forEach(function (el) {
          el.addEventListener("click", function () {
            logsRequestInput.value = el.getAttribute("data-rid") || "";
            loadLogs();
          });
        });
      })
      .catch(function (err) {
        logsStatusEl.textContent = "Failed: " + err.message;
      });
  }

  function loadRuns() {
    runsStatusEl.textContent = "Loading…";
    adminFetch("/admin/api/runs")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var runs = data.runs || [];
        runsStatusEl.textContent = runs.length + " run(s) tracked on this Cloud Run instance.";
        runsTbody.innerHTML = runs.map(function (row) {
          return "<tr><td class=\"small\"><code>" + esc(row.id) + "</code></td>" +
            "<td>" + esc(row.site_id) + "</td>" +
            "<td>" + esc(row.status) + "</td>" +
            "<td>" + esc(row.source_type) + "</td>" +
            "<td class=\"small text-nowrap\">" + esc(row.created_at) + "</td></tr>";
        }).join("") || "<tr><td colspan=\"5\" class=\"text-muted\">No active runs in memory.</td></tr>";
      })
      .catch(function (err) {
        runsStatusEl.textContent = "Failed: " + err.message;
      });
  }

  document.querySelectorAll("[data-admin-panel]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setPanel(btn.getAttribute("data-admin-panel"));
    });
  });

  adminLoginForm.addEventListener("submit", function (ev) {
    ev.preventDefault();
    adminLoginErrorEl.classList.add("d-none");
    var password = document.getElementById("admin-login-password").value;
    fetch("/admin/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password })
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (j) {
            throw new Error(j.detail && j.detail.error ? j.detail.error : "login failed");
          });
        }
        return checkSession();
      })
      .catch(function (err) {
        adminLoginErrorEl.textContent = err.message || "Login failed";
        adminLoginErrorEl.classList.remove("d-none");
      });
  });

  adminLogoutBtn.addEventListener("click", function () {
    fetch("/admin/api/logout", { method: "POST", credentials: "same-origin" })
      .then(function () { showLoginGate(true); });
  });

  overviewRefreshBtn.addEventListener("click", loadOverview);
  logsRefreshBtn.addEventListener("click", loadLogs);
  logsClearFilterBtn.addEventListener("click", function () {
    logsRequestInput.value = "";
    loadLogs();
  });
  runsRefreshBtn.addEventListener("click", loadRuns);
  logsRequestInput.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") loadLogs();
  });

  checkSession();
})();
