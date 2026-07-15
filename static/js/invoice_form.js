(function () {
    function getActiveRows(container) {
        return Array.from(container.querySelectorAll("[data-invoice-item-row]")).filter(function (row) {
            return row.dataset.invoiceItemDeleted !== "true";
        });
    }

    function getDeleteInput(row) {
        return row.querySelector('input[type="checkbox"][name$="-DELETE"]');
    }

    function setRowDeleted(row, isDeleted) {
        var deleteInput = getDeleteInput(row);
        if (deleteInput) {
            deleteInput.checked = isDeleted;
        }
        row.dataset.invoiceItemDeleted = isDeleted ? "true" : "false";
        row.classList.toggle("invoice-item-row-deleted", isDeleted);
        row.hidden = isDeleted;
    }

    function updateRemoveButtons(container) {
        var activeRows = getActiveRows(container);
        activeRows.forEach(function (row) {
            var removeButton = row.querySelector("[data-remove-invoice-item]");
            if (!removeButton) {
                return;
            }
            removeButton.disabled = activeRows.length <= 1;
            removeButton.setAttribute("aria-disabled", activeRows.length <= 1 ? "true" : "false");
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var container = document.getElementById("invoiceItemRows");
        var addButton = document.getElementById("addInvoiceItemButton");
        var template = document.getElementById("invoiceItemEmptyFormTemplate");
        var totalFormsInput = document.getElementById("id_items-TOTAL_FORMS");

        if (!container || !addButton || !template || !totalFormsInput) {
            return;
        }

        Array.from(container.querySelectorAll("[data-invoice-item-row]")).forEach(function (row) {
            setRowDeleted(row, Boolean(getDeleteInput(row) && getDeleteInput(row).checked));
        });

        if (getActiveRows(container).length === 0) {
            var firstRow = container.querySelector("[data-invoice-item-row]");
            if (firstRow) {
                setRowDeleted(firstRow, false);
            }
        }
        updateRemoveButtons(container);

        addButton.addEventListener("click", function () {
            var formIndex = parseInt(totalFormsInput.value, 10);
            if (Number.isNaN(formIndex)) {
                formIndex = container.querySelectorAll("[data-invoice-item-row]").length;
            }

            var wrapper = document.createElement("tbody");
            wrapper.innerHTML = template.innerHTML.replace(/__prefix__/g, String(formIndex)).trim();
            var row = wrapper.querySelector("[data-invoice-item-row]");
            if (!row) {
                return;
            }

            setRowDeleted(row, false);
            container.appendChild(row);
            totalFormsInput.value = String(formIndex + 1);
            updateRemoveButtons(container);

            var firstInput = row.querySelector("input:not([type='hidden']):not([type='checkbox']), textarea, select");
            if (firstInput) {
                firstInput.focus();
            }
        });

        container.addEventListener("click", function (event) {
            var removeButton = event.target.closest("[data-remove-invoice-item]");
            if (!removeButton) {
                return;
            }

            var row = removeButton.closest("[data-invoice-item-row]");
            if (!row || getActiveRows(container).length <= 1) {
                updateRemoveButtons(container);
                return;
            }

            setRowDeleted(row, true);
            updateRemoveButtons(container);
        });
    });
})();
