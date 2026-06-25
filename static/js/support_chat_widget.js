(function () {
    const widget = document.querySelector("[data-support-chat]");
    if (!widget) {
        return;
    }

    const panel = widget.querySelector("#support-chat-panel");
    const toggles = widget.querySelectorAll("[data-support-chat-toggle]");
    const form = widget.querySelector("[data-support-chat-form]");
    const messages = widget.querySelector("[data-support-chat-messages]");
    const messageInput = widget.querySelector("[data-support-chat-input]");
    const submitButton = widget.querySelector("[data-support-chat-submit]");

    function appendMessage(text, type) {
        const bubble = document.createElement("div");
        bubble.className = `support-chat-message support-chat-message-${type}`;
        bubble.textContent = text;
        messages.appendChild(bubble);
        messages.scrollTop = messages.scrollHeight;
    }

    function setOpen(isOpen) {
        panel.hidden = !isOpen;
        toggles.forEach((toggle) => {
            toggle.setAttribute("aria-expanded", String(isOpen));
        });
        if (isOpen) {
            messageInput.focus();
        }
    }

    toggles.forEach((toggle) => {
        toggle.addEventListener("click", () => {
            setOpen(panel.hidden);
        });
    });

    messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const formData = new FormData(form);
        const message = String(formData.get("message") || "").trim();
        if (!message) {
            appendMessage("Please type a message before sending.", "bot");
            return;
        }

        appendMessage(message, "user");
        submitButton.disabled = true;
        submitButton.textContent = "Sending...";

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: formData,
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                appendMessage("I could not send that request. Please check the fields and try again.", "bot");
                return;
            }
            appendMessage(payload.message, "bot");
            form.reset();
            messageInput.focus();
        } catch (error) {
            appendMessage("The request could not be sent right now. Please try again in a moment.", "bot");
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = "Send";
        }
    });
})();
