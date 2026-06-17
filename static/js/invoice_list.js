document.addEventListener("DOMContentLoaded", function () {
    var issueDateFrom = document.getElementById("issueDateFrom");
    var issueDateTo = document.getElementById("issueDateTo");
    var dateRangeMessage = document.getElementById("invoiceDateRangeMessage");
    var invoiceListSearchForm = document.getElementById("invoiceListSearchForm");
    var invoiceListSearchButton = document.getElementById("invoiceListSearchButton");
    var invoiceListResetButton = document.getElementById("invoiceListResetButton");
    var hasInitialServerMessage = Boolean(
        dateRangeMessage
        && !dateRangeMessage.classList.contains("d-none")
        && dateRangeMessage.textContent.trim()
    );

    var showDateRangeMessage = function (message) {
        if (!dateRangeMessage) {
            return;
        }
        if (message) {
            dateRangeMessage.textContent = message;
            dateRangeMessage.classList.remove("d-none");
        } else {
            dateRangeMessage.textContent = "";
            dateRangeMessage.classList.add("d-none");
        }
    };

    var updateDateLimits = function () {
        if (!issueDateFrom || !issueDateTo) {
            return;
        }
        issueDateTo.min = issueDateFrom.value || "";
        issueDateFrom.max = issueDateTo.value || "";
    };

    var validateDateRange = function (preserveExistingMessage) {
        if (!issueDateFrom || !issueDateTo) {
            return true;
        }

        updateDateLimits();
        issueDateFrom.setCustomValidity("");
        issueDateTo.setCustomValidity("");

        if (issueDateFrom.value && issueDateTo.value && issueDateFrom.value > issueDateTo.value) {
            issueDateTo.setCustomValidity("To date cannot be earlier than From date.");
            if (!preserveExistingMessage || !hasInitialServerMessage) {
                showDateRangeMessage("To date cannot be earlier than From date.");
            }
            if (invoiceListSearchButton) {
                invoiceListSearchButton.disabled = true;
            }
            return false;
        }

        if (!preserveExistingMessage || !hasInitialServerMessage) {
            showDateRangeMessage("");
        }
        if (invoiceListSearchButton) {
            invoiceListSearchButton.disabled = false;
        }
        return true;
    };

    if (issueDateFrom && issueDateTo) {
        updateDateLimits();
        validateDateRange(true);
        hasInitialServerMessage = false;

        issueDateFrom.addEventListener("change", function () {
            if (issueDateTo.value && issueDateFrom.value && issueDateTo.value < issueDateFrom.value) {
                issueDateTo.value = "";
                showDateRangeMessage("The To date was cleared because it was earlier than the From date.");
            }
            updateDateLimits();
            validateDateRange();
        });

        issueDateTo.addEventListener("change", function () {
            validateDateRange();
        });
    }

    if (invoiceListSearchForm) {
        invoiceListSearchForm.addEventListener("submit", function (event) {
            if (!validateDateRange()) {
                event.preventDefault();
            }
        });
    }

    if (invoiceListResetButton) {
        invoiceListResetButton.addEventListener("click", function () {
            if (issueDateFrom) {
                issueDateFrom.value = "";
                issueDateFrom.max = "";
                issueDateFrom.setCustomValidity("");
            }
            if (issueDateTo) {
                issueDateTo.value = "";
                issueDateTo.min = "";
                issueDateTo.setCustomValidity("");
            }
            if (invoiceListSearchButton) {
                invoiceListSearchButton.disabled = false;
            }
            showDateRangeMessage("");
        });
    }

    var selectAllInvoices = document.getElementById("selectAllInvoices");
    if (!selectAllInvoices) {
        return;
    }

    var batchCheckboxes = Array.from(document.querySelectorAll(".js-invoice-batch-checkbox"));
    if (!batchCheckboxes.length) {
        selectAllInvoices.disabled = true;
        return;
    }

    var syncSelectAll = function () {
        var checkedCount = batchCheckboxes.filter(function (checkbox) {
            return checkbox.checked;
        }).length;
        selectAllInvoices.checked = checkedCount > 0 && checkedCount === batchCheckboxes.length;
        selectAllInvoices.indeterminate = checkedCount > 0 && checkedCount < batchCheckboxes.length;
    };

    selectAllInvoices.addEventListener("change", function () {
        batchCheckboxes.forEach(function (checkbox) {
            checkbox.checked = selectAllInvoices.checked;
        });
        syncSelectAll();
    });

    batchCheckboxes.forEach(function (checkbox) {
        checkbox.addEventListener("change", syncSelectAll);
    });

    syncSelectAll();
});
