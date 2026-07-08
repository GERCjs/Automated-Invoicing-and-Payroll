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
    const categoryInput = widget.querySelector("[data-support-chat-category]");
    const issueInput = widget.querySelector("[data-support-chat-issue]");
    const invoiceIdInput = widget.querySelector("[data-support-chat-invoice-id]");
    const referenceInput = widget.querySelector("[data-support-chat-reference]");
    const optionsScript = widget.querySelector("#support-chat-options");
    const chatOptions = optionsScript ? JSON.parse(optionsScript.textContent) : { options: [], references: [] };
    let activeOption = null;

    function appendMessage(text, type) {
        const bubble = document.createElement("div");
        bubble.className = `support-chat-message support-chat-message-${type}`;
        bubble.textContent = text;
        messages.appendChild(bubble);
        messages.scrollTop = messages.scrollHeight;
    }

    function appendQuickReplies(prompt, replies, onSelect) {
        if (!replies.length) {
            return;
        }

        const group = document.createElement("div");
        group.className = "support-chat-quick-group";

        const promptBubble = document.createElement("div");
        promptBubble.className = "support-chat-message support-chat-message-bot";
        promptBubble.textContent = prompt;
        group.appendChild(promptBubble);

        const chips = document.createElement("div");
        chips.className = "support-chat-quick-replies";
        replies.forEach((reply) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "support-chat-chip";
            button.textContent = reply.meta ? `${reply.label} (${reply.meta})` : reply.label;
            button.addEventListener("click", () => {
                chips.querySelectorAll("button").forEach((chip) => {
                    chip.disabled = true;
                });
                onSelect(reply);
            });
            chips.appendChild(button);
        });
        group.appendChild(chips);
        messages.appendChild(group);
        messages.scrollTop = messages.scrollHeight;
    }

    function resetConversationMetadata() {
        activeOption = null;
        categoryInput.value = "";
        issueInput.value = "";
        invoiceIdInput.value = "";
        referenceInput.value = "";
        messageInput.placeholder = "Write a message...";
    }

    function showTopicOptions(prompt) {
        appendQuickReplies(prompt, chatOptions.options || [], (option) => {
            activeOption = option;
            categoryInput.value = option.category || "";
            issueInput.value = option.label || "";
            referenceInput.value = "";
            appendMessage(option.label, "user");

            const references = option.referenceKind ? chatOptions.references || [] : [];
            if (references.length) {
                appendQuickReplies(option.prompt || "Which record is this about?", references, (reference) => {
                    invoiceIdInput.value = reference.id || "";
                    referenceInput.value = reference.value || "";
                    appendMessage(reference.label, "user");
                    appendMessage(option.detailPrompt || "Tell us more about the issue.", "bot");
                    messageInput.placeholder = option.detailPrompt || "Write a message...";
                    messageInput.focus();
                });
                return;
            }

            const noReferencePrompt = option.referenceKind === "invoice"
                ? "I could not find a linked invoice to attach here. Open the invoice from My Invoices and use Ask About This Invoice."
                : option.referenceKind
                    ? "I could not find linked records to show here."
                    : option.prompt || "Tell us more about the issue.";
            appendMessage(noReferencePrompt, "bot");
            messageInput.placeholder = option.detailPrompt || "Write a message...";
            messageInput.focus();
        });
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

    messageInput.addEventListener("input", () => {
        messageInput.style.height = "auto";
        messageInput.style.height = `${Math.min(messageInput.scrollHeight, 92)}px`;
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
            messageInput.style.height = "";
            resetConversationMetadata();
            showTopicOptions("Need help with anything else?");
            messageInput.focus();
        } catch (error) {
            appendMessage("The request could not be sent right now. Please try again in a moment.", "bot");
        } finally {
            submitButton.disabled = false;
            submitButton.textContent = "Send";
        }
    });

    resetConversationMetadata();
    showTopicOptions("Choose a topic so we can route this correctly.");
})();
