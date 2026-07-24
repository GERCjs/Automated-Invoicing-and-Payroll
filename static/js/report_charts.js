(function (window) {
    "use strict";

    function formatValueLabel(value) {
        var numericValue = Number(value);
        if (!Number.isFinite(numericValue)) {
            return "";
        }

        var hasFraction = Math.abs(numericValue % 1) > 0.000001;
        return numericValue.toLocaleString(undefined, {
            minimumFractionDigits: hasFraction ? 2 : 0,
            maximumFractionDigits: hasFraction ? 2 : 0,
        });
    }

    function parseJsonScript(scriptId, fallbackValue) {
        var node = document.getElementById(scriptId);
        if (!node) {
            return fallbackValue;
        }

        try {
            return JSON.parse(node.textContent);
        } catch (error) {
            return fallbackValue;
        }
    }

    function hasNumericData(values) {
        if (!Array.isArray(values) || values.length === 0) {
            return false;
        }

        return values.some(function (value) {
            var numericValue = Number(value);
            return Number.isFinite(numericValue) && numericValue > 0;
        });
    }

    function normalizeDataset(dataset) {
        var dataValues = Array.isArray(dataset.data) ? dataset.data.map(Number) : [];
        return {
            label: dataset.label || "",
            data: dataValues,
            backgroundColor: dataset.backgroundColor,
            borderColor: dataset.borderColor,
            borderWidth: dataset.borderWidth,
            tension: dataset.tension,
            fill: dataset.fill,
        };
    }

    function buildValueLabelPlugin(config) {
        return {
            id: "reportValueLabels",
            afterDatasetsDraw: function (chart) {
                var ctx = chart.ctx;
                var chartType = chart.config.type;
                var formatter = typeof config.valueLabelFormatter === "function"
                    ? config.valueLabelFormatter
                    : formatValueLabel;
                var textColor = config.valueLabelColor || "#334155";
                var fontSize = config.valueLabelFontSize || 11;
                var fontWeight = config.valueLabelFontWeight || "700";

                ctx.save();
                ctx.fillStyle = textColor;
                ctx.font = fontWeight + " " + fontSize + "px Segoe UI";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.strokeStyle = "rgba(255, 255, 255, 0.92)";
                ctx.lineWidth = 3;
                ctx.lineJoin = "round";

                function clampLabelY(preferredY, fallbackY) {
                    var chartArea = chart.chartArea || {};
                    var topLimit = Number.isFinite(chartArea.top) ? chartArea.top + 12 : 12;
                    var bottomLimit = Number.isFinite(chartArea.bottom) ? chartArea.bottom - 12 : preferredY;
                    var y = preferredY < topLimit && Number.isFinite(fallbackY) ? fallbackY : preferredY;
                    if (y < topLimit) {
                        return topLimit;
                    }
                    if (y > bottomLimit) {
                        return bottomLimit;
                    }
                    return y;
                }

                chart.data.datasets.forEach(function (dataset, datasetIndex) {
                    var meta = chart.getDatasetMeta(datasetIndex);
                    if (!meta || meta.hidden) {
                        return;
                    }

                    meta.data.forEach(function (element, index) {
                        var rawValue = dataset.data[index];
                        var numericValue = Number(rawValue);
                        if (!Number.isFinite(numericValue) || numericValue <= 0) {
                            return;
                        }

                        var labelText = formatter(rawValue, chartType);
                        if (!labelText) {
                            return;
                        }

                        var x = 0;
                        var y = 0;

                        if (chartType === "bar") {
                            if (chart.options && chart.options.indexAxis === "y") {
                                var chartArea = chart.chartArea || {};
                                var rightLimit = Number.isFinite(chartArea.right) ? chartArea.right - 10 : element.x + 18;
                                x = Math.min(element.x + 18, rightLimit);
                                y = element.y;
                                ctx.textAlign = "left";
                            } else {
                                x = element.x;
                                y = clampLabelY(element.y - 12, element.y + 14);
                                ctx.textAlign = "center";
                            }
                        } else if (chartType === "line") {
                            x = element.x;
                            y = clampLabelY(element.y - 14, element.y + 14);
                            ctx.textAlign = "center";
                        } else if (chartType === "doughnut" || chartType === "pie") {
                            var position = element.tooltipPosition();
                            x = position.x;
                            y = position.y;
                            ctx.textAlign = "center";
                        } else {
                            return;
                        }

                        ctx.strokeText(labelText, x, y);
                        ctx.fillText(labelText, x, y);
                    });
                });

                ctx.restore();
            },
        };
    }

    function renderChart(config) {
        var canvas = document.getElementById(config.canvasId);
        var emptyState = document.getElementById(config.emptyStateId);

        if (!canvas || !emptyState) {
            return null;
        }

        var labels = Array.isArray(config.labels) ? config.labels : [];
        var datasets = Array.isArray(config.datasets) ? config.datasets.map(normalizeDataset) : [];
        var hasData = labels.length > 0 && datasets.some(function (dataset) {
            return hasNumericData(dataset.data);
        });

        if (!hasData || typeof window.Chart === "undefined") {
            canvas.classList.add("d-none");
            emptyState.classList.remove("d-none");
            emptyState.textContent = config.emptyText || "No data available yet";
            return null;
        }

        emptyState.classList.add("d-none");
        canvas.classList.remove("d-none");

        var plugins = [];
        if (config.showValueLabels !== false) {
            plugins.push(buildValueLabelPlugin(config));
        }

        return new window.Chart(canvas.getContext("2d"), {
            type: config.type,
            data: {
                labels: labels,
                datasets: datasets,
            },
            options: config.options || {},
            plugins: plugins,
        });
    }

    window.ReportCharts = {
        parseJsonScript: parseJsonScript,
        renderChart: renderChart,
    };
})(window);
