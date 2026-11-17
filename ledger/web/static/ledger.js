// Page behavior for the account ledger: the theme toggle, the month picker, CSV export and
// the Activity table. The browser only talks to this app; it never sees a credential.
(function () {
  "use strict";

  // -- small helpers -------------------------------------------------------------------

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (attrs[key] == null || attrs[key] === false) return;
      if (key === "class") node.className = attrs[key];
      else if (key === "text") node.textContent = attrs[key];
      else node.setAttribute(key, attrs[key] === true ? "" : attrs[key]);
    });
    (children || []).forEach(function (child) {
      if (child != null) node.append(child);
    });
    return node;
  }

  function money(micro, places) {
    if (micro == null) return "—";
    var amount = (Math.abs(micro) / 1e6).toLocaleString("en-US", {
      minimumFractionDigits: places,
      maximumFractionDigits: places,
    });
    return (micro < 0 ? "-$" : "$") + amount;
  }

  function utc(value) {
    var time = Date.parse(value);
    if (isNaN(time)) return "—";
    return new Date(time).toISOString().slice(0, 19).replace("T", " ");
  }

  // A cell that a spreadsheet would run as a formula is written as text instead. Phone
  // numbers and plain signed numbers are left alone.
  function csvCell(value) {
    var text = value == null ? "" : String(value);
    if (/^[=@\t\r]/.test(text) || (/^[+-]/.test(text) && !/^[+-]?[\d.,$ ]+$/.test(text))) {
      text = "'" + text;
    }
    return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }

  function download(filename, type, content) {
    var url = URL.createObjectURL(new Blob([content], { type: type }));
    var link = el("a", { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  function toCsv(rows) {
    return rows.map(function (row) { return row.map(csvCell).join(","); }).join("\r\n") + "\r\n";
  }

  // -- theme, month picker, table export -----------------------------------------------

  var toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var root = document.documentElement;
      var dark = root.getAttribute("data-theme")
        ? root.getAttribute("data-theme") === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches;
      var next = dark ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try {
        localStorage.setItem("ledger-theme", next);
      } catch (e) {
        /* the choice lasts for this page only */
      }
    });
  }

  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    select.addEventListener("change", function () {
      select.form.submit();
    });
  });

  document.querySelectorAll("[data-export-table]").forEach(function (button) {
    button.addEventListener("click", function () {
      var table = document.getElementById(button.dataset.exportTable);
      if (!table) return;
      var rows = Array.prototype.map.call(table.querySelectorAll("thead tr, tbody tr"), function (tr) {
        return Array.prototype.map.call(tr.cells, function (cell) {
          return cell.textContent.trim().replace(/\s+/g, " ");
        });
      });
      download(button.dataset.filename || "export.csv", "text/csv", toCsv(rows));
    });
  });

  // -- Activity ------------------------------------------------------------------------

  var root = document.getElementById("activity");
  if (root) activity(root);

  function activity(root) {
    var ledgerProducts = root.dataset.ledgerProducts.split(",");
    var form = document.getElementById("activity-filters");
    var tbody = document.querySelector("#activity-table tbody");
    var summary = document.getElementById("activity-summary");
    var notices = document.getElementById("activity-notices");
    var empty = document.getElementById("activity-empty");
    var more = document.getElementById("load-more");
    var chips = root.querySelectorAll(".chip");

    var state = { product: root.dataset.product, sources: {}, rows: [], run: 0, shown: [] };

    var query = new URLSearchParams(location.search);
    ["from", "to"].forEach(function (name) {
      if (/^\d{4}-\d{2}-\d{2}$/.test(query.get(name) || "")) form.elements[name].value = query.get(name);
    });

    function sourceKeys() {
      return state.product === "all" ? ledgerProducts : [state.product];
    }

    function fetchSource(key, cursor) {
      var params = new URLSearchParams();
      if (form.elements.from.value) params.set("from", form.elements.from.value);
      if (form.elements.to.value) params.set("to", form.elements.to.value);
      if (form.elements.include_deleted.checked) params.set("include_deleted", "true");
      if (cursor) params.set("cursor", cursor);
      return fetch("/api/activity/" + key + "?" + params.toString())
        .then(function (response) {
          return response.json().then(function (body) {
            if (!response.ok) throw new Error(body.error || "The request failed.");
            return body;
          });
        })
        .then(
          function (body) { return { key: key, rows: body.rows || [], next: body.next || null }; },
          function (error) { return { key: key, rows: [], next: null, error: error.message }; }
        );
    }

    function apply(results) {
      results.forEach(function (result) {
        state.sources[result.key] = { next: result.next, error: result.error };
        state.rows = state.rows.concat(result.rows);
      });
    }

    function load(keys, fresh) {
      var run = ++state.run;
      more.disabled = true;
      summary.textContent = "Loading…";
      if (fresh) {
        state.sources = {};
        state.rows = [];
      }
      return Promise.all(
        keys.map(function (key) {
          return fetchSource(key, fresh ? null : state.sources[key].next);
        })
      ).then(function (results) {
        if (run !== state.run) return; // a newer request replaced this one
        apply(results);
        more.disabled = false;
        render();
      });
    }

    function reload() {
      var params = new URLSearchParams(location.search);
      params.set("product", state.product);
      params.set("from", form.elements.from.value);
      params.set("to", form.elements.to.value);
      history.replaceState(null, "", location.pathname + "?" + params.toString());
      chips.forEach(function (chip) {
        chip.setAttribute("aria-pressed", String(chip.dataset.product === state.product));
      });
      closeDrawer();
      load(sourceKeys(), true);
    }

    function options(select, values) {
      var current = select.value;
      while (select.options.length > 1) select.remove(1);
      values.sort().forEach(function (value) {
        select.add(new Option(value, value));
      });
      select.value = values.indexOf(current) >= 0 ? current : "";
    }

    function distinct(field) {
      var seen = {};
      state.rows.forEach(function (row) {
        if (row[field]) seen[row[field]] = true;
      });
      return Object.keys(seen);
    }

    function when(row) {
      var time = Date.parse(row.created_at);
      return isNaN(time) ? 0 : time;
    }

    function party(row) {
      if (row.to) return (row.from || "—") + " → " + row.to;
      return row.from || "—";
    }

    function badge(row) {
      if (!row.status) return el("span", { class: "muted", text: "—" });
      return el("span", { class: "badge badge-" + (row.tone || "neutral") }, [
        el("span", { class: "dot", "aria-hidden": "true" }),
        row.status,
      ]);
    }

    function render() {
      options(form.elements.status, distinct("status"));
      options(form.elements.direction, distinct("direction"));
      var status = form.elements.status.value;
      var direction = form.elements.direction.value;
      var needle = form.elements.q.value.trim().toLowerCase();

      var rows = state.rows.filter(function (row) {
        if (status && row.status !== status) return false;
        if (direction && row.direction !== direction) return false;
        if (needle) {
          var hay = [row.id, row.from, row.to, row.type, row.error].join(" ").toLowerCase();
          if (hay.indexOf(needle) < 0) return false;
        }
        return true;
      });
      rows.sort(function (a, b) { return when(b) - when(a); });
      state.shown = rows;

      // In the combined view each product pages on its own. Rows older than the point
      // every product has been read back to may have neighbours that are not loaded yet,
      // so a divider marks where the list stops being complete. This relies on each list
      // arriving newest first, which is what the API returns but the docs do not state.
      var pending = sourceKeys().filter(function (key) {
        return state.sources[key] && state.sources[key].next;
      });
      var complete = -Infinity;
      if (sourceKeys().length > 1) {
        pending.forEach(function (key) {
          var oldest = Infinity;
          state.rows.forEach(function (row) {
            if (row.product === key) oldest = Math.min(oldest, when(row));
          });
          complete = Math.max(complete, oldest);
        });
      }

      tbody.replaceChildren();
      var divided = false;
      rows.forEach(function (row) {
        if (!divided && when(row) < complete) {
          divided = true;
          tbody.append(
            el("tr", { class: "divider" }, [
              el("td", { colspan: "8", text: "Older rows below may be incomplete until you load more." }),
            ])
          );
        }
        var tr = el("tr", { class: "row", tabindex: "0", "data-id": row.id }, [
          el("td", { class: "mono nowrap", text: utc(row.created_at) }),
          el("td", { text: row.product_label }),
          el("td", { class: "mono col-type", text: row.type || "—" }),
          el("td", { text: row.direction || "—" }),
          el("td", { class: "mono party", title: party(row), text: party(row) }),
          el("td", {}, [badge(row)]),
          el("td", { text: row.size || "—" }),
          el("td", { class: "num", text: row.charge || "—" }),
        ]);
        tr.addEventListener("click", function () { openDrawer(row, tr); });
        tr.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openDrawer(row, tr);
          }
        });
        tbody.append(tr);
      });

      notices.replaceChildren();
      sourceKeys().forEach(function (key) {
        var source = state.sources[key];
        if (source && source.error) {
          var chip = root.querySelector('.chip[data-product="' + key + '"]');
          notices.append(
            el("p", { class: "notice notice-error", role: "alert", text: chip.textContent + ": " + source.error })
          );
        }
      });

      var total = 0;
      var charged = 0;
      rows.forEach(function (row) {
        if (row.charge_micro != null) {
          total += row.charge_micro;
          charged += 1;
        }
      });
      var text = rows.length === state.rows.length
        ? rows.length + (rows.length === 1 ? " row" : " rows") + " loaded"
        : rows.length + " of " + state.rows.length + " loaded rows match";
      if (charged) text += " · charges " + money(total, 4);
      if (pending.length) text += " · more available";
      summary.textContent = text;

      empty.hidden = rows.length > 0 || notices.children.length > 0;
      more.hidden = pending.length === 0;
    }

    // -- the detail drawer ---------------------------------------------------------------

    var drawer = document.getElementById("drawer");
    var backdrop = document.getElementById("drawer-backdrop");
    var body = document.getElementById("drawer-body");
    var title = document.getElementById("drawer-title");
    var closeButton = document.getElementById("drawer-close");
    var opener = null;

    function fields(pairs) {
      var list = el("dl", { class: "fields" });
      pairs.forEach(function (pair) {
        if (pair[1] == null || pair[1] === "") return;
        list.append(el("div", {}, [el("dt", { text: pair[0] }), el("dd", { class: pair[2] || "", text: String(pair[1]) })]));
      });
      return list;
    }

    // The same list with the API's own field names as labels, set in the code typeface.
    function rawFields(pairs) {
      var list = fields(pairs);
      list.classList.add("fields-raw");
      Array.prototype.forEach.call(list.querySelectorAll("dt"), function (dt) {
        dt.classList.add("mono");
      });
      return list;
    }

    function section(heading, content) {
      return el("section", {}, [el("h3", { text: heading }), content]);
    }

    function fill(row, extra) {
      var record = row.record || {};
      title.textContent = row.product_label + " log";
      body.replaceChildren();

      var charge = el("section", {}, [
        el("h3", { text: "Charge" }),
        el("p", { class: "drawer-charge", text: row.charge || "No charge recorded" }),
      ]);
      var details = (record.charge_details || []).map(function (item) {
        return [item.description, money(Math.round(Number(item.charge) * 1e6), 4), "num"];
      });
      if (details.length) {
        var breakdown = fields(details);
        breakdown.classList.add("fields-wide");
        charge.append(breakdown);
      }
      body.append(charge);

      body.append(
        section("Details", fields([
          ["ID", row.id, "mono"],
          ["Time (UTC)", utc(row.created_at), "mono"],
          ["Type", row.type, "mono"],
          ["Direction", row.direction],
          ["From", row.from, "mono"],
          ["To", row.to, "mono"],
          ["Status", row.status],
          ["Size", row.size],
          ["Billed time", record.billing_ms != null ? record.billing_ms / 1000 + " s" : null],
          ["Source", record.source, "mono"],
          ["Parent log", record.parent_id, "mono"],
          ["Started (UTC)", record.started_at ? utc(record.started_at) : null, "mono"],
          ["Ended (UTC)", record.ended_at ? utc(record.ended_at) : null, "mono"],
          ["Deleted (UTC)", record.discarded_at ? utc(record.discarded_at) : null, "mono"],
          ["Error", row.error],
        ]))
      );

      var quality = Object.assign({}, record.qos_metrics || {});
      Object.keys(record).forEach(function (key) {
        if (key.indexOf("audio_") === 0 && record[key] != null) quality[key] = record[key];
      });
      var measured = Object.keys(quality).sort().map(function (key) {
        return [key, quality[key], "mono"];
      });
      if (measured.length) body.append(section("Audio quality", rawFields(measured)));

      if (extra && extra.events && extra.events.length) {
        var timeline = el("ol", { class: "timeline" });
        extra.events.forEach(function (event) {
          var item = el("li", { class: "level-" + (event.level || "info") }, [
            el("span", { class: "when mono", text: utc(event.event_at).slice(11) }),
            el("span", { class: "what mono", text: event.name || "event" }),
          ]);
          if (event.details && Object.keys(event.details).length) {
            item.append(el("pre", { text: JSON.stringify(event.details, null, 2) }));
          }
          timeline.append(item);
        });
        body.append(section("Events", timeline));
      } else if (extra && extra.events_error) {
        body.append(section("Events", el("p", { class: "notice notice-error", text: extra.events_error })));
      } else if (extra && extra.loading) {
        body.append(el("p", { class: "muted", text: "Loading the latest record…" }));
      }
      if (extra && extra.error) {
        body.append(el("p", { class: "notice notice-error", text: extra.error }));
      }

      body.append(
        el("details", {}, [
          el("summary", { text: "Full record" }),
          el("pre", { text: JSON.stringify(record, null, 2) }),
        ])
      );
    }

    function openDrawer(row, tr) {
      opener = tr;
      tbody.querySelectorAll(".is-open").forEach(function (node) { node.classList.remove("is-open"); });
      tr.classList.add("is-open");
      fill(row, { loading: row.has_detail });
      drawer.hidden = false;
      backdrop.hidden = false;
      closeButton.focus();
      if (!row.has_detail) return;
      fetch("/api/activity/" + row.product + "/" + encodeURIComponent(row.id))
        .then(function (response) { return response.json(); })
        .then(function (detail) {
          if (opener !== tr || drawer.hidden) return;
          if (detail.error) fill(row, { error: detail.error });
          else fill(detail.row, detail);
        })
        .catch(function () {
          if (opener === tr && !drawer.hidden) fill(row, { error: "The latest record could not be loaded." });
        });
    }

    function closeDrawer() {
      if (drawer.hidden) return;
      drawer.hidden = true;
      backdrop.hidden = true;
      tbody.querySelectorAll(".is-open").forEach(function (node) { node.classList.remove("is-open"); });
      if (opener && opener.isConnected) opener.focus();
      opener = null;
    }

    closeButton.addEventListener("click", closeDrawer);
    backdrop.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) {
      if (drawer.hidden) return;
      if (event.key === "Escape") closeDrawer();
      if (event.key !== "Tab") return;
      // Keep keyboard focus inside the open drawer.
      var stops = drawer.querySelectorAll("button, summary, a[href]");
      var first = stops[0];
      var last = stops[stops.length - 1];
      if (!drawer.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    // -- wiring --------------------------------------------------------------------------

    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        state.product = chip.dataset.product;
        reload();
      });
    });
    form.addEventListener("submit", function (event) { event.preventDefault(); });
    ["from", "to", "include_deleted"].forEach(function (name) {
      form.elements[name].addEventListener("change", reload);
    });
    ["status", "direction"].forEach(function (name) {
      form.elements[name].addEventListener("change", render);
    });
    form.elements.q.addEventListener("input", render);

    more.addEventListener("click", function () {
      load(
        sourceKeys().filter(function (key) { return state.sources[key] && state.sources[key].next; }),
        false
      );
    });

    document.getElementById("export-csv").addEventListener("click", function () {
      var head = ["created_at", "product", "type", "direction", "from", "to", "status", "size", "charge_usd", "id"];
      var lines = state.shown.map(function (row) {
        return [
          row.created_at, row.product, row.type, row.direction, row.from, row.to, row.status, row.size,
          row.charge_micro == null ? "" : (row.charge_micro / 1e6).toFixed(6), row.id,
        ];
      });
      download("activity.csv", "text/csv", toCsv([head].concat(lines)));
    });
    document.getElementById("export-json").addEventListener("click", function () {
      var records = state.shown.map(function (row) {
        return Object.assign({ product: row.product }, row.record);
      });
      download("activity.json", "application/json", JSON.stringify(records, null, 2));
    });

    if (root.dataset.configured === "false") {
      // No project credentials: the page already says so, and there is nothing to request.
      summary.textContent = "Not configured";
      chips.forEach(function (chip) {
        chip.setAttribute("aria-pressed", String(chip.dataset.product === state.product));
      });
      return;
    }
    reload();
  }
})();
