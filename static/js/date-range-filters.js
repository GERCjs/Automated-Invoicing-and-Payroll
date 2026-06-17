document.addEventListener("DOMContentLoaded", function () {
    var dateRangeForms = Array.from(document.querySelectorAll("[data-date-range-form]"));

    dateRangeForms.forEach(function (form) {
        var fromInput = form.querySelector("[data-date-from]");
        var toInput = form.querySelector("[data-date-to]");
        var dateMessage = form.querySelector("[data-date-error]");
        var hasInitialServerMessage = Boolean(
            dateMessage
            && !dateMessage.classList.contains("d-none")
            && dateMessage.textContent.trim()
        );

        if (!fromInput || !toInput) {
            return;
        }

        var setMessage = function (message) {
            if (!dateMessage) {
                return;
            }

            if (message) {
                dateMessage.textContent = message;
                dateMessage.classList.remove("d-none");
            } else {
                dateMessage.textContent = "";
                dateMessage.classList.add("d-none");
            }
        };

        var setInputState = function (isInvalid) {
            fromInput.classList.toggle("is-invalid", Boolean(isInvalid));
            toInput.classList.toggle("is-invalid", Boolean(isInvalid));
        };

        var updateDateLimits = function () {
            toInput.min = fromInput.value || "";
            fromInput.max = toInput.value || "";
        };

        var validateDateRange = function (preserveExistingMessage) {
            updateDateLimits();
            fromInput.setCustomValidity("");
            toInput.setCustomValidity("");

            if (fromInput.value && toInput.value && fromInput.value > toInput.value) {
                toInput.setCustomValidity("To date cannot be earlier than From date.");
                setInputState(true);
                if (!preserveExistingMessage || !hasInitialServerMessage) {
                    setMessage("To date cannot be earlier than From date.");
                }
                return false;
            }

            setInputState(false);
            if (!preserveExistingMessage || !hasInitialServerMessage) {
                setMessage("");
            }
            return true;
        };

        updateDateLimits();
        validateDateRange(true);
        hasInitialServerMessage = false;

        fromInput.addEventListener("change", function () {
            if (toInput.value && fromInput.value && toInput.value < fromInput.value) {
                toInput.value = "";
                setMessage("The To date was cleared because it was earlier than the From date.");
            }
            validateDateRange();
        });

        toInput.addEventListener("change", function () {
            validateDateRange();
        });

        form.addEventListener("submit", function (event) {
            if (!validateDateRange()) {
                event.preventDefault();
            }
        });
    });
});
