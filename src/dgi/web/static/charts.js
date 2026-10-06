/* Charts: ECharts options built from the page's JSON. Colours come from the CSS tokens so light and dark both work.
   Names and values from the data are written with textContent or escaped; nothing is concatenated into markup raw. */
(function () {
  "use strict";

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function tokens() {
    return { ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), grid: css("--grid"), axis: css("--axis"),
             surface: css("--surface"), series: [css("--series-1"), css("--series-2"), css("--series-3")] };
  }
  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function format(unit, v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    if (unit === "pct") return (v * 100).toFixed(1) + "%";
    if (unit === "usd") return "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (unit === "x") return v.toFixed(1) + "x";
    if (unit === "millions") return v.toLocaleString(undefined, { maximumFractionDigits: 1 }) + "M";
    return String(v);
  }
  function axisFormat(unit, v, digits) {
    if (unit === "pct") return (v * 100).toFixed(digits) + "%";
    if (unit === "usd") return "$" + v;
    if (unit === "x") return v + "x";
    return v;
  }
  function valueRange(spec) {
    var all = [];
    spec.series.forEach(function (s) { s.values.forEach(function (v) { if (v !== null && v !== undefined) all.push(v); }); });
    return all.length ? { min: Math.min.apply(null, all), max: Math.max.apply(null, all) } : { min: 0, max: 1 };
  }

  function buildOption(spec, t) {
    var multi = spec.series.length > 1;
    var time = spec.xType === "time";
    var range = valueRange(spec);
    var spread = (range.max - range.min) * 100;
    var digits = spec.unit === "pct" ? (spread < 1 ? 2 : spread < 10 ? 1 : 0) : 0;  // narrow ranges need decimals, or ticks repeat
    // bars, ratios and percentages start at zero when they cannot go negative; prices and per-share lines fit their range
    var zero = spec.kind === "bar" || ((spec.unit === "x" || spec.unit === "pct") && range.min >= 0);
    var series = spec.series.map(function (s) {
      var color = t.series[s.slot - 1];
      var data = time ? s.values.map(function (v, i) { return [spec.x[i], v]; }) : s.values;
      var base = { name: s.name, type: spec.kind, data: data, itemStyle: { color: color }, emphasis: { focus: "series" } };
      if (spec.kind === "line") {
        base.symbol = "none"; base.lineStyle = { width: 2, color: color }; base.connectNulls = false;
      } else {
        base.barMaxWidth = 28; base.itemStyle = { color: color, borderRadius: [4, 4, 0, 0] };
      }
      return base;
    });
    if (spec.references && spec.references.length && series.length) {
      series[0].markLine = {
        silent: true, symbol: "none",
        lineStyle: { color: t.muted, type: "dashed", width: 1 },
        label: { color: t.ink2, formatter: function (p) { return p.name; }, position: "insideEndTop" },
        data: spec.references.map(function (r) { return { name: r.name, yAxis: r.value }; })
      };
    }
    return {
      animation: false,
      textStyle: { color: t.ink2, fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
      grid: { left: 48, right: 16, top: multi ? 36 : 12, bottom: 28, containLabel: false },
      legend: multi ? { show: true, top: 0, left: 0, textStyle: { color: t.ink2 }, icon: "roundRect", itemWidth: 14, itemHeight: 3 } : { show: false },
      xAxis: time
        ? { type: "time", axisLine: { lineStyle: { color: t.axis } }, axisLabel: { color: t.muted, hideOverlap: true }, splitLine: { show: false } }
        : { type: "category", data: spec.x, axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, axisLabel: { color: t.muted, hideOverlap: true } },
      yAxis: { type: "value", min: zero ? 0 : null, scale: !zero, axisLabel: { color: t.muted, formatter: function (v) { return axisFormat(spec.unit, v, digits); } },
               splitLine: { lineStyle: { color: t.grid, width: 1 } } },
      tooltip: {
        trigger: "axis", confine: true, axisPointer: { type: "line", lineStyle: { color: t.axis } },
        backgroundColor: t.surface, borderColor: t.grid, textStyle: { color: t.ink },
        formatter: function (params) {
          var rows = params.map(function (p) {
            var v = Array.isArray(p.value) ? p.value[1] : p.value;
            return '<div><span style="display:inline-block;width:12px;height:3px;background:' + esc(p.color) + ';margin-right:6px;vertical-align:middle"></span>' +
                   "<strong>" + esc(format(spec.unit, v)) + '</strong> <span style="color:' + esc(t.ink2) + '">' + esc(p.seriesName) + "</span></div>";
          }).join("");
          var head = Array.isArray(params[0].value) ? String(params[0].value[0]).slice(0, 10) : params[0].axisValueLabel;
          return '<div style="color:' + esc(t.ink2) + '">' + esc(head) + "</div>" + rows;
        }
      },
      series: series
    };
  }

  function tableFor(spec) {
    var table = document.createElement("table");
    var head = table.createTHead().insertRow();
    ["", ].concat(spec.series.map(function (s) { return s.name; })).forEach(function (name, i) {
      var th = document.createElement("th"); th.textContent = i === 0 ? (spec.xType === "time" ? "Date" : "Year") : name; head.appendChild(th);
    });
    var body = table.createTBody();
    var step = spec.x.length > 400 ? Math.ceil(spec.x.length / 400) : 1;
    spec.x.forEach(function (x, i) {
      if (i % step) return;
      var tr = body.insertRow();
      var first = tr.insertCell(); first.textContent = String(x);
      spec.series.forEach(function (s) { var td = tr.insertCell(); td.className = "num"; td.textContent = format(spec.unit, s.values[i]); });
    });
    return table;
  }

  var live = [];

  function dropCharts(card) {
    live = live.filter(function (c) { if (card.contains(c.chart.getDom())) { c.chart.dispose(); return false; } return true; });
  }

  function addChart(host, spec) {
    var card = document.createElement("div"); card.className = "chart-card"; card.dataset.chartId = spec.id;
    var h = document.createElement("h3"); h.textContent = spec.title; card.appendChild(h);
    var el = document.createElement("div"); el.className = "chart"; el.setAttribute("role", "img");
    el.setAttribute("aria-label", spec.title + ". The values are in the table below the chart.");
    card.appendChild(el);
    if (spec.note) { var n = document.createElement("div"); n.className = "note"; n.textContent = spec.note; card.appendChild(n); }
    var d = document.createElement("details"); var s = document.createElement("summary"); s.textContent = "Table";
    d.appendChild(s); var wrap = document.createElement("div"); wrap.className = "table-wrap"; wrap.appendChild(tableFor(spec)); d.appendChild(wrap);
    card.appendChild(d);
    host.appendChild(card);
    var chart = echarts.init(el);
    chart.setOption(buildOption(spec, tokens()));
    live.push({ chart: chart, render: function () { chart.setOption(buildOption(spec, tokens()), true); } });
    return card;
  }

  window.dgiInitCompany = function (ticker) {
    var host = document.getElementById("charts");
    var annual = JSON.parse(document.getElementById("charts-data").textContent);
    annual.forEach(function (spec) { addChart(host, spec); });
    var notice = document.getElementById("daily-notice");
    fetch("/api/company/" + encodeURIComponent(ticker) + "/daily").then(function (r) { return r.json(); }).then(function (body) {
      if (!body.available) { notice.textContent = body.notice || ""; return; }
      var yieldCard = host.querySelector('[data-chart-id="yield"]');
      body.charts.forEach(function (spec) {
        var card = addChart(host, spec);            // created in the page first, so ECharts can measure it
        if (spec.id === "yield_daily" && yieldCard) { dropCharts(yieldCard); yieldCard.replaceWith(card); }
        else if (spec.id === "price") { host.insertBefore(card, host.firstChild); }
      });
      window.dispatchEvent(new Event("resize"));
    }).catch(function () { notice.textContent = "Daily prices could not be loaded; showing annual figures."; });
  };

  window.dgiInitScatter = function () {
    var el = document.getElementById("scatter");
    if (!el) return;
    fetch(el.dataset.scatterUrl).then(function (r) { return r.json(); }).then(function (body) {
      var points = body.points || [];
      var chart = echarts.init(el);
      function render() {
        var t = tokens();
        var scores = points.map(function (p) { return p.score || 0; });
        var lo = Math.min.apply(null, scores.concat([0])), hi = Math.max.apply(null, scores.concat([1]));
        chart.setOption({
          animation: false, textStyle: { color: t.ink2 },
          grid: { left: 56, right: 24, top: 16, bottom: 44 },
          xAxis: { name: "Dividend yield (%)", nameLocation: "middle", nameGap: 28, type: "value", scale: true, boundaryGap: ["4%", "4%"], axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.axis } }, splitLine: { lineStyle: { color: t.grid } } },
          yAxis: { name: "5-year dividend growth (%)", nameLocation: "middle", nameGap: 40, type: "value", scale: true, boundaryGap: ["4%", "4%"], axisLabel: { color: t.muted }, splitLine: { lineStyle: { color: t.grid } } },
          tooltip: { trigger: "item", confine: true, backgroundColor: t.surface, borderColor: t.grid, textStyle: { color: t.ink },
            formatter: function (p) { var d = p.data; return "<strong>" + esc(d.ticker) + "</strong> " + esc(d.name) + "<br>Yield " + d.value[0].toFixed(1) + "% · 5y growth " + d.value[1].toFixed(1) + "%<br>Score " + (d.score === null ? "–" : d.score.toFixed(0)); } },
          series: [{ type: "scatter", itemStyle: { color: t.series[0], opacity: 0.8, borderColor: t.surface, borderWidth: 2 },
            symbolSize: function (v, p) { var s = p.data.score || 0; return 8 + 22 * (s - lo) / (hi - lo || 1); },
            data: points.map(function (p) { return { value: [p.yield, p.dgr5], ticker: p.ticker, name: p.name, score: p.score }; }) }]
        }, true);
      }
      render();
      live.push({ chart: chart, render: render });
      chart.on("click", function (p) { if (p.data && p.data.ticker) window.location.href = "/company/" + encodeURIComponent(p.data.ticker); });
    });
  };

  function rerenderAll() { live.forEach(function (c) { c.render(); }); }
  window.addEventListener("resize", function () { live.forEach(function (c) { c.chart.resize(); }); });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerenderAll);
  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.addEventListener("click", function () {
      var root = document.documentElement;
      var dark = root.dataset.theme ? root.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("dgi-theme", root.dataset.theme); } catch (e) {}
      rerenderAll();
    });
    document.querySelectorAll(".filters input[type=range]").forEach(function (r) {
      r.addEventListener("input", function () { var o = r.parentElement.querySelector("output"); if (o) o.textContent = r.value; });
    });
  });
})();
