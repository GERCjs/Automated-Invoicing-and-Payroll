(function (window) {
    "use strict";

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
            emptyState.textContent = "No data available yet";
            return null;
        }

        emptyState.classList.add("d-none");
        canvas.classList.remove("d-none");

        return new window.Chart(canvas.getContext("2d"), {
            type: config.type,
            data: {
                labels: labels,
                datasets: datasets,
            },
            options: config.options || {},
        });
    }

    window.ReportCharts = {
        parseJsonScript: parseJsonScript,
        renderChart: renderChart,
    };
})(window);
