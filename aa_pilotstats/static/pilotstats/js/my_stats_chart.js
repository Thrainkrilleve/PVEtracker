/*
 * My Stats: weekly average DPS (left axis) and ISK per hour (right axis).
 *
 * Data comes from json_script "pstats-trends-data" ({labels, dps,
 * isk_per_hour}; null for weeks with nothing to rate). Chart.js is Alliance
 * Auth's own bundle; colors follow the page's CSS variables.
 */
(function () {
    "use strict";

    function iskShort(value) {
        var units = [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]];
        for (var i = 0; i < units.length; i++) {
            if (Math.abs(value) >= units[i][0]) {
                return (value / units[i][0]).toFixed(1) + units[i][1];
            }
        }
        return String(Math.round(value));
    }

    function draw() {
        var canvas = document.getElementById("pstats-trends-chart");
        var source = document.getElementById("pstats-trends-data");
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
        var style = getComputedStyle(canvas.closest(".pilotstats-app") || document.body);
        function css(name, fallback) {
            var value = style.getPropertyValue(name).trim();
            return value || fallback;
        }
        var text = css("--bs-secondary-color", "#6c757d");
        var grid = css("--bs-border-color-translucent", "rgba(128,128,128,0.2)");
        var dpsColor = css("--pstats-out", "#0dcaf0");
        var iskColor = css("--pstats-isk", "#ffc107");
        var hasIsk = (data.isk_per_hour || []).some(function (v) { return v !== null; });

        var datasets = [{
            label: wrap.dataset.labelDps,
            data: data.dps,
            yAxisID: "y",
            borderColor: dpsColor,
            backgroundColor: dpsColor,
            borderWidth: 2,
            pointRadius: 3,
            spanGaps: true,
            tension: 0.25,
        }];
        if (hasIsk) {
            datasets.push({
                label: wrap.dataset.labelIsk,
                data: data.isk_per_hour,
                yAxisID: "isk",
                borderColor: iskColor,
                backgroundColor: iskColor,
                borderWidth: 2,
                pointRadius: 3,
                spanGaps: true,
                tension: 0.25,
            });
        }

        new window.Chart(canvas, {
            type: "line",
            data: { labels: data.labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                scales: {
                    x: { ticks: { color: text }, grid: { color: grid } },
                    y: {
                        beginAtZero: true,
                        position: "left",
                        ticks: { color: dpsColor },
                        grid: { color: grid },
                        title: { display: true, text: wrap.dataset.labelDps, color: text },
                    },
                    isk: {
                        display: hasIsk,
                        beginAtZero: true,
                        position: "right",
                        ticks: { color: iskColor, callback: iskShort },
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: wrap.dataset.labelIsk, color: text },
                    },
                },
                plugins: {
                    legend: { labels: { color: text, usePointStyle: true, pointStyle: "line" } },
                    tooltip: {
                        callbacks: {
                            label: function (item) {
                                if (item.raw === null) {
                                    return item.dataset.label + ": —";
                                }
                                var shown = item.dataset.yAxisID === "isk" ? iskShort(item.raw) + " ISK" : String(item.raw);
                                return item.dataset.label + ": " + shown;
                            },
                        },
                    },
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
