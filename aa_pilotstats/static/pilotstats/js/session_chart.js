/*
 * Session timeline chart: damage and reps per second over the session.
 *
 * Data comes from the session's stored timeline (json_script
 * "pstats-timeline-data"): one total per bucket of `bucket` seconds from
 * `start` (EVE time). Chart.js is Alliance Auth's own bundle. Colors come from
 * the same CSS variables as the stat cards, so light and dark themes match.
 */
(function () {
    "use strict";

    var SERIES = [
        // [data key, label attribute, CSS variable, fallback color, dashed]
        ["damage_out", "labelOut", "--pstats-out", "#0dcaf0", false],
        ["damage_in", "labelIn", "--pstats-in", "#dc3545", false],
        ["remote_rep_in", "labelRepIn", "--pstats-rep", "#ffc107", false],
        ["remote_rep_out", "labelRepOut", "--pstats-rep", "#ffc107", true],
    ];

    function pad(n) {
        return n < 10 ? "0" + n : String(n);
    }

    function draw() {
        var canvas = document.getElementById("pstats-timeline-chart");
        var source = document.getElementById("pstats-timeline-data");
        if (!canvas || !source) {
            return;
        }
        var wrap = canvas.parentElement;
        if (typeof window.Chart === "undefined") {
            wrap.style.height = "auto";
            wrap.textContent = wrap.dataset.unavailable;
            wrap.classList.add("text-body-secondary", "small");
            return;
        }

        var data = JSON.parse(source.textContent);
        var bucket = data.bucket || 60;
        var start = Date.parse(data.start);
        var count = (data.damage_out || []).length;
        var labels = [];
        for (var i = 0; i < count; i++) {
            var at = new Date(start + i * bucket * 1000);
            labels.push(pad(at.getUTCHours()) + ":" + pad(at.getUTCMinutes()));
        }

        var style = getComputedStyle(canvas.closest(".pilotstats-app") || document.body);
        function css(name, fallback) {
            var value = style.getPropertyValue(name).trim();
            return value || fallback;
        }
        var text = css("--bs-secondary-color", "#6c757d");
        var grid = css("--bs-border-color-translucent", "rgba(128,128,128,0.2)");

        var datasets = [];
        SERIES.forEach(function (series) {
            var values = data[series[0]] || [];
            if (!values.some(function (v) { return v > 0; })) {
                return; // nothing of this kind in the session
            }
            var color = css(series[2], series[3]);
            datasets.push({
                label: wrap.dataset[series[1]],
                data: values.map(function (v) { return Math.round(v / bucket); }),
                borderColor: color,
                backgroundColor: color,
                borderDash: series[4] ? [6, 4] : [],
                borderWidth: 2,
                pointRadius: 0,
                pointHitRadius: 6,
                tension: 0.25,
            });
        });

        new window.Chart(canvas, {
            type: "line",
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                scales: {
                    x: { ticks: { color: text, maxTicksLimit: 12, autoSkip: true }, grid: { color: grid } },
                    y: {
                        beginAtZero: true,
                        ticks: { color: text },
                        grid: { color: grid },
                        title: { display: true, text: wrap.dataset.labelAxis, color: text },
                    },
                },
                plugins: {
                    legend: { labels: { color: text, usePointStyle: true, pointStyle: "line" } },
                },
            },
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", draw);
    } else {
        draw();
    }
})();
